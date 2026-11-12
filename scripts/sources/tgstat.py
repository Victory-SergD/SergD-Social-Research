"""TGStat API Search: posts/search — поиск публикаций по Telegram-каналам и чатам, морфология русского.
Сверено по документации TGStat API (api.tgstat.ru/docs), проверено вживую.
Тариф S: 50 уникальных ключевых слов и 12 000 запросов на 30 дней, глубина 10 дней. ГЛАВНЫЙ РЕСУРС — СЛОВА: каждая новая q
расходует слово (уточнить по usage/stat после первых запросов). Поэтому синонимы — в одну q через расширенный синтаксис
«(a|b) c -d». Ошибки: flood_control_10/60 — пауза и повтор; quota_keywords_reached — стоп."""
import datetime as dt
import time, re, hashlib, html as _html
from _keys import load_key
from _http import get_json, author_key, slug

SERVICE = "tgstat"
KEY_FILE, KEY_VAR = "tgstat.env", "TGSTAT_TOKEN"
USD_PER_CREDIT = 0.35 / 90  # 1 кредит = 1 запрос (тариф S); слова считаются отдельно — usage()
API = "https://api.tgstat.ru"
DEPTH_DAYS = 10  # тариф S


def _clean(html):
    """Текст TGStat приходит с HTML-разметкой: снять теги, раскодировать сущности (формат, не смысл)."""
    t = re.sub(r"<br\s*/?>|</p>", "\n", html, flags=re.I)
    t = _html.unescape(re.sub(r"<[^>]+>", "", t))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def usage():
    st, body, _ = get_json(API + "/usage/stat", {"token": load_key("tgstat", KEY_VAR)}, {})
    return body


def search(source, query, since=None, lang=None, limit=150, tag="adhoc", sort=None):
    key = load_key("tgstat", KEY_VAR)
    now = int(time.time())
    start = max(now - DEPTH_DAYS * 86400 + 600,
                int(dt.datetime.strptime(since[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()) if since else 0)
    recs, offset, calls, el_total, st, err, seen = [], 0, 0, 0.0, None, None, set()
    max_calls = max(3, -(-limit // 50) * 3)  # дубли рассылок отбрасываются — не листать бесконечно
    while len(recs) < limit and offset + 50 <= 1000 and calls < max_calls:
        params = {"token": key, "q": query, "extendedSyntax": 1, "peerType": "all", "hideForwards": 1, "hideDeleted": 1,
                  "startDate": start, "endDate": now, "limit": 50, "offset": offset, "extended": 1}
        st, body, el = get_json(API + "/posts/search", params, {}, raw_tag=f"{source}_{slug(query)}_o{offset}", raw_dir=tag)
        calls += 1
        el_total += el
        if isinstance(body, dict) and str(body.get("error", "")).startswith("flood_control"):
            time.sleep(12 if body["error"].endswith("10") else 62)
            continue
        if st != 200 or not isinstance(body, dict) or body.get("status") != "ok":
            err = body.get("error") if isinstance(body, dict) else body
            break
        resp = body.get("response") or {}
        chans = {c.get("id"): c for c in resp.get("channels", [])}
        items = resp.get("items", [])
        for it in items:
            link = it.get("link") or ""
            ch = chans.get(it.get("channel_id"), {})
            text = _clean(it.get("text") or "")
            sig = hashlib.sha1(text.lower().encode()).hexdigest()
            if not text or sig in seen:  # рассылка одного и того же текста по чатам — один раз
                continue
            seen.add(sig)
            is_chat = ch.get("peer_type") == "chat"
            recs.append({"source": source, "platform": "telegram", "id": str(it.get("id")),
                         "url": link if link.startswith("http") else "https://" + link,
                         # в чатах TGStat не отдаёт автора сообщения → пусто (каждое сообщение — свой автор);
                         # у канала автор — сам канал
                         "author_hash": "" if is_chat else author_key("telegram", ch.get("username") or f"id:{it.get('channel_id')}"),
                         "date": dt.datetime.fromtimestamp(it.get("date", 0), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "text": ("[чат] " if is_chat else "[канал] ") + text,
                         "metrics": {**{k: it.get(k) for k in ("views", "shares_count", "comments_count", "reactions_count") if it.get(k) is not None},
                                     "peer_type": ch.get("peer_type"), "chat_title": ch.get("title")},
                         "lang": None})
        if len(items) < 50:
            break
        offset += 50
    return recs, {"source": source, "tag": tag, "status": st, "elapsed_s": round(el_total, 2), "n": len(recs), "credits": calls,
                  "credits_unit": "tgstat (запросы; слова — usage/stat)", "usd": round(calls * USD_PER_CREDIT, 4),
                  "balance_after": None, "raw_file": None, "error": err}


SOURCES = {"tgstat_posts": {"fn": search, "reserve": 4, "langs": "ru", "status": "ok",
                            "about": "Telegram: посты каналов и чатов (TGStat), морфология, глубина 10 дней на тарифе S"}}
