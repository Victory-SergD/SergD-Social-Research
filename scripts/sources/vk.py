"""VK API: newsfeed.search — поиск постов по всему ВКонтакте с сервисным ключом приложения VK ID.
Сверено по документации VK API и схеме VKCOM/vk-api-schema 5.199.
НЕ ПРОВЕРЕНО ВЖИВУЮ (status untested). Первым запросом проверить: выдачу с сервисным ключом против поиска на vk.com,
глубину (30/180/365 дней). Бесплатно: 100 млн вызовов/мес с подтверждённым профилем бизнеса (10 000 без).
Ловушки: без start_time — только последние сутки; потолок 1 000 результатов на запрос; ≤ 3 запроса/с."""
import datetime as dt
import time
from _keys import load_key
from _http import get_json, author_key, slug

SERVICE = "vk"
KEY_FILE, KEY_VAR = "vk.env", "VK_SERVICE_TOKEN"
USD_PER_CREDIT = 0.0  # кредит = 1 вызов API, в пределах лимита бесплатно
API = "https://api.vk.ru/method/newsfeed.search"
_last = [0.0]


def _epoch(since):
    return int(dt.datetime.strptime(since[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())


def search(source, query, since=None, lang=None, limit=200, tag="adhoc", sort=None):
    key = load_key("vk", KEY_VAR)
    start = _epoch(since) if since else int(time.time()) - 30 * 86400  # без start_time VK ищет только за сутки
    recs, cursor, calls, el_total, st, err, raw = [], None, 0, 0.0, None, None, None
    while len(recs) < min(limit, 1000) and calls < 6:
        wait = 0.35 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        params = {"q": query, "v": "5.199", "count": 200, "start_time": start, "end_time": int(time.time()),
                  "extended": 1, "fields": "screen_name", "start_from": cursor}
        st, body, el = get_json(API, params, {"Authorization": "Bearer " + key}, raw_tag=f"{source}_{slug(query)}_p{calls}", raw_dir=tag)
        _last[0] = time.time()
        calls += 1
        el_total += el
        if not isinstance(body, dict) or "error" in body or st != 200:
            err = (body or {}).get("error") if isinstance(body, dict) else body
            break
        resp = body.get("response") or {}
        groups = {g["id"]: g for g in resp.get("groups", [])}
        profiles = {p["id"]: p for p in resp.get("profiles", [])}
        for it in resp.get("items", []):
            owner, pid = it.get("owner_id"), it.get("id")
            frm = it.get("from_id") or owner
            who = groups.get(-frm, {}).get("screen_name") if frm and frm < 0 else profiles.get(frm, {}).get("screen_name")
            recs.append({"source": source, "platform": "vk", "id": f"{owner}_{pid}", "url": f"https://vk.com/wall{owner}_{pid}",
                         "author_hash": author_key("vk", frm),  # стабильный числовой id (from_id), не ник
                         "date": dt.datetime.fromtimestamp(it.get("date", 0), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "text": (it.get("text") or "").strip(),
                         "metrics": {k: (it.get(k) or {}).get("count") for k in ("likes", "reposts", "views", "comments") if it.get(k)},
                         "lang": None})
        cursor = resp.get("next_from")
        if not cursor:
            break
    return recs, {"source": source, "tag": tag, "status": st, "elapsed_s": round(el_total, 2), "n": len(recs), "credits": calls,
                  "credits_unit": "vk (вызовы)", "usd": 0.0, "balance_after": None, "raw_file": None, "error": err}


SOURCES = {"vk_posts": {"fn": search, "reserve": 6, "langs": "ru", "status": "untested",
                        "about": "VK: посты по всему ВКонтакте (newsfeed.search), до 1 000 на запрос, бесплатно"}}
