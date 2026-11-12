"""Чтение ключей из папки ключей: <KEY_DIR>/<service>.env, строка ИМЯ=значение. Значение нигде не печатается.
Папка — переменная SOCIAL_RESEARCH_KEYS, по умолчанию ~/.config/sergd-social (права 700, файлы 600)."""
import os
from pathlib import Path

KEY_DIR = Path(os.environ.get("SOCIAL_RESEARCH_KEYS") or (Path.home() / ".config" / "sergd-social")).expanduser()


def load_key(service: str, var: str) -> str:
    path = KEY_DIR / f"{service}.env"
    for line in path.read_text().splitlines():
        if line.startswith(var + "="):
            val = line.split("=", 1)[1].strip()
            if val:
                return val
    raise RuntimeError(f"нет ключа {var} в {path.name}")
