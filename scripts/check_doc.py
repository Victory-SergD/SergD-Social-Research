#!/usr/bin/env python3
"""Контракт документа для людей (шаг 4в навыка: документ исполнителю).

    python3 scripts/check_doc.py <doc.md> <run_dir> [<run_dir> ...] [--allow URL ...]

Проверяет детерминированно (смысл и числа проверяют помощники brief.js):
- ссылок вида ](../…) и ](runs/…) нет — документ уходит файлом в Telegram, относительные ссылки там не открываются;
- каждая https-ссылка есть в собранных данных: url поста или адрес внутри текста поста (posts.json или work/all.jsonl
  любого из run_dir), или передана в --allow (например, адрес из нашего замера).
Выход 0 — всё в порядке; 1 — список нарушений.
"""
import json, re, sys
from pathlib import Path

URL = re.compile(r"https?://[^\s\)\]>\"'`]+")
REL = re.compile(r"\]\((?!https?://|#|mailto:)([^)]+)\)")


def norm(u):
    u = u.rstrip("/.,;:")
    u = re.sub(r"^https?://(www\.)?", "", u)
    return u.rstrip("/")


def load(run_dirs):
    urls, texts = set(), []
    for rd in run_dirs:
        rd = Path(rd)
        pj = rd / "posts.json"
        if pj.exists():
            d = json.loads(pj.read_text())
            for p in (d.get("posts", d) if isinstance(d, dict) else d):
                urls.add(norm(p.get("url") or ""))
                texts.append(p.get("text") or "")
        al = rd / "work" / "all.jsonl"
        if al.exists():
            for line in al.read_text().splitlines():
                try:
                    p = json.loads(line)
                except ValueError:
                    continue
                urls.add(norm(p.get("url") or p.get("url_norm") or ""))
                texts.append(p.get("text") or "")
    return urls, " ".join(texts)


def main():
    args = sys.argv[1:]
    allow = set()
    if "--allow" in args:
        k = args.index("--allow")
        allow = {norm(u) for u in args[k + 1:]}
        args = args[:k]
    if len(args) < 2 or args[0] in ("-h", "--help"):
        sys.exit(__doc__)
    doc = Path(args[0]).read_text()
    urls, blob = load(args[1:])
    bad = []
    for r in REL.findall(doc):
        bad.append(f"относительная ссылка: {r}")
    found = sorted(set(URL.findall(doc)))
    for u in found:
        n = norm(u)
        if n in urls or n in allow or n.split("?")[0] in urls or n in blob:
            continue
        bad.append(f"нет в данных: {u}")
    print(f"ссылок {len(found)}, нарушений {len(bad)}")
    for b in bad:
        print(" -", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
