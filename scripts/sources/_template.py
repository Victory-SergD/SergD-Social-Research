"""ОБРАЗЕЦ нового источника (файлы с «_» реестр не грузит). Скопируй в scripts/sources/<имя>.py и заполни.
Проверка: python3 scripts/social.py --list  →  источник виден, key_ok=True;
          python3 scripts/social.py <источник> "тест" --limit 5 --tag probe  →  status 200, n > 0, ссылки открываются."""
import time
from _keys import load_key
from _http import get_json, author_key, slug

SERVICE = "example"
KEY_FILE, KEY_VAR = "example.env", "EXAMPLE_API_KEY"
USD_PER_CREDIT = 0.001


def search(source, query, since=None, lang=None, limit=25, tag="adhoc", sort=None):
    key = load_key(KEY_FILE[:-4], KEY_VAR)
    st, body, el = get_json("https://api.example.com/search", {"q": query, "limit": limit},
                            {"Authorization": "Bearer " + key}, raw_tag=f"{source}_{slug(query)}", raw_dir=tag)
    recs = [{"source": source, "platform": "example", "id": str(it["id"]), "url": it["url"],
             "author_hash": author_key("<площадка>", it.get("author_id") or it.get("author")), "date": it.get("date"), "text": it.get("text", ""),
             "metrics": {}, "lang": None} for it in (body or {}).get("items", [])]
    credits = 1
    return recs, {"source": source, "tag": tag, "status": st, "elapsed_s": el, "n": len(recs), "credits": credits,
                  "credits_unit": SERVICE, "usd": credits * USD_PER_CREDIT, "balance_after": None,
                  "raw_file": None, "error": None if st == 200 else body}


SOURCES = {"example_posts": {"fn": search, "reserve": 1, "about": "что ищет", "langs": "ru", "status": "untested"}}
