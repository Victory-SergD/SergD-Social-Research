"""Яндекс Search API (AI Studio): веб-поиск по русским соцплощадкам через site: — Дзен, Пикабу, отзовики, vc.ru, Хабр.
Проверено вживую: date:>, (site:a | site:b), GROUP_MODE_FLAT работают.
Цена: синхронный запрос (1 страница до 100 результатов) 0,488 ₽ днём, 0,366 ₽ ночью. Текст — только сниппет (до 5 фрагментов),
дата — modtime (время изменения страницы, не публикации). Лимит 1 запрос/с, до 250 результатов на запрос."""
import base64
import re
import time
import xml.etree.ElementTree as ET
from _keys import load_key
import urllib.parse
from _http import post_json, author_hash, slug  # noqa: F401

SERVICE = "yandex"
KEY_FILE, KEY_VAR = "yandex-search.env", "YANDEX_SEARCH_API_KEY"
USD_PER_CREDIT = 0.488 / 90  # 1 кредит = 1 платный запрос (страница) днём
API = "https://searchapi.api.cloud.yandex.net/v2/web/search"
# Площадки: Дзен только статьи и посты (/a, /b) — без подборок /list
# и видео; 2ГИС Яндекс не отдаёт, Карты — страницы поиска, убраны; banki.ru — отзывы о банках, 4pda — форум техники.
# Не больше 6 site: на запрос.
SITES = {
    "yandex_social": ["dzen.ru/a", "dzen.ru/b", "pikabu.ru", "vc.ru", "habr.com", "otzovik.com"],
    "yandex_reviews": ["otzovik.com", "irecommend.ru", "otzyvru.com", "banki.ru"],
    "yandex_forums": ["4pda.to", "banki.ru", "otvet.mail.ru", "drive2.ru"],
    "yandex_dzen": ["dzen.ru/a", "dzen.ru/b"],
    "yandex_pikabu": ["pikabu.ru"],
}
MAX_DOCS = 200  # реально доступно 200 документов на запрос (2 страницы по 100); 3-я страница — ошибка «слишком далекий»
_last = [0.0]


