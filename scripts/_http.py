"""Общий HTTP-вызов: JSON, таймер, сохранение сырого ответа в data/raw/ (не в git). Ключи не логируются."""
import json, time, urllib.request, urllib.parse, urllib.error, hashlib, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
UA = "sergd-social-research/0.1"


SECRET_PARAMS = {"token", "access_token", "api_key", "apikey", "key", "secret", "folderid", "folder_id"}


def _safe_params(params: dict) -> dict:
    """Параметры для сырого файла: значения ключей (TGStat token и т. п.) заменены на «***»."""
    return {k: ("***" if str(k).lower() in SECRET_PARAMS else v) for k, v in (params or {}).items()}


def slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^\w]+", "-", s.lower(), flags=re.U).strip("-")
    return s[:n] or "q"


def get_json(url: str, params: dict, headers: dict, timeout: int = 90, raw_tag: str = "", raw_dir: str = "misc"):
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    full = url + ("?" + qs if qs else "")
    req = urllib.request.Request(full, headers={"User-Agent": UA, "Accept": "application/json", **headers})
    t0 = time.time()
    status, body, hdrs = None, None, {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, hdrs = r.status, dict(r.headers)
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        status, hdrs = e.code, dict(e.headers)
        txt = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(txt)
        except Exception:
            body = {"_text": txt[:2000]}
    except (urllib.error.URLError, OSError, ValueError) as e:  # сеть, таймаут, не-JSON: не ронять вызывающего
        status, body = None, {"_error": f"{type(e).__name__}: {e}"[:500]}
    elapsed = round(time.time() - t0, 2)
    if raw_tag:
        d = RAW / raw_dir
        d.mkdir(parents=True, exist_ok=True)
        fn = d / f"{time.strftime('%Y%m%d-%H%M%S')}_{raw_tag}.json"
        fn.write_text(json.dumps({"url": url, "params": _safe_params(params), "status": status, "elapsed_s": elapsed,
                                   "headers": {k: v for k, v in hdrs.items() if k.lower().startswith(("x-", "ratelimit", "retry"))},
                                   "body": body}, ensure_ascii=False, indent=1))
    return status, body, elapsed


def author_hash(handle) -> str:
    if not handle:
        return ""
    return hashlib.sha256(str(handle).lower().encode()).hexdigest()[:12]


DELETED_AUTHORS = {"[deleted]", "[removed]"}


def author_key(platform, *ids) -> str:
    """Единая схема автора во всех источниках: хэш «платформа:первый непустой id» (id канала, author.id, urn, ник).
    Один человек на одной площадке — один хэш в любом API; одинаковые ники разных площадок не склеиваются.
    [deleted]/пусто → "" (opinions.py считает такой пост отдельным автором)."""
    for v in ids:
        if v not in (None, "") and str(v) not in DELETED_AUTHORS:
            return author_hash(f"{platform}:{v}")
    return ""


def post_json(url: str, payload: dict, headers: dict, timeout: int = 90, raw_tag: str = "", raw_dir: str = "misc"):
    """POST с JSON-телом (Яндекс Search API). Сохраняет сырой ответ так же, как get_json; заголовки с ключом не пишутся."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json", **headers})
    t0 = time.time()
    status, body = None, None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.status
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        status = e.code
        txt = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(txt)
        except Exception:
            body = {"_text": txt[:2000]}
    except (urllib.error.URLError, OSError, ValueError) as e:  # сеть, таймаут, не-JSON: не ронять вызывающего
        status, body = None, {"_error": f"{type(e).__name__}: {e}"[:500]}
    elapsed = round(time.time() - t0, 2)
    if raw_tag:
        d = RAW / raw_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{time.strftime('%Y%m%d-%H%M%S')}_{raw_tag}.json").write_text(
            json.dumps({"url": url, "payload": _safe_params(payload), "status": status, "elapsed_s": elapsed, "body": body}, ensure_ascii=False, indent=1))
    return status, body, elapsed
