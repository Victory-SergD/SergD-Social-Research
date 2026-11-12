"""Сборка work/compact.jsonl и чанков для разметчика (workflows/analyze.js). Общая для run_plan.py и expand.py.

Контракт (форматы, не смысл):
- текст разметчику — до 2 500 знаков: начало 2 000 + конец 500 (длинные обзоры и ветки решаются по сути, а не по началу);
- чанки по 40 постов;
- подсказки поставщика (hint) — как есть, с пометкой в промпте «может ошибаться»; решает модель;
- author_name — имя/ник автора X отдельно от текста: X сверяет слова запроса и с именем автора, «совпало только по
  имени» решает разметчик (это вопрос смысла, не регекс).
Порядок all.jsonl = номер i; новые записи (expand.py) только дописываются в конец, номера старых не меняются."""
import json
from pathlib import Path

HEAD, TAIL, CHUNK = 2000, 500, 40
HINT_KEYS = ("sc_rel", "sc_seller", "sc_intent", "sc_stance", "subreddit", "flair", "is_reply", "reply_to",
             "followers", "verified", "author_role", "kind_sc", "date_confidence", "out_of_window")


# Параметры адреса, которые различают страницы (тема форума, сообщение, ролик, комментарий); остальные — служебные
# (utm_*, st/start — страница выдачи внутри темы, fbclid…) и для дедупа отбрасываются. Контракт формата адреса.
KEEP_QS = {"id", "v", "lc", "showtopic", "showforum", "topic", "t", "p", "f", "item", "story_fbid", "fbid",
           "thread", "threadid", "postid", "question", "qid", "articleid", "review"}
DROP_QS_BY_HOST = {"youtube.com": {"t"}}  # у YouTube t — секунда ролика, не другая страница


def norm_url(u):
    import urllib.parse
    u = (u or "").replace("twitter.com", "x.com").replace("://reddit.com", "://www.reddit.com")
    base, _, qs = u.partition("?")
    qs = qs.split("#")[0]
    if qs:
        host = urllib.parse.urlsplit(base).netloc.lower().removeprefix("www.").removeprefix("m.")
        drop = DROP_QS_BY_HOST.get(host, set())
        keep = [(k, v) for k, v in urllib.parse.parse_qsl(qs, keep_blank_values=True) if k.lower() in KEEP_QS and k.lower() not in drop]
        return base.rstrip("/") + ("?" + urllib.parse.urlencode(keep) if keep else "")
    return base.split("#")[0].rstrip("/")


def clip(text, head=HEAD, tail=TAIL):
    t = text or ""
    if len(t) <= head + tail:
        return t
    return f"{t[:head]}\n[… пропущено {len(t) - head - tail} знаков …]\n{t[-tail:]}"


def compact_row(i, r):
    row = {"i": i, "url": r.get("url_norm") or r.get("url"), "source": r["source"], "platform": r.get("platform"),
           "kind": r.get("kind") or "post", "date": (r.get("date") or "")[:10], "author": r.get("author_hash"),
           "found_by": r.get("found_by"), "metrics": r.get("metrics")}
    if r.get("author_name"):
        row["author_name"] = r["author_name"]
    if r.get("parent"):
        row["parent"] = r["parent"]
    if r.get("thread"):
        row["thread"] = r["thread"]
    hint = {k: r[k] for k in HINT_KEYS if r.get(k) not in (None, "", False)}
    if hint:
        row["hint"] = hint
    if r.get("enriched"):
        row["full_page"] = True
    row["text"] = clip(r.get("text"))
    return row


def write_compact(out: Path, recs, chunk=CHUNK):
    """compact.jsonl + chunk_NN.jsonl (старые чанки удаляются). Возвращает (rows, [пути чанков])."""
    rows = [compact_row(i, r) for i, r in enumerate(recs)]
    (out / "compact.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))
    for f in out.glob("chunk_*.jsonl"):
        f.unlink()
    chunks = []
    for k in range(0, len(rows), chunk):
        f = out / f"chunk_{k // chunk:02d}.jsonl"
        f.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows[k:k + chunk]))
        chunks.append(str(f))
    return rows, chunks


def unify_x_authors(recs, extra=()):
    """Один человек в X — один автор: twitterapi (и everywhere, если отдал id) знает стабильный author.id,
    SocialCrawl sc_x — только ник. Все записи X несут x_handle_hash (хэш ника); у записей с x_has_id author_hash —
    хэш id; тем же ником у остальных записей X подставляем хэш id.
    extra — записи tw_x, отброшенные как дубли (их пост уже есть от sc_x), но знающие id автора."""
    m = {r["x_handle_hash"]: r["author_hash"] for r in [*recs, *extra]
         if (r.get("x_has_id") or r.get("source") == "tw_x") and r.get("x_handle_hash") and r.get("author_hash")}
    n = 0
    for r in recs:
        if r.get("x_handle_hash") in m and r.get("author_hash") != m[r["x_handle_hash"]]:
            r["author_hash"] = m[r["x_handle_hash"]]
            n += 1
    return n