def _txt(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""



def _msk_to_utc(mt):
    """modtime Яндекса — время по Москве (UTC+3), формат 20260715T164300."""
    if len(mt) < 13:
        return None
    import datetime as _dt
    t = _dt.datetime(int(mt[0:4]), int(mt[4:6]), int(mt[6:8]), int(mt[9:11]), int(mt[11:13])) - _dt.timedelta(hours=3)
    return t.strftime("%Y-%m-%dT%H:%M:00Z")


_NOT_AUTHOR = {"a", "b", "list", "video", "shorts", "news", "articles", "search", "suggest", "id", "topic"}


def _author_from_url(url):
    """Автор, только если он есть в адресе: pikabu /@ник, vc.ru /id123, dzen.ru/<канал>
    (не /a/ /b/ /list/ — это статьи и подборки). Иначе пусто: раньше раздел пути («a», «story», «ru») склеивал всех в одного."""
    u = urllib.parse.urlsplit(url)
    host = u.netloc.lower().removeprefix("www.").removeprefix("m.")
    parts = [x for x in u.path.split("/") if x]
    if host == "pikabu.ru" and parts and parts[0].startswith("@"):
        return "pikabu:" + parts[0][1:].lower()
    if host == "vc.ru":
        for x in parts:
            if re.fullmatch(r"id\d+", x) or re.fullmatch(r"u/\d+.*", x):
                return "vc:" + x
        if parts and parts[0] == "u" and len(parts) > 1:
            return "vc:u" + parts[1].split("-")[0]
    if host == "dzen.ru" and parts and parts[0] not in _NOT_AUTHOR and not parts[0].startswith("?"):
        return "dzen:" + (parts[1] if parts[0] == "id" and len(parts) > 1 else parts[0]).lower()
    return ""

def search(source, query, since=None, lang=None, limit=100, tag="adhoc", sort=None):
    key = load_key("yandex-search", KEY_VAR)
    folder = load_key("yandex-search", "YANDEX_FOLDER_ID")
    sites = " | ".join(f"site:{s}" for s in SITES[source])
    q = f"{query} ({sites})" + (f" date:>{since[:10].replace('-', '')}" if since else "")
    recs, pages, el_total, st, err = [], 0, 0.0, None, None
    while len(recs) < min(limit, MAX_DOCS) and pages < MAX_DOCS // 100:
        wait = 1.05 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        payload = {"query": {"searchType": "SEARCH_TYPE_RU", "queryText": q, "familyMode": "FAMILY_MODE_NONE",
                             "page": str(pages), "fixTypoMode": "FIX_TYPO_MODE_OFF"},
                   # SORT_MODE_BY_TIME не шлём: выдача по времени — мусор; свежесть задаёт date:>
                   "sortSpec": {"sortMode": "SORT_MODE_BY_RELEVANCE", "sortOrder": "SORT_ORDER_DESC"},
                   "groupSpec": {"groupMode": "GROUP_MODE_FLAT", "groupsOnPage": "100", "docsInGroup": "1"},
                   "maxPassages": "5", "region": "225", "l10n": "LOCALIZATION_RU", "folderId": folder,
                   "responseFormat": "FORMAT_XML"}
        st, body, el = post_json(API, payload, {"Authorization": "Api-Key " + key},
                                 raw_tag=f"{source}_{slug(query)}_p{pages}", raw_dir=tag)
        _last[0] = time.time()
        pages += 1
        el_total += el
        if st != 200 or not isinstance(body, dict) or "rawData" not in body:
            err = body
            break
        root = ET.fromstring(base64.b64decode(body["rawData"]))
        docs = root.findall(".//results/grouping/group/doc")
        for d in docs:
            url = _txt(d.find("url"))
            mt = _txt(d.find("modtime"))
            date = _msk_to_utc(mt)
            passages = " … ".join(_txt(p) for p in d.findall("passages/passage")) or _txt(d.find("headline"))
            ext = _txt(d.find("properties/extended-text")) or _txt(d.find("extended-text"))
            text = _txt(d.find("title")) + "\n" + passages + (("\n" + ext) if ext and ext not in passages else "")
            host = _txt(d.find("domain"))
            recs.append({"source": source, "platform": host or "web", "id": url, "url": url,
                         "author_hash": author_hash(_author_from_url(url)),  # «площадка:автор» (= _http.author_key), пусто, если автора в адресе нет → каждый пост — свой автор
                         "date": date, "text": text.strip(), "metrics": {}, "lang": _txt(d.find("properties/lang")) or None})
        if len(docs) < 100:
            break
    return recs, {"source": source, "tag": tag, "status": st, "elapsed_s": round(el_total, 2), "n": len(recs), "credits": pages,
                  "credits_unit": "yandex (запросы)", "usd": round(pages * USD_PER_CREDIT, 4), "balance_after": None,
                  "raw_file": None, "error": err}


_ABOUT = {"yandex_social": "Яндекс по Дзену (статьи), Пикабу, vc.ru, Хабру, Отзовику (сниппет; полный текст — expand.py)",
          "yandex_reviews": "Яндекс по отзовикам: Отзовик, IRecommend, otzyvru, banki.ru (сниппеты)",
          "yandex_forums": "Яндекс по форумам: 4pda, banki.ru, Ответы Mail.ru, Drive2 (сниппеты)",
          "yandex_dzen": "Яндекс по Дзену, статьи и посты (полный текст — expand.py)",
          "yandex_pikabu": "Яндекс по Пикабу (полный текст — expand.py)"}
SOURCES = {n: {"fn": search, "reserve": 2, "langs": "ru", "status": "untested" if n == "yandex_forums" else "ok",
              "about": _ABOUT[n]} for n in SITES}
