#!/usr/bin/env python3
"""Перед коммитом: нет ли в индексе git точных значений ключей из папки ключей (scripts/_keys.py) — *.env.
Значения не печатаются — только имя файла ключа. Выход 1, если нашлось; 2 — сравнивать не с чем.
Ещё папки с ключами (например, модели сводки) — переменная SOCIAL_RESEARCH_EXTRA_KEYS="папка1:папка2".
(Простой grep по 'sc_|unif_' даёт ложные срабатывания на имена источников sc_x и request_id unif_…)"""
import subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _keys import KEY_DIR  # noqa: E402

diff = subprocess.run(["git", "diff", "--cached"], capture_output=True, text=True).stdout
bad = []
import os  # noqa: E402
dirs = [KEY_DIR] + [Path(x).expanduser() for x in os.environ.get("SOCIAL_RESEARCH_EXTRA_KEYS", "").split(":") if x]
files = sorted(f for d in dirs if d.is_dir() for f in d.glob("*.env"))
if not files:  # сравнивать не с чем — это не «чисто»
    print(f"ВНИМАНИЕ: в {', '.join(str(d) for d in dirs)} нет *.env — проверка ничего не сравнила")
    sys.exit(2)
for f in files:
    for line in f.read_text().splitlines():
        if "=" in line:
            val = line.split("=", 1)[1].strip()
            if len(val) >= 12 and (val in diff or val[:16] in diff):
                bad.append(f.name)
if bad:
    print("НАЙДЕН КЛЮЧ из:", ", ".join(sorted(set(bad))))
    sys.exit(1)
print("ключей в индексе нет")
