#!/usr/bin/env python3
"""После workflows/analyze.js: сохранить разметку и оценки в папку запуска и собрать страницу.
python3 scripts/save_analysis.py <output-file из уведомления о завершении Workflow> [runs/<папка>]
→ work/marks.json, analysis.json, затем render.py и check_links.py.
Вход — JSON {"result": {"marks": [{i, rel, kind, lang, first_hand, seller, outcome, close, gist}], "report_md": "…",
"analyst": {...}, "critics": [...]}} (так возвращает workflows/analyze.js; без Workflow — собрать такой же файл из
ответов помощников: marks обязателен, report_md нужен для отчёта)."""
import json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
    sys.exit(__doc__)
d = json.loads(Path(sys.argv[1]).read_text())
res = d.get("result", d)
if isinstance(res, str):
    res = json.loads(res)
rd = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(res["run_dir"])
if res.get("report_md"):
    (rd / "report.md").write_text(res["report_md"].rstrip() + "\n")
else:
    print("ВНИМАНИЕ: в результате нет report_md — отчёта нет, страница соберётся без него")
(rd / "work" / "marks.json").write_text(json.dumps(res["marks"], ensure_ascii=False))
(rd / "analysis.json").write_text(json.dumps({"analyst": res.get("analyst"), "critics": res.get("critics", []),
                                              "revision": res.get("revision"), "failed_chunks": res.get("failed_chunks")},
                                             ensure_ascii=False, indent=1))
print(f"разметка: {len(res['marks'])} постов, не размечено чанков: {res.get('failed_chunks')}")
subprocess.run([sys.executable, str(ROOT / "scripts" / "render.py"), str(rd)], check=True)
sys.exit(subprocess.run([sys.executable, str(ROOT / "scripts" / "check_links.py"), str(rd)]).returncode)
