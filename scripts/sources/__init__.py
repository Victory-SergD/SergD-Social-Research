"""Реестр подключаемых источников social-research.

Новый API = один файл scripts/sources/<имя>.py по образцу _template.py. Модуль объявляет:
  SERVICE        — короткое имя сервиса для учёта бюджета (vk, yandex, tgstat…), ключ предела в run_plan --max SERVICE=N
  KEY_FILE, KEY_VAR — файл <папка ключей, scripts/_keys.py>/<KEY_FILE> и имя переменной (None, если ключ не нужен)
  USD_PER_CREDIT — $ за единицу, которую возвращает meta["credits"]
  SOURCES        — {имя_источника: {"fn": функция поиска, "reserve": запас кредитов на 1 вызов,
                     "about": что ищет (одна строка), "langs": "ru" | "en" | "ru,en", "status": "ok" | "untested" | "broken"}}
Функция поиска: fn(source, query, since=None, lang=None, limit=25, tag="adhoc", sort=None) -> (records, meta)
  record = {source, platform, id, url, author_hash, date (ISO), text, metrics{}, lang}
  author_hash — _http.author_key(площадка, стабильный id | ник): одна схема «площадка:id» для всех API
  meta   = {source, tag, status, elapsed_s, n, credits, credits_unit, usd, balance_after, raw_file, error}
Сырые ответы сохранять через _http.get_json(..., raw_tag=..., raw_dir=tag) — они нужны проверке ссылок.
"""
import importlib, pkgutil
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _keys import KEY_DIR  # noqa: E402
PLUGINS = {}   # source -> {"module", "fn", "reserve", "service", "usd", "about", "langs", "status", "key_ok"}


def _load():
    for m in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if m.name.startswith("_"):
            continue
        mod = importlib.import_module(f"sources.{m.name}")
        key_ok = True
        if getattr(mod, "KEY_FILE", None):
            f = KEY_DIR / mod.KEY_FILE
            key_ok = f.exists() and any(l.startswith(mod.KEY_VAR + "=") and len(l.split("=", 1)[1].strip()) > 0
                                        for l in f.read_text().splitlines())
        for name, spec in mod.SOURCES.items():
            PLUGINS[name] = {"module": m.name, "fn": spec["fn"], "reserve": spec.get("reserve", 25),
                             "service": mod.SERVICE, "usd": getattr(mod, "USD_PER_CREDIT", 0.0),
                             "about": spec.get("about", ""), "langs": spec.get("langs", ""),
                             "status": spec.get("status", "untested"), "key_ok": key_ok}


_load()
