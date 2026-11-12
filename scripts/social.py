#!/usr/bin/env python3
"""Единая обёртка поиска по соцсетям.

search(source, query, since=None, lang=None, limit=25, tag="adhoc") -> (records, meta)
record = {source, platform, kind (post|comment|channel), id, url, author_hash, date, text, metrics, lang, …подсказки}
  author_hash — хэш «платформа:стабильный id» (id канала YouTube, author.id X у twitterapi, id LinkedIn, ник Reddit/HN);
  пусто для [deleted] (opinions.py считает такой пост отдельным автором). Подсказки поставщика: sc_rel, sc_seller,
  sc_intent, subreddit, is_reply/reply_to/parent, thread/thread_title (у комментариев), author_name (X).
meta   = {source, status, elapsed_s, n, credits, credits_unit, usd, balance_after, raw_file, error}

Источники (source):
  sc_x sc_reddit sc_youtube sc_youtube_adv sc_bluesky sc_linkedin sc_telegram sc_everywhere        — SocialCrawl
  (sc_hn, sc_threads — broken; комментарии веток — sc_comments(), тред HN — hn_thread(), статья X — tw_article():
   их зовёт scripts/expand.py)
  unif_x unif_reddit unif_hn unif_threads unif_linkedin unif_youtube                            — UnifAPI
  tw_x                                                                                          — twitterapi.io
  hn                                                                                            — HN Algolia (бесплатно)

Сырые ответы — data/raw/<tag>/ (не в git). Каждый вызов — строка в data/ledger.jsonl (кредиты, баланс, время).
Ключи читаются из ~/.config/sergd-social/*.env и нигде не печатаются.

CLI: python3 scripts/social.py SOURCE "запрос" [--since YYYY-MM-DD] [--lang ru] [--limit 25] [--tag probe]
"""
import argparse, json, re, sys, time, datetime as dt, html as _html
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _keys import load_key  # noqa: E402
from _http import get_json, author_hash, author_key, slug, RAW, ROOT  # noqa: E402

LEDGER = ROOT / "data" / "ledger.jsonl"

# $ за единицу кредита (по ценам покупки/документации)
USD_PER_CREDIT = {
    "socialcrawl": 20.57 / 2600,      # ≈ 0,0079 $ за кредит (прайс пакета SocialCrawl)
    "unifapi": 0.001,                 # 1 кредит = $0,001 (docs)
    "twitterapi": 1 / 100_000,        # 100 000 кредитов = $1 (docs)
    "hn_algolia": 0.0,
}

SC_BASE = "https://www.socialcrawl.dev/v1/"
UNIF_BASE = "https://api.unifapi.com"
TW_BASE = "https://api.twitterapi.io"

_tw_last_call = [0.0]
TW_MIN_INTERVAL = 5.2  # бесплатный уровень twitterapi.io: 1 запрос в 5 с (docs rate-limits)


# ---------- общее ----------

