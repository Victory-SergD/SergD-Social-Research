#!/usr/bin/env python3
"""Документ .md → самодостаточная HTML-страница для отправки файлом (Telegram открывает её в браузере телефона).

    python3 scripts/doc_html.py <doc.md> [<out.html>]

Для чатов: короткий пост + подробности HTML-файлом ответом. Страница без внешних ресурсов, читается с телефона,
светлая и тёмная тема, ссылки открываются в новой вкладке.
"""
import html, re, sys
from pathlib import Path

import mistune

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6b66;--accent:#2f5d8a;--line:#e4e1da;--code:#f0ede6}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--muted:#a3a29b;--accent:#8db7e3;--line:#33322f;--code:#23221f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.6em;line-height:1.25;margin:.2em 0 .6em}
h2{font-size:1.25em;margin:2em 0 .5em;padding-top:.6em;border-top:1px solid var(--line)}
h3{font-size:1.08em;margin:1.5em 0 .4em}
p,li{overflow-wrap:anywhere}
ul,ol{padding-left:1.3em}
li{margin:.25em 0}
a{color:var(--accent)}
code{background:var(--code);padding:.1em .35em;border-radius:4px;font-size:.88em}
pre{background:var(--code);padding:12px;border-radius:8px;overflow-x:auto}
table{border-collapse:collapse;width:100%;display:block;overflow-x:auto;font-size:.92em}
th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
hr{border:0;border-top:1px solid var(--line);margin:2em 0}
blockquote{margin:1em 0;padding:.2em 1em;border-left:3px solid var(--accent);color:var(--muted)}
.foot{color:var(--muted);font-size:.85em;margin-top:3em}
"""


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__)
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".html")
    text = src.read_text()
    m = re.search(r"^#\s+(.+)$", text, re.M)
    title = m.group(1).strip() if m else src.stem
    title = re.sub(r"[*_`]", "", title)
    # mistune (CommonMark): списки без пустой строки перед ними и вложенность в 2 пробела — как в Telegram/GitHub.
    # python-markdown их склеивал в абзац (из 355 пунктов в HTML выходило 37).
    body = mistune.create_markdown(escape=False, plugins=["table", "strikethrough", "url"])(text)
    body = re.sub(r'<a href="(https?://)', r'<a target="_blank" rel="noopener" href="\1', body)
    page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style></head>
<body><main>{body}
<p class="foot">«Народное мнение» · {html.escape(src.name)}</p></main></body></html>
"""
    out.write_text(page)
    print(out)


if __name__ == "__main__":
    main()
