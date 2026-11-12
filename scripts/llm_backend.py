"""Открытый LLM-бэкенд для opinions.py и expand.py: любые OpenAI-совместимые API (по умолчанию OpenRouter).

Модель задаётся как «провайдер:id», например "openrouter:deepseek/deepseek-v4-flash". Провайдеры:
  openrouter — https://openrouter.ai/api/v1, ключ OPENROUTER_API_KEY в <папка ключей>/openrouter.env
  deepseek   — https://api.deepseek.com,     ключ DEEPSEEK_API_KEY   в deepseek.env
  nano       — https://nano-gpt.com/api/v1,  ключ NANOGPT_API_KEY    в nanogpt.env
  свой       — переменная SOCIAL_LLM_<ИМЯ>_BASE=https://…/v1 и ключ <ИМЯ>_API_KEY в <имя>.env
Папка ключей — scripts/_keys.py (переменная SOCIAL_RESEARCH_KEYS, по умолчанию ~/.config/sergd-social). Ключи нигде
не печатаются. Модели по умолчанию — ниже; поменять можно переменными окружения SOCIAL_LLM_WRITER, SOCIAL_LLM_QUOTER,
SOCIAL_LLM_CANON, SOCIAL_LLM_EXTRACTOR_1, SOCIAL_LLM_EXTRACTOR_2 (список через запятую: основная, запасная).

Интерфейс: llm(prompt, models, max_tokens, timeout, want_json=True) — по очереди пробует модели из кортежа, 429 и сбои
сети — повтор с паузой, 401/402/403 и нет ключа — сразу к следующей модели; want_json — вернуть разобранный JSON
(или None), иначе текст. STATS — исходы попыток, _TL.served — какая модель ответила, spend() — расход в $ (OpenRouter
отдаёт цену запроса; у остальных — по токенам, если цена известна, иначе 0).
"""
import collections, json, os, re, threading, time, urllib.error, urllib.request

from _keys import KEY_DIR

PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter.env", "OPENROUTER_API_KEY"),
    "deepseek": ("https://api.deepseek.com", "deepseek.env", "DEEPSEEK_API_KEY"),
    "nano": ("https://nano-gpt.com/api/v1", "nanogpt.env", "NANOGPT_API_KEY"),
}


def _env_models(name, default):
    v = os.environ.get(name)
    return tuple(x.strip() for x in v.split(",") if x.strip()) if v else default


OR = "openrouter:"
WRITER, WRITER_FALLBACK = (_env_models("SOCIAL_LLM_WRITER", (OR + "anthropic/claude-sonnet-5.5", OR + "openai/gpt-5.4")) + (None,))[:2]
QUOTER = _env_models("SOCIAL_LLM_QUOTER", (OR + "openai/gpt-5.4-mini", OR + "openai/gpt-5.4"))
CANON_CHEAP = _env_models("SOCIAL_LLM_CANON", (OR + "deepseek/deepseek-v4-flash", OR + "openai/gpt-5.4-mini"))
# Два независимых сводчика (основной, запасной) — разные семейства, чтобы не повторять ошибки друг друга.
SOCIAL_EXTRACTORS = (
    (_env_models("SOCIAL_LLM_EXTRACTOR_1", (OR + "deepseek/deepseek-v4-flash", OR + "qwen/qwen3-235b-a22b-2507")) + (None,))[:2],
    (_env_models("SOCIAL_LLM_EXTRACTOR_2", (OR + "deepseek/deepseek-v4-pro", OR + "z-ai/glm-4.6")) + (None,))[:2],
)

STATS = collections.defaultdict(collections.Counter)
_TL = threading.local()
_COST = collections.defaultdict(float)
_LOCK = threading.Lock()
_KEYS = {}


def _s(x) -> str:
    """Любое значение → строка (у части поставщиков content — список кусков)."""
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        return "\n".join(_s(p.get("text") if isinstance(p, dict) else p) for p in x)
    if isinstance(x, dict):
        return _s(x.get("text")) if "text" in x else json.dumps(x, ensure_ascii=False)
    return str(x)


def _norm(s: str) -> str:
    return re.sub(r"[\s«»\"'“”„*_`]+", " ", (s or "").lower()).strip()


def _provider(name):
    if name in PROVIDERS:
        return PROVIDERS[name]
    base = os.environ.get(f"SOCIAL_LLM_{name.upper()}_BASE")
    return (base, f"{name}.env", f"{name.upper()}_API_KEY") if base else None


def _key(prov):
    if prov not in _KEYS:
        p = _provider(prov)
        val = None
        if p:
            f = KEY_DIR / p[1]
            if f.exists():
                for line in f.read_text().splitlines():
                    if line.startswith(p[2] + "="):
                        val = line.split("=", 1)[1].strip() or None
        _KEYS[prov] = val
    return _KEYS[prov]


def _call(prov, mid, prompt, max_tokens, timeout):
    base = _provider(prov)[0].rstrip("/")
    body = {"model": mid, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens}
    if prov == "openrouter":
        body["usage"] = {"include": True}  # цена запроса в ответе
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {_key(prov)}", "Content-Type": "application/json",
                                          "User-Agent": "social-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def llm(prompt, models, max_tokens=4000, timeout=300, want_json=True, pauses=(5, 15, 30), hedge_after=None):
    _TL.served = None
    for m in models:
        if not m:
            continue
        prov, mid = m.split(":", 1) if ":" in m and _provider(m.split(":", 1)[0]) else ("openrouter", m)
        if not _provider(prov) or not _key(prov):
            STATS[m]["нет ключа"] += 1
            continue
        for n, pause in enumerate(pauses, 1):
            try:
                d = _call(prov, mid, prompt, max_tokens, timeout)
                u = d.get("usage") or {}
                with _LOCK:
                    _COST[m] += float(u.get("cost") or 0)
                txt = _s((d.get("choices") or [{}])[0].get("message", {}).get("content"))
                if not want_json:
                    if txt.strip():
                        STATS[m][f"ok@{n}"] += 1
                        _TL.served = m
                        return txt
                    STATS[m]["пусто"] += 1
                    continue
                mm = re.search(r"\{.*\}", txt, re.S)
                if mm:
                    try:
                        j = json.loads(mm.group(0))
                    except ValueError:
                        STATS[m]["200 без JSON"] += 1
                        continue
                    STATS[m][f"ok@{n}"] += 1
                    _TL.served = m
                    return j
                STATS[m]["200 без JSON"] += 1
            except urllib.error.HTTPError as e:
                STATS[m][str(e.code)] += 1
                if e.code in (400, 401, 402, 403, 404):
                    break
                time.sleep(pause)
            except Exception as e:  # сеть, таймаут — повтор
                STATS[m][type(e).__name__] += 1
                time.sleep(pause)
    return None


def spend() -> dict:
    by = {m: round(c, 4) for m, c in _COST.items() if c}
    usd = round(sum(by.values()), 4)
    return {"usd": usd, "total_usd": usd, "by_model": by}


def missing_keys(models):
    """Каких ключей не хватает: [(файл, переменная)] для моделей, у которых нет ключа (пусто — всё есть)."""
    out = []
    for m in models:
        if not m:
            continue
        prov = m.split(":", 1)[0] if ":" in m and _provider(m.split(":", 1)[0]) else "openrouter"
        p = _provider(prov)
        if p and not _key(prov) and (p[1], p[2]) not in out:
            out.append((p[1], p[2]))
    return out