def _iso(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return dt.datetime.fromtimestamp(v, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = str(v)
    for fmt in ("%a %b %d %H:%M:%S %z %Y",):  # формат X: Wed Jan 03 23:28:54 +0000 2024
        try:
            return dt.datetime.strptime(s, fmt).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return s


def _since_epoch(since):
    if not since:
        return None
    return int(dt.datetime.strptime(since[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())


def _ledger(meta, query, params):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "query": query,
           **{k: meta.get(k) for k in ("source", "tag", "status", "n", "n_before_since", "credits", "credits_unit", "usd",
                                        "balance_after", "elapsed_s", "raw_file", "error")},
           "params": {k: v for k, v in params.items() if k not in ("query", "q")}}
    with LEDGER.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------- SocialCrawl ----------

SC_EP = {
    "sc_x": ("twitter", "twitter/search/tweets"),
    "sc_reddit": ("reddit", "reddit/search"),
    "sc_hn": ("hackernews", "hackernews/search"),
    "sc_threads": ("threads", "threads/search"),
    "sc_youtube": ("youtube", "youtube/search"),
    "sc_youtube_adv": ("youtube", "youtube/search/advanced"),
    "sc_bluesky": ("bluesky", "bluesky/search"),
    "sc_linkedin": ("linkedin", "linkedin/search/posts"),
    "sc_telegram": ("telegram", "telegram/search"),
    "sc_everywhere": ("multi", "search/everywhere"),
}
DELETED = {"[deleted]", "[removed]"}  # текст удалённого комментария


def _sc_params(source, query, since, lang, limit, sort, context=None):
    p = {"query": query}
    if source == "sc_x":
        # since= всегда включает latest (sort=top с since — бесплатный 400): для top окно уходит в запрос since_time:;
        # нарезка по месяцам (run_plan) сама пишет since_time:/until_time: в запрос
        inq = "since_time:" in query
        if sort == "top" and since and not inq:
            p["query"] = f"{query} since_time:{_since_epoch(since)}"
            inq = True
        p.update(sort=sort or "latest", since=None if inq else since, max_items=limit if limit > 20 else None,
                 max_credits=max(1, -(-limit // 20)) if limit > 20 else None)
    elif source == "sc_reddit":
        srt = {"latest": "new"}.get(sort or "", sort or "relevance")
        p.update(sort=srt, timeframe=_unif_timeframe(since) if srt != "new" else None,
                 since=since if srt == "new" else None,  # API: since требует sort=new; иначе фильтруем у себя
                 limit=limit if limit > 25 else None)
    elif source == "sc_hn":
        p.update(since=since, max_items=limit if limit > 20 else None,
                 max_credits=max(1, -(-limit // 20)) if limit > 20 else None)
    elif source == "sc_threads":
        pass  # limit/start_date дают ложное no_matches (наша проба); дату фильтруем у себя
    elif source == "sc_youtube":
        p.update(since=since)
    elif source == "sc_youtube_adv":  # число комментариев и просмотров — бесплатно
        # окно дат (published_after = since) API принимает только с order=date (иначе 400) → при relevance
        # окно не шлём, дату фильтрует search() у себя
        latest = sort == "latest"
        p.update(order="date" if latest else "relevance", language=lang, max_results=50 if not latest else min(max(limit, 1), 50),
                 published_after=f"{since[:10]}T00:00:00Z" if since and latest else None)
    elif source == "sc_bluesky":
        p.update(since=since, lang=lang, sort=sort or "latest")
    elif source == "sc_linkedin":
        p.update(limit=min(limit, 200), sort_by="date_posted" if sort == "latest" else None)
    elif source == "sc_telegram":
        p.update(limit=min(limit, 10))
    elif source == "sc_everywhere":
        # context — о чём должны быть строки (вопрос), relevance=filter — мусор уходит, цена та же
        p.update(from_date=since, limit=min(limit, 100), deadline_s=60, context=(context or "")[:2000] or None,
                 relevance="filter" if context else None)
    return p


_akey = author_key  # единая схема «платформа:id» — _http.author_key (её же берут плагины scripts/sources/*)
REDDIT_C = re.compile(r"^https?://(?:www\.|old\.)?reddit\.com/r/([^/]+)/comments/([a-z0-9]+)/(?:comment/|[^/]+/)([a-z0-9]+)/?(?:[?#].*)?$")


def reddit_comment_url(url):
    """Ссылка на комментарий Reddit — один вид для всех методов: …/r/<sub>/comments/<пост>/comment/<id>/
    (prism/comments отдаёт то /comment/<id>/, то /<slug>/<id>/; top_comments everywhere — /<slug>/<id>/). → (url, id)."""
    m = REDDIT_C.match(url or "")
    if not m:
        return url, None
    return f"https://www.reddit.com/r/{m.group(1)}/comments/{m.group(2)}/comment/{m.group(3)}/", m.group(3)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _sc_norm(source, platform, it):
    if "post" not in it and isinstance(it.get("author"), dict) and it["author"].get("url"):  # telegram/search: каналы
        a = it["author"]
        hit = (a.get("ext") or {}).get("search_hit") or {}
        return {"source": source, "platform": platform, "id": a.get("username") or a.get("id"), "url": a["url"],
                "author_hash": _akey("telegram", a.get("username")), "date": None, "kind": "channel",
                "text": f"{hit.get('title') or ''}\n{hit.get('snippet') or a.get('bio') or ''}".strip(),
                "metrics": {"followers": a.get("followers")} if a.get("followers") else {}, "lang": None}
    post = it.get("post", it) if isinstance(it, dict) else {}
    comp = it.get("computed", {}) if isinstance(it, dict) else {}
    content = post.get("content") or {}
    ext = post.get("ext") or {}
    author = post.get("author") or {}
    if not isinstance(author, dict):
        author = {"username": author}
    plat = post.get("platform") or it.get("platform") or it.get("source") or platform
    text = content.get("text") or post.get("text") or ""
    art = ext.get("article") if isinstance(ext.get("article"), dict) else {}
    title = ext.get("title") or art.get("title")  # LinkedIn: статья — ext.article.title
    if title and title not in text:
        text = f"{title}\n{text}"
    eng = post.get("engagement") or {}
    labels = comp.get("labels") or {}
    intent = labels.get("intent") or {}
    user = author.get("username")
    sid = ext.get("channel_id") or ext.get("author_channel_id") or ext.get("author_id") or ext.get("author_urn")
    rec = {
        "source": source, "platform": plat, "kind": "post", "id": str(post.get("id", "")),
        "url": post.get("url") or it.get("url"),
        "author_hash": _akey(plat, sid, user),
        "date": _iso(post.get("published_at") or it.get("published_at")),
        "text": text.strip(),
        "metrics": {k: eng.get(k) for k in ("likes", "comments", "shares", "views") if eng.get(k) is not None},
        "lang": comp.get("language") or ext.get("content_language"),
        "sc_rel": (comp.get("relevance") or {}).get("p"),
        "sc_intent": intent.get("label"),
        "sc_seller": _num(intent.get("seller")),
    }
    if plat == "twitter":
        rec["author_name"] = f"{author.get('display_name') or ''} (@{user})".strip() if user else author.get("display_name")
        rec["x_handle_hash"] = rec["author_hash"]
        rec["followers"] = ext.get("author_followers")
        rec["verified"] = author.get("verified")
        if ext.get("in_reply_to_id"):
            rec["is_reply"] = True
            rec["reply_to"] = f"https://x.com/i/status/{ext['in_reply_to_id']}"
            rec["parent"] = rec["reply_to"]
        rec["conversation_id"] = ext.get("conversation_id")
        if ext.get("quote_count"):
            rec["metrics"]["quotes"] = ext["quote_count"]
    elif plat == "reddit":
        rec["title"] = ext.get("title")  # заголовок ветки — для «[тред: …]» у комментариев (expand.py)
        rec["subreddit"] = ext.get("subreddit")
        rec["flair"] = ext.get("flair")
    elif plat == "linkedin":
        rec["author_role"] = ext.get("author_headline")
        rec["kind_sc"] = ext.get("type")
    elif plat == "youtube":
        rec["kind_sc"] = ext.get("content_type")
    return rec


def _sc_comment(source, platform, it, thread_url, thread_title, parents):
    """Строка комментария SocialCrawl (prism/comments, youtube/video/comments, top_comments) → запись kind=comment.
    parents: id → (url, text) уже разобранных комментариев ветки (для «[в ответ на: …]»)."""
    c = it.get("comment") or {}
    ext = c.get("ext") or {}
    a = c.get("author") or {}
    text = (c.get("text") or ext.get("text_original") or "").strip()
    if not text or text in DELETED:
        return None
    cid = str(c.get("id") or "")
    url = reddit_comment_url(c.get("url"))[0] if platform == "reddit" else c.get("url")
    if not url and platform == "youtube" and c.get("post_id") and cid:
        url = f"https://www.youtube.com/watch?v={c['post_id']}&lc={cid}"
    if not url:
        return None
    par = parents.get(str(c.get("parent_id") or ""))
    head = f"[тред: {thread_title}]" if thread_title else ""
    if par:
        head += f"\n[в ответ на: {par[1][:160]}{'…' if len(par[1]) > 160 else ''}]"
    eng = c.get("engagement") or {}
    labels = (it.get("computed") or {}).get("labels") or {}
    rec = {"source": source, "platform": platform, "kind": "comment", "id": cid, "url": url,
           "author_hash": _akey(platform, ext.get("author_channel_id") or ext.get("author_id"), a.get("username")),
           "date": _iso(c.get("published_at")), "text": (head + "\n" + text).strip(),
           "metrics": {k: v for k, v in {"likes": eng.get("likes"), "comments": eng.get("replies")}.items() if v is not None},
           "lang": (it.get("computed") or {}).get("language"),
           "parent": par[0] if par else thread_url, "thread": thread_url, "thread_title": thread_title,
           "depth": ext.get("depth"), "sc_complaint": ((labels.get("complaint") or {}).get("p"))}
    parents[cid] = (url, text)
    return rec


def _flatten_comments(items):
    """prism/comments: дерево (replies вложены) → плоский список в порядке обхода (родитель раньше ответа)."""
    out = []
    stack = list(reversed(items or []))
    while stack:
        it = stack.pop()
        if not isinstance(it, dict):
            continue
        out.append(it)
        stack.extend(reversed(it.get("replies") or []))
    return out


def _ev_row(source, si, it):
    """search/everywhere: строка source_items (или items_by_source) → запись. it — ранжированная строка (есть оценка)."""
    md = si.get("metadata") or {}
    eng = si.get("engagement") if isinstance(si.get("engagement"), dict) else {}
    plat = si.get("source") or (it or {}).get("source")
    text = "\n".join(x for x in (si.get("title") or (it or {}).get("title"), si.get("body") or (it or {}).get("snippet")) if x)
    comp = (it or {}).get("computed") or {}
    rel = comp.get("relevance") or {}
    handle = md.get("author_username") or si.get("author")
    xid = ("id:" + str(md["author_id"])) if plat == "twitter" and md.get("author_id") else md.get("author_id")
    extra = {"x_handle_hash": _akey("twitter", handle), "x_has_id": bool(xid)} if plat == "twitter" else {}
    return {"source": source, "platform": plat, "kind": "post", "id": str(si.get("native_id") or ""),
            "url": (it or {}).get("url") or si.get("url"), **extra,
            "author_hash": _akey(plat, xid, handle),
            "date": _iso(si.get("published_at")), "text": text.strip(),
            "metrics": {k: v for k, v in eng.items() if v is not None and not isinstance(v, (dict, list))}, "lang": si.get("language"),
            "sc_rel": rel.get("score") if rel.get("score") is not None else rel.get("p"),
            "sc_stance": (comp.get("stance") or {}).get("label"),
            "date_confidence": si.get("date_confidence") if si.get("date_confidence") == "low" else None,
            "sc_unfiltered": it is None}


def _sc_everywhere_records(source, data):
    """items (прошли фильтр, есть оценка) + строки items_by_source, которых нет в items (половина выдачи; фильтр
    relevance их не трогает — решит разметчик) + топ-комментарии Reddit отдельными записями."""
    recs, seen = [], set()
    pairs = [((it.get("source_items") or [{}])[0], it) for it in data.get("items") or [] if isinstance(it, dict)]
    for rows in (data.get("items_by_source") or {}).values():
        pairs += [(si, None) for si in rows or [] if isinstance(si, dict)]
    for si, it in pairs:
        r = _ev_row(source, si, it)
        if not r["url"] or r["url"] in seen:
            continue
        seen.add(r["url"])
        recs.append(r)
        title = (si.get("title") or "")[:200]
        for tc in (si.get("metadata") or {}).get("top_comments") or []:
            if not isinstance(tc, dict) or not tc.get("url") or not tc.get("excerpt"):
                continue
            cu, cid = reddit_comment_url(tc["url"])
            if cu in seen:
                continue
            seen.add(cu)
            recs.append({"source": source, "platform": r["platform"], "kind": "comment", "id": cid or cu, "url": cu,
                         "author_hash": _akey(r["platform"], tc.get("author")), "date": _iso(tc.get("date")),
                         "text": (f"[тред: {title}]\n" if title else "") + tc["excerpt"] + (" …" if tc.get("truncated") else ""),
                         "metrics": {"likes": tc.get("score")} if tc.get("score") is not None else {}, "lang": None,
                         "parent": r["url"], "thread": r["url"], "thread_title": title})
    return recs


def _sc_call(ep, params, tag, raw_tag, timeout=120):
    key = load_key("socialcrawl", "SOCIALCRAWL_API_KEY")
    st, body, el = get_json(SC_BASE + ep, params, {"x-api-key": key}, timeout=timeout, raw_tag=raw_tag, raw_dir=tag)
    credits = body.get("credits_used") if isinstance(body, dict) else None
    meta = {"status": st, "elapsed_s": el, "credits": credits, "credits_unit": "socialcrawl",
            "usd": round((credits or 0) * USD_PER_CREDIT["socialcrawl"], 5),
            "balance_after": body.get("credits_remaining") if isinstance(body, dict) else None,
            "cached": body.get("cached") if isinstance(body, dict) else None, "raw_file": _last_raw(tag),
            "error": None if st == 200 else (body.get("error") if isinstance(body, dict) else body)}
    return body, meta


def sc_search(source, query, since=None, lang=None, limit=25, tag="adhoc", sort=None, context=None):
    platform, ep = SC_EP[source]
    params = _sc_params(source, query, since, lang, limit, sort, context)
    body, meta = _sc_call(ep, params, tag, f"{source}_{slug(query)}")
    data = body.get("data") if isinstance(body, dict) else None
    items = []
    if isinstance(data, dict):
        items = data.get("items") or data.get("results") or []
    elif isinstance(data, list):
        items = data
    if source == "sc_everywhere" and isinstance(data, dict):
        recs = _sc_everywhere_records(source, data)
    else:
        recs = [_sc_norm(source, platform, it) for it in items if isinstance(it, dict)]
    meta.update(source=source, tag=tag, n=len(recs))
    return recs, meta


def sc_comments(url, platform, thread_title="", tag="adhoc", per_thread=60, since=None):
    """Комментарии ветки: Reddit/HN — prism/comments (вся ветка, ≈ 2 кр), YouTube — youtube/video/comments
    (1 кр за страницу до 100, order=top). Возвращает (records, meta); записи kind=comment, source=sc_comments."""
    if platform == "youtube":
        body, meta = _sc_call("youtube/video/comments", {"url": url, "format": "plainText", "order": "top"},
                              tag, f"sc_ytc_{slug(url, 60)}")
        items = ((body or {}).get("data") or {}).get("items") or [] if isinstance(body, dict) else []
        src = "sc_yt_comments"
    else:
        body, meta = _sc_call("prism/comments", {"url": url, "max": 300}, tag, f"sc_thread_{slug(url, 60)}", timeout=180)
        items = _flatten_comments(((body or {}).get("data") or {}).get("comments") or []) if isinstance(body, dict) else []
        src = "sc_comments"
    parents, recs = {}, []
    for it in items:
        r = _sc_comment(src, platform, it, url, thread_title, parents)
        if r:
            recs.append(r)
    if since:
        recs = [r for r in recs if not r["date"] or r["date"][:10] >= since[:10]]
    if len(recs) > per_thread:  # длинная ветка: самые поддержанные, порядок ветки сохраняется
        keep = {id(r) for r in sorted(recs, key=lambda r: -(r["metrics"].get("likes") or 0))[:per_thread]}
        recs = [r for r in recs if id(r) in keep]
    meta.update(source=src, tag=tag, n=len(recs))
    _ledger(meta, url, {"kind": "comments", "platform": platform})
    return recs, meta


# ---------- UnifAPI ----------

def _unif_timeframe(since):
    if not since:
        return "all"
    days = (dt.datetime.now(dt.timezone.utc).timestamp() - _since_epoch(since)) / 86400
    return "day" if days <= 1 else "week" if days <= 7 else "month" if days <= 31 else "year" if days <= 366 else "all"


UNIF_EP = {
    "unif_x": ("twitter", "/x/tweets/search/recent"),
    "unif_reddit": ("reddit", "/reddit/search/posts"),
    "unif_hn": ("hackernews", "/hacker-news/search"),
    "unif_threads": ("threads", "/threads/search/recent"),
    "unif_linkedin": ("linkedin", "/linkedin/search/posts"),
    "unif_youtube": ("youtube", "/youtube/search"),
}


def _unif_params(source, query, since, lang, limit, sort):
    tf = _unif_timeframe(since)
    if source == "unif_x":
        q = query + (f" lang:{lang}" if lang and "lang:" not in query else "")
        return {"query": q, "max_results": max(10, min(limit, 100))}
    if source == "unif_reddit":
        sb = {"latest": "new", "top": "top"}.get(sort or "", "relevance")
        return {"query": query, "sort_by": sb, "timeframe": tf}
    if source == "unif_hn":
        dr = {"day": "24h", "week": "week", "month": "month", "year": "year"}.get(tf, "all")
        lim = min([x for x in (10, 20, 30, 50) if x >= min(limit, 50)] or [50])
        return {"query": query, "sort": "date" if sort == "latest" else "popularity", "date_range": dr, "limit": lim}
    if source == "unif_threads":
        return {"q": query}
    if source == "unif_linkedin":
        dp = {"day": "last-day", "week": "last-week", "month": "last-month", "year": "last-year"}.get(tf)
        return {"query": query, "date_posted": dp}
    if source == "unif_youtube":
        ud = {"day": "today", "week": "week", "month": "month", "year": "year"}.get(tf)
        return {"q": query, "upload_date": ud, "language": lang, "sort_by": "date" if sort == "latest" else None}
    raise KeyError(source)


def _first(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return None


def _unif_norm(source, platform, it):
    author = it.get("author") or it.get("user") or it.get("channel") or {}
    handle = author if isinstance(author, str) else _first(author, "username", "handle", "screen_name", "name", "id")
    text = _first(it, "text", "body_text", "content", "description", "caption") or ""
    title = it.get("title")
    if title and title not in text:
        text = f"{title}\n{text}"
    url = _first(it, "permalink", "item_url", "url", "link")
    if not url and source == "unif_x" and it.get("id"):
        url = f"https://x.com/{handle or 'i'}/status/{it['id']}" if handle else f"https://x.com/i/status/{it['id']}"
    met = it.get("public_metrics") or it.get("metrics") or {}
    metrics = {k: v for k, v in {
        "likes": _first(it, "score", "like_count", "likes") or _first(met, "like_count", "likes"),
        "comments": _first(it, "comment_count", "reply_count", "comments") or _first(met, "reply_count"),
        "views": _first(it, "view_count", "views") or _first(met, "impression_count", "view_count"),
    }.items() if v is not None}
    return {"source": source, "platform": platform, "id": str(it.get("id", "")), "url": url,
            "author_hash": _akey(platform, handle), "date": _iso(_first(it, "created_at", "published_at", "date")),
            "text": str(text).strip(), "metrics": metrics, "lang": it.get("lang") or it.get("language")}


def unif_search(source, query, since=None, lang=None, limit=25, tag="adhoc", sort=None):
    platform, ep = UNIF_EP[source]
    key = load_key("unifapi", "UNIFAPI_API_KEY")
    params = _unif_params(source, query, since, lang, limit, sort)
    st, body, el = get_json(UNIF_BASE + ep, params, {"Authorization": "Bearer " + key}, timeout=120,
                            raw_tag=f"{source}_{slug(query)}", raw_dir=tag)
    data = body.get("data") if isinstance(body, dict) else None
    items = data if isinstance(data, list) else (data or {}).get("items") or (data or {}).get("posts") or (data or {}).get("tweets") or [] if isinstance(data, dict) else []
    recs = [_unif_norm(source, platform, it) for it in items if isinstance(it, dict)]
    if since:
        cut = _iso(_since_epoch(since))
        recs = [r for r in recs if not r["date"] or r["date"] >= cut]
    bill = (body or {}).get("billing") or {} if isinstance(body, dict) else {}
    credits = bill.get("credits_charged")
    meta = {"source": source, "tag": tag, "status": st, "elapsed_s": el, "n": len(recs), "credits": credits,
            "credits_unit": "unifapi", "usd": round((credits or 0) * USD_PER_CREDIT["unifapi"], 5),
            "balance_after": bill.get("balance_remaining"), "raw_file": _last_raw(tag),
            "error": None if st == 200 else (body.get("error") if isinstance(body, dict) else body)}
    return recs, meta


# ---------- twitterapi.io ----------

_LEAD_MENTIONS = re.compile(r"^(?:@\w+\s+)+")  # формат: ведущие @упоминания ответа (displayTextRange), не смысл


def _tw_text(it, depth=0):
    """Текст твита для разметчика: t.co → полные ссылки (entities.urls), статья (заголовок + превью), цитата."""
    t = it.get("text") or ""
    for u in (it.get("entities") or {}).get("urls") or []:
        if isinstance(u, dict) and u.get("url") and u.get("expanded_url"):
            t = t.replace(u["url"], u["expanded_url"])
    if it.get("isReply"):
        t = _LEAD_MENTIONS.sub("", t)
    art = it.get("article")
    if isinstance(art, dict) and (art.get("title") or art.get("preview_text")):
        t += f"\n[статья X: {art.get('title') or ''}]\n{art.get('preview_text') or ''}"
    qt = it.get("quoted_tweet")
    if depth == 0 and isinstance(qt, dict) and (qt.get("text") or qt.get("article")):
        q = _tw_text(qt, 1)
        t += f"\n[цитирует: {q[:800]}{'…' if len(q) > 800 else ''}]"
    return t.strip()


def _tw_norm(it):
    a = it.get("author") or {}
    rec = {"source": "tw_x", "platform": "twitter", "kind": "post", "id": str(it.get("id", "")),
           "url": it.get("url") or f"https://x.com/i/status/{it.get('id')}",
           "author_hash": _akey("twitter", ("id:" + str(a["id"])) if a.get("id") else None, a.get("userName")),
           "x_handle_hash": _akey("twitter", a.get("userName")), "x_has_id": bool(a.get("id")),
           "author_name": f"{a.get('name') or ''} (@{a.get('userName')})" if a.get("userName") else a.get("name"),
           "date": _iso(it.get("createdAt")), "text": _tw_text(it),
           "metrics": {k: v for k, v in {"likes": it.get("likeCount"), "comments": it.get("replyCount"),
                                         "shares": it.get("retweetCount"), "views": it.get("viewCount"),
                                         "quotes": it.get("quoteCount") or None}.items() if v is not None},
           "lang": it.get("lang"), "followers": a.get("followers"), "verified": a.get("isBlueVerified") or None,
           "conversation_id": it.get("conversationId")}
    if it.get("isReply") and it.get("inReplyToId"):
        rec.update(is_reply=True, reply_to=f"https://x.com/i/status/{it['inReplyToId']}",
                   parent=f"https://x.com/i/status/{it['inReplyToId']}")
    return rec


def _tw_wait():
    wait = TW_MIN_INTERVAL - (time.time() - _tw_last_call[0])
    if wait > 0:
        time.sleep(wait)


def tw_search(source, query, since=None, lang=None, limit=20, tag="adhoc", sort=None):
    key = load_key("twitterapi", "TWITTERAPI_IO_API_KEY")
    q = query
    if since and "since_time:" not in q:
        q += f" since_time:{_since_epoch(since)}"
    if lang and "lang:" not in q:
        q += f" lang:{lang}"
    recs, cursor, pages, total_el, st, err, raw_files, credits = [], "", 0, 0.0, None, None, [], 0
    while len(recs) < limit and pages < 10:
        _tw_wait()
        st, body, el = get_json(TW_BASE + "/twitter/tweet/advanced_search",
                                {"query": q, "queryType": "Top" if sort == "top" else "Latest", "cursor": cursor},
                                {"X-API-Key": key}, timeout=90, raw_tag=f"tw_x_{slug(query)}_p{pages}", raw_dir=tag)
        _tw_last_call[0] = time.time()
        total_el += el
        pages += 1
        raw_files.append(_last_raw(tag))
        if st != 200 or not isinstance(body, dict):
            err = body
            break
        tweets = body.get("tweets") or []
        credits += max(15, 15 * len(tweets))  # 15 кредитов за твит, минимум 15 за запрос (docs pricing)
        recs += [_tw_norm(t) for t in tweets]
        nxt = body.get("next_cursor") or ""
        # неполная страница или тот же курсор — дальше пусто, не платить ещё 15 кр
        if not body.get("has_next_page") or len(tweets) < 15 or not nxt or nxt == cursor:
            break
        cursor = nxt
    meta = {"source": "tw_x", "tag": tag, "status": st, "elapsed_s": round(total_el, 2), "n": len(recs),
            "credits": credits, "credits_unit": "twitterapi (оценка по прайсу)",
            "usd": round(credits * USD_PER_CREDIT["twitterapi"], 5), "balance_after": None,
            "raw_file": raw_files[0] if raw_files else None, "pages": pages, "error": err}
    return recs, meta


TW_ARTICLE_CREDITS = 100  # /twitter/article — 100 кр за статью


def tw_article(tweet_id, tag="adhoc"):
    """Полный текст статьи X по id твита. Возвращает (text | None, meta)."""
    key = load_key("twitterapi", "TWITTERAPI_IO_API_KEY")
    _tw_wait()
    st, body, el = get_json(TW_BASE + "/twitter/article", {"tweet_id": tweet_id}, {"X-API-Key": key}, timeout=90,
                            raw_tag=f"tw_article_{tweet_id}", raw_dir=tag)
    _tw_last_call[0] = time.time()
    art = body.get("article") if isinstance(body, dict) else None
    text = None
    if isinstance(art, dict):
        blocks = [b.get("text") for b in art.get("contents") or [] if isinstance(b, dict) and b.get("text")]
        text = "\n".join(x for x in [f"[статья X: {art.get('title') or ''}]", *blocks] if x).strip() or None
    meta = {"source": "tw_article", "tag": tag, "status": st, "elapsed_s": el, "n": 1 if text else 0,
            "credits": TW_ARTICLE_CREDITS if st == 200 else 0, "credits_unit": "twitterapi (оценка по прайсу)",
            "usd": round((TW_ARTICLE_CREDITS if st == 200 else 0) * USD_PER_CREDIT["twitterapi"], 5),
            "balance_after": None, "raw_file": _last_raw(tag), "error": None if st == 200 and text else (body if st != 200 else "нет текста")}
    _ledger(meta, f"article {tweet_id}", {})
    return text, meta


# ---------- HN Algolia ----------

def _hn_clean(h):
    """HTML Algolia → текст (формат): <p> → абзац, <a href> → адрес, сущности раскодировать."""
    if not h:
        return ""
    t = re.sub(r"<a\s[^>]*href=\"([^\"]+)\"[^>]*>.*?</a>", lambda m: _html.unescape(m.group(1)), h, flags=re.S | re.I)
    t = re.sub(r"<p>|<br\s*/?>", "\n", t, flags=re.I)
    t = _html.unescape(re.sub(r"<[^>]+>", "", t))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _hn_rec(source, h, title_key="title"):
    oid = str(h.get("objectID") or h.get("id"))
    title = h.get(title_key) or ""
    body_t = _hn_clean(h.get("comment_text") or h.get("story_text") or h.get("text") or "")
    is_comment = bool(h.get("comment_text")) or h.get("type") == "comment" or "comment" in (h.get("_tags") or [])
    story_title = h.get("story_title") or ""
    if is_comment:
        text = (f"[тред: {story_title}]\n" if story_title else "") + body_t
    else:
        text = title + ("\n" if title and body_t else "") + body_t
    rec = {"source": source, "platform": "hackernews", "kind": "comment" if is_comment else "post", "id": oid,
           "url": f"https://news.ycombinator.com/item?id={oid}",
           "author_hash": _akey("hackernews", h.get("author")), "date": h.get("created_at"), "text": text.strip(),
           "metrics": {k: v for k, v in {"likes": h.get("points"), "comments": h.get("num_comments")}.items() if v is not None},
           "lang": None}
    if is_comment:
        sid = h.get("story_id")
        rec.update(thread=f"https://news.ycombinator.com/item?id={sid}" if sid else None, thread_title=story_title,
                   parent=f"https://news.ycombinator.com/item?id={h['parent_id']}" if h.get("parent_id") else None,
                   story_id=sid)
    return rec


def hn_search(source, query, since=None, lang=None, limit=50, tag="adhoc", sort=None):
    ep = "search_by_date" if sort == "latest" else "search"
    # typoTolerance=false и prefixNone: без «it's/its» на запрос TTS; посты и комментарии вместе —
    # посты с числом комментариев нужны expand.py для выбора тредов
    params = {"query": query, "tags": "(story,comment)", "hitsPerPage": min(limit, 1000), "typoTolerance": "false",
              "queryType": "prefixNone"}
    if since:
        params["numericFilters"] = f"created_at_i>{_since_epoch(since)}"
    st, body, el = get_json(f"https://hn.algolia.com/api/v1/{ep}", params, {}, raw_tag=f"hn_{slug(query)}", raw_dir=tag)
    recs = [_hn_rec("hn", h) for h in ((body or {}).get("hits", []) if isinstance(body, dict) else [])]
    meta = {"source": "hn", "tag": tag, "status": st, "elapsed_s": el, "n": len(recs), "credits": 0,
            "credits_unit": "free", "usd": 0.0, "balance_after": None, "raw_file": _last_raw(tag),
            "error": None if st == 200 else body}
    return recs, meta


def hn_thread(story_id, tag="adhoc", per_thread=60, since=None):
    """Тред HN целиком (Algolia items/<id>, бесплатно) → комментарии kind=comment с «[тред: …]» и родителем.
    Обход в ширину: сначала верхний уровень (у комментариев HN нет очков), до per_thread штук."""
    st, body, el = get_json(f"https://hn.algolia.com/api/v1/items/{story_id}", {}, {},
                            raw_tag=f"hn_item_{story_id}", raw_dir=tag)
    recs = []
    if st == 200 and isinstance(body, dict):
        title = body.get("title") or ""
        texts = {}
        queue = [(c, body.get("id")) for c in body.get("children") or []]
        while queue and len(recs) < per_thread:
            c, par = queue.pop(0)
            queue += [(x, c.get("id")) for x in c.get("children") or []]
            t = _hn_clean(c.get("text") or "")
            if c.get("type") != "comment" or not t or not c.get("author"):
                continue
            ptxt = texts.get(par)
            head = f"[тред: {title}]" + (f"\n[в ответ на: {ptxt[:160]}{'…' if len(ptxt) > 160 else ''}]" if ptxt else "")
            texts[c.get("id")] = t
            if since and (c.get("created_at") or "")[:10] < since[:10]:
                continue
            recs.append({"source": "hn_thread", "platform": "hackernews", "kind": "comment", "id": str(c["id"]),
                         "url": f"https://news.ycombinator.com/item?id={c['id']}",
                         "author_hash": _akey("hackernews", c.get("author")), "date": c.get("created_at"),
                         "text": head + "\n" + t, "metrics": {}, "lang": None,
                         "parent": f"https://news.ycombinator.com/item?id={par}",
                         "thread": f"https://news.ycombinator.com/item?id={story_id}", "thread_title": title})
    meta = {"source": "hn_thread", "tag": tag, "status": st, "elapsed_s": el, "n": len(recs), "credits": 0,
            "credits_unit": "free", "usd": 0.0, "balance_after": None, "raw_file": _last_raw(tag),
            "error": None if st == 200 else body}
    _ledger(meta, f"items/{story_id}", {})
    return recs, meta


# ---------- диспетчер ----------

def _last_raw(tag):
    d = RAW / tag
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime) if d.exists() else []
    return str(files[-1].relative_to(ROOT)) if files else None


SOURCES = {**{s: sc_search for s in SC_EP}, **{s: unif_search for s in UNIF_EP}, "tw_x": tw_search, "hn": hn_search}

# Встроенные источники: сервис для бюджета, запас кредитов на вызов, что ищут, языки, статус по нашей проверке
BUILTIN = {
    "sc_x": ("sc", 3, "X/Twitter: посты, операторы X, lang:ru", "ru,en", "ok"),
    "sc_reddit": ("sc", 4, "Reddit: посты (до 1 000 за вызов)", "en", "ok"),
    "sc_everywhere": ("sc", 20, "сводный поиск по 23 площадкам (GitHub, новости, LinkedIn, YouTube…)", "ru,en", "ok"),
    "sc_linkedin": ("sc", 8, "LinkedIn: посты (B2B, маркетинг)", "en", "ok"),
    "sc_bluesky": ("sc", 1, "Bluesky: посты (техника en)", "en", "ok"),
    "sc_hn": ("sc", 3, "Hacker News через SocialCrawl: нет заголовков и текста — брать hn", "en", "broken"),
    "sc_youtube": ("sc", 1, "YouTube: только заголовки роликов, без числа комментариев (лучше sc_youtube_adv)", "ru,en", "ok"),
    "sc_youtube_adv": ("sc", 1, "YouTube: ролики с числом комментариев (для добора комментариев expand.py)", "ru,en", "ok"),
    "sc_telegram": ("sc", 2, "Telegram: поиск КАНАЛОВ (не постов)", "ru", "ok"),
    "sc_threads": ("sc", 2, "Threads: сломан у SocialCrawl (ложное «нет совпадений»), не брать", "en", "broken"),
    "unif_reddit": ("unif", 25, "Reddit: посты (запасной, $1/1 000)", "en", "ok"),
    "unif_linkedin": ("unif", 25, "LinkedIn: посты (запасной)", "en", "ok"),
    "unif_hn": ("unif", 50, "Hacker News (платно; лучше hn)", "en", "ok"),
    "unif_threads": ("unif", 25, "Threads: посты", "en", "untested"),
    "unif_youtube": ("unif", 25, "YouTube", "ru,en", "broken"),
    "unif_x": ("unif", 100, "X/Twitter (отвечал 500)", "ru,en", "broken"),
    "tw_x": ("tw", 615, "X/Twitter (запасной; бесплатный уровень — 1 запрос/5 с)", "ru,en", "ok"),
    "hn": ("hn", 0, "Hacker News Algolia, бесплатно: 1–3 англ. слова, все обязательны, фраза в кавычках", "en", "ok"),
}
try:
    from sources import PLUGINS  # noqa: E402  подключаемые источники: scripts/sources/*.py
except Exception as e:  # сломанный модуль не роняет встроенные источники
    PLUGINS = {}
    print(f"[sources] не загрузились: {type(e).__name__}: {e}", file=sys.stderr)
for _n, _p in PLUGINS.items():
    SOURCES[_n] = _p["fn"]
    USD_PER_CREDIT.setdefault(_p["service"], _p["usd"])


BUILTIN_KEYS = {"sc": ("socialcrawl", "SOCIALCRAWL_API_KEY"), "unif": ("unifapi", "UNIFAPI_API_KEY"),
                "tw": ("twitterapi", "TWITTERAPI_IO_API_KEY")}  # hn — без ключа


def _builtin_key_ok(service):
    if service not in BUILTIN_KEYS:
        return True
    try:
        return bool(load_key(*BUILTIN_KEYS[service]))
    except (OSError, RuntimeError):
        return False


def catalog():
    """Все источники: имя → {service, reserve, about, langs, status, key_ok}."""
    out = {n: {"service": s, "reserve": r, "about": a, "langs": l, "status": st, "key_ok": _builtin_key_ok(s)}
           for n, (s, r, a, l, st) in BUILTIN.items()}
    for n, p in PLUGINS.items():
        out[n] = {k: p[k] for k in ("service", "reserve", "about", "langs", "status", "key_ok")}
    return out


def search(source, query, since=None, lang=None, limit=25, tag="adhoc", sort=None, context=None):
    """context — вопрос исследования; уходит только в sc_everywhere (context= + relevance=filter)."""
    if source not in SOURCES:
        raise KeyError(f"неизвестный источник {source}; есть: {', '.join(SOURCES)}")
    try:
        kw = {"context": context} if context and source == "sc_everywhere" else {}
        recs, meta = SOURCES[source](source, query, since=since, lang=lang, limit=limit, tag=tag, sort=sort, **kw)
    except Exception as e:  # сбой одного источника не роняет прогон
        recs, meta = [], {"source": source, "tag": tag, "status": None, "n": 0, "error": f"{type(e).__name__}: {e}"}
    if since and recs:  # единый фильтр даты у себя (у API он разный или ломается); посты без даты оставляем
        cut = since[:10]
        before = len(recs)
        recs = [r for r in recs if not r.get("date") or r["date"][:10] >= cut]
        meta["n_before_since"], meta["n"] = before, len(recs)
    _ledger(meta, query, {"since": since, "lang": lang, "limit": limit, "sort": sort})
    out = RAW / tag
    out.mkdir(parents=True, exist_ok=True)
    with (out / f"norm_{source}_{slug(query)}.jsonl").open("w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return recs, meta


def main():
    if "--list" in sys.argv:
        for n, c in catalog().items():
            flag = "готов" if c["key_ok"] and c["status"] == "ok" else ("НЕТ КЛЮЧА" if not c["key_ok"] else c["status"])
            print(f"{n:<16} {c['service']:<7} {c['langs']:<6} {flag:<11} запас {c['reserve']:<4} {c['about']}")
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("query")
    ap.add_argument("--since")
    ap.add_argument("--lang")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--tag", default="adhoc")
    ap.add_argument("--sort")
    ap.add_argument("--show", type=int, default=3)
    a = ap.parse_args()
    recs, meta = search(a.source, a.query, a.since, a.lang, a.limit, a.tag, a.sort)
    print(json.dumps(meta, ensure_ascii=False))
    for r in recs[: a.show]:
        print("-", r["date"], r["lang"], r["url"], "|", r["text"][:140].replace("\n", " "))


if __name__ == "__main__":
    main()
