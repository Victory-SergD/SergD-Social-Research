#!/usr/bin/env python3
"""Контракт: каждая ссылка на пост в отчёте запуска есть в сырых данных запуска.
python3 scripts/check_links.py runs/<папка>                  — запуск навыка: report.md против work/ и data/raw/<tag>
Печатает найдено/не найдено, выход 1 при пропусках."""
import sys, re, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POST = re.compile(r"https?://(?:www\.)?(?:x\.com|twitter\.com|reddit\.com|old\.reddit\.com|news\.ycombinator\.com|"
                  r"threads\.(?:net|com)|bsky\.app|linkedin\.com|youtube\.com|youtu\.be|t\.me)[^\s)\]>\"'`|]*")


def norm(u):
    u = u.split("?id=")[0] + ("?id=" + u.split("?id=")[1] if "?id=" in u else "")
    u = u.replace("twitter.com", "x.com").replace("old.reddit.com", "www.reddit.com").replace("://reddit.com", "://www.reddit.com")
    if "ycombinator" not in u and "youtube.com/watch" not in u:  # комментарий YouTube: watch?v=…&lc=…
        u = u.split("?")[0]
    return u.rstrip("/.,;:").lower()


def main():
    known = set()
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__)
    if len(sys.argv) == 2:  # папка запуска
        rd = Path(sys.argv[1])
        report = rd / "report.md"
        meta = json.loads((rd / "meta.json").read_text())
        dirs = [rd / "work", ROOT / meta.get("raw_dir", f"data/raw/{meta['tag']}")]
    else:
        sys.exit(__doc__)
    for d in dirs:
        for f in d.glob("*.json*"):
            known |= {norm(m) for m in POST.findall(f.read_text(errors="ignore"))}
    links = sorted({norm(m) for m in POST.findall(report.read_text())})
    miss = [u for u in links if u not in known]
    print(f"ссылок в отчёте: {len(links)}, есть в сырье: {len(links) - len(miss)}, нет: {len(miss)}")
    for u in miss:
        print("  НЕТ:", u)
    sys.exit(1 if miss else 0)


if __name__ == "__main__":
    main()
