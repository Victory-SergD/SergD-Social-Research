#!/usr/bin/env python3
"""Сборка итога запуска social-research: runs/<папка>/ → posts.json, sources.json, report.html (+ report.md уже есть).

Вход в папке запуска:
  meta.json      — {question, since, tag, created, ordered_by, budget}
  plan.json      — запросы
  work/all.jsonl — посты после дедупа (run_plan.py), work/runs.jsonl — строки запросов
  work/marks.json— разметка моделью [{i, rel, kind, lang, first_hand, gist}]
  analysis.json  — оценки: {"analyst": {...source_grades, overall_grade, headline}, "critics": [{lens, source_grades, overall_grade}]}
  report.md      — отчёт аналитика после критики
Запуск: python3 scripts/render.py runs/<папка>
"""
import collections, html, json, os, sys
from pathlib import Path

import markdown

sys.path.insert(0, str(Path(__file__).resolve().parent))
from social import catalog, USD_PER_CREDIT  # noqa: E402

E = html.escape
RUB = 90
REL_NAME = {2: "по делу", 1: "по теме", 0: "шум", None: "не разобрано"}
KIND_RU = {"experience": "опыт", "advice": "совет", "opinion": "мнение", "news": "новость", "ad": "реклама",
           "question": "вопрос", "other": "другое"}
HOW_TO_READ = (
    "**Как читать.** «По делу» — пост прямо отвечает на вопрос (опыт, факт, совет, цифры); «по теме» — косвенно; "
    "«шум» — совпадение слов. Разметку делала модель по смыслу, не по словам. **Сила вывода — число разных авторов**, "
    "не постов: один автор с десятью постами — это одно мнение. Реклама и продавцы помечены отдельно. Каждая ссылка "
    "отчёта сверена скриптом с сырыми данными; тексты постов — данные, а не проверенные факты.")


def _write(path, text):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8", errors="replace") as f:
        f.write(text)
    os.replace(tmp, path)


def md(text):
    return markdown.markdown(text or "", extensions=["tables", "sane_lists"])


def split_report(text):
    """«Главное» — для блока «Итог» вверху; остальное — полный отчёт ниже."""
    lines = (text or "").split("\n")
    start = next((i for i, l in enumerate(lines) if l.startswith("## ") and "Главное" in l), None)
    if start is None:
        return "", text
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = [l for l in lines[start + 1:end] if l.strip() != "---"]  # разделители «---» убрать, маркеры списка оставить
    return "\n".join(body).strip(), "\n".join(lines[:start] + lines[end:])


def load(run):
    rd = Path(run)
    meta = json.loads((rd / "meta.json").read_text())
    recs = [json.loads(l) for l in open(rd / "work" / "all.jsonl")]
    marks = {m["i"]: m for m in json.loads((rd / "work" / "marks.json").read_text())} if (rd / "work" / "marks.json").exists() else {}
    runs = [json.loads(l) for l in open(rd / "work" / "runs.jsonl")] if (rd / "work" / "runs.jsonl").exists() else []
    analysis = json.loads((rd / "analysis.json").read_text()) if (rd / "analysis.json").exists() else {}
    report = (rd / "report.md").read_text() if (rd / "report.md").exists() else ""
    op = json.loads((rd / "options.json").read_text()) if (rd / "options.json").exists() else None
    return rd, meta, recs, marks, runs, analysis, report, op


def build(run):
    rd, meta, recs, marks, runs, analysis, report, op = load(run)
    cat = catalog()
    posts = []
    for i, r in enumerate(recs):
        m = marks.get(i, {})
        posts.append({"i": i, "url": r.get("url_norm") or r.get("url"), "source": r["source"], "platform": r.get("platform"),
                      "date": (r.get("date") or "")[:10], "author_hash": r.get("author_hash"), "found_by": r.get("found_by", []),
                      "metrics": r.get("metrics") or {}, "text": r.get("text") or "", "rel": m.get("rel"),
                      "kind": m.get("kind"), "lang": m.get("lang"), "first_hand": m.get("first_hand"), "seller": m.get("seller"),
                      "gist": m.get("gist", "")})
    # источники: пост засчитывается каждому источнику, чей запрос его нашёл
    by = collections.defaultdict(list)
    for p in posts:
        for fb in p["found_by"]:
            by[fb.split("#")[0]].append(p)
    grades = collections.defaultdict(dict)
    a = analysis.get("analyst") or {}
    for g in a.get("source_grades", []):
        grades[g["source"]]["аналитик"] = g["grade"]
    for c in analysis.get("critics", []):
        for g in c.get("source_grades", []):
            grades[g["source"]]["критик: " + {"fidelity": "верность", "completeness": "полнота"}.get(c.get("lens"), c.get("lens", "?"))] = g["grade"]
    srcs = []
    for s in sorted({x["source"] for x in runs} | set(by)):
        ps = by.get(s, [])
        rel2 = [p for p in ps if p["rel"] == 2]
        rel1 = [p for p in ps if (p["rel"] or 0) >= 1]
        dates = sorted(p["date"] for p in rel2 if p["date"])
        cred = sum((x.get("credits") or 0) for x in runs if x.get("source") == s)
        svc = cat.get(s, {}).get("service", s.split("_")[0])
        usd = sum((x.get("usd") or 0) for x in runs if x.get("source") == s)
        srcs.append({"source": s, "about": cat.get(s, {}).get("about", ""), "found": len(ps), "rel_ge1": len(rel1),
                     "rel2": len(rel2), "authors_rel2": len({p["author_hash"] for p in rel2 if p["author_hash"]}),
                     "ru_share_rel": round(sum(1 for p in rel1 if p["lang"] == "ru") / len(rel1), 2) if rel1 else None,
                     "first_hand_rel2": sum(1 for p in rel2 if p["first_hand"]),
                     "median_date_rel2": dates[len(dates) // 2] if dates else None, "credits": cred, "service": svc,
                     "usd": round(usd, 4), "secs": round(sum((x.get("elapsed_s") or 0) for x in runs if x.get("source") == s), 1),
                     "errors": [x.get("error") or x.get("status") for x in runs if x.get("source") == s and (x.get("error") or x.get("status") not in (200, None))],
                     "grades": grades.get(s, {})})
    srcs.sort(key=lambda x: (-x["rel2"], -x["found"]))
    rel2_all = [p for p in posts if p["rel"] == 2]
    rel1_all = [p for p in posts if (p["rel"] or 0) >= 1]
    tot = {"posts": len(posts), "rel_ge1": len(rel1_all), "rel2": len(rel2_all),
           "authors_rel2": len({p["author_hash"] for p in rel2_all if p["author_hash"]}),
           "ru_share_rel": round(sum(1 for p in rel1_all if p["lang"] == "ru") / len(rel1_all), 2) if rel1_all else None,
           "unmarked": sum(1 for p in posts if p["rel"] is None),
           "usd": round(sum((x.get("usd") or 0) for x in runs), 3),
           "search_min": round(sum((x.get("elapsed_s") or 0) for x in runs) / 60, 1),
           "sources_used": len({x["source"] for x in runs if x.get("status") == 200}),
           "requests": len(runs), "overall": {"аналитик": a.get("overall_grade"),
                                              **{"критик: " + {"fidelity": "верность", "completeness": "полнота"}.get(c.get("lens"), "?"): c.get("overall_grade")
                                                 for c in analysis.get("critics", [])}},
           "headline": a.get("headline", "")}
    _write(rd / "posts.json", json.dumps({"meta": meta, "posts": posts}, ensure_ascii=False, indent=1))
    _write(rd / "sources.json", json.dumps({"meta": meta, "total": tot, "sources": srcs, "runs": runs}, ensure_ascii=False, indent=1))
    if op:
        tot["opinions"] = {"options": len(op["options"]), "chosen": sum(1 for o in op["options"] if o["n_main"]),
                           "cost": op.get("cost", {}).get("usd"), "leader": (by_main(op["options"]) or [{}])[0].get("canon")}
    _write(rd / "report.html", page(meta, tot, srcs, posts, runs, report, op))
    print("готово:", rd / "report.html", f"· постов {tot['posts']}, по делу {tot['rel2']}, авторов {tot['authors_rel2']}, ${tot['usd']}")


def by_main(rows):
    return sorted((r for r in rows if r["n_main"]), key=lambda r: (-r["n_main"], -(r["n_pro"] - r["n_contra"]), -r["n_authors"]))


def month_range(rows):
    ms = sorted({m for r in rows for m in (r.get("by_month") or {})})
    return ms[-9:]  # до 9 последних месяцев окна


def trend_of(r):
    if r.get("by_month") is not None:
        return r["by_month"]
    bm = collections.defaultdict(lambda: [set(), set(), set()])  # старые прогоны: считаем по постам варианта
    for p in r.get("posts", []):
        if p.get("date") and not p.get("seller"):
            b = bm[p["date"][:7]]
            b[0].add(p["author"])
            if p["stance"] in ("pro", "mixed"):
                b[1].add(p["author"])
            if p["stance"] in ("contra", "mixed"):
                b[2].add(p["author"])
    r["by_month"] = {k: [len(v[0]), len(v[1]), len(v[2])] for k, v in sorted(bm.items())}
    return r["by_month"]


def opinion_row(i, r, months, top_main, top_auth):
    bm = trend_of(r)
    mx = max([v[0] for v in bm.values()] or [1]) or 1
    spark = "".join(f"<i class='{'z' if not bm.get(m) else ''}' style='height:{max(4, 26 * (bm.get(m, [0])[0]) / mx):.0f}px' "
                    f"title='{m}: авторов {bm.get(m, [0, 0, 0])[0]}, хвалят {bm.get(m, [0, 0, 0])[1]}, ругают {bm.get(m, [0, 0, 0])[2]}'></i>"
                    for m in months)
    tot = max(r["n_pro"] + r["n_contra"], 1)
    pc = (f"<div class='pc'><span class='p' style='width:{100 * r['n_pro'] / tot:.0f}%'></span>"
          f"<span class='c' style='width:{100 * r['n_contra'] / tot:.0f}%'></span></div>") if (r["n_pro"] or r["n_contra"]) else "<div class='pc'></div>"
    quotes = "".join(
        f"<blockquote><span class='st st-{E(qt['stance'])}'>{ {'pro': 'хвалит', 'contra': 'ругает'}.get(qt['stance'], 'нейтрально') }</span> "
        f"«{E(qt['quote'])}»{('<div><b>Почему:</b> ' + E(qt['why']) + '</div>') if qt.get('why') else ''}"
        f"<cite><a href='{E(qt['url'])}' target='_blank' rel='noopener'>{E(qt['platform'])} · {E(qt.get('date') or '')}</a></cite></blockquote>"
        for qt in r.get("quotes", [])[:6])
    seen, plinks = set(), []
    for p in sorted(r.get("posts", []), key=lambda p: (p.get("seller", False), not p.get("main"), p.get("stance") == "neutral")):
        if p["url"] in seen:
            continue
        seen.add(p["url"])
        mark = {"pro": "<span class=grn>+</span>", "contra": "<span class=red>−</span>", "mixed": "±"}.get(p["stance"], "·")
        plinks.append(f"<li>{mark} <a href='{E(p['url'])}' target='_blank' rel='noopener'>{E(p['url'][:90])}</a> "
                      f"<span class='mut'>{E(p.get('date') or '')}{' · выбирает' if p.get('main') else ''}{' · свой опыт' if p.get('first_hand') else ''}{' · продавец' if p.get('seller') else ''}</span></li>")
    names = ", ".join(E(n) for n in r.get("names", []) if n != r["canon"])
    return (f"<tr data-kind='{E(r['kind'])}' data-t='{E((r['canon'] + ' ' + ' '.join(r.get('names', []))).lower())}'>"
            f"<td class='n'>{i}</td><td><b>{E(r['canon'])}</b><div class='mut'>{E(r['kind'])}{(' · впервые ' + E(r['first_seen'])) if r.get('first_seen') else ''}</div></td>"
            f"<td><div class='bar'><span style='width:{100 * r['n_main'] / top_main:.0f}%'></span></div><span class='n'>{r['n_main']}</span></td>"
            f"<td>{pc}<span class='grn'>{r['n_pro']}</span> / <span class='red'>{r['n_contra']}</span></td>"
            f"<td class='n'>{r['n_first_hand']}</td>"
            f"<td><div class='bar'><span style='width:{100 * r['n_authors'] / top_auth:.0f}%'></span></div><span class='n'>{r['n_authors']}</span>"
            f"{(' <span class=mut>(продавцов ' + str(r['n_sellers']) + ')</span>') if r.get('n_sellers') else ''}</td>"
            f"<td><div class='spark'>{spark}</div></td>"
            f"<td><details><summary>почему и где</summary>{('<div class=mut>Другие названия: ' + names + '</div>') if names else ''}"
            f"{quotes or '<p class=mut>дословных цитат нет (вариант ниже 30-го места)</p>'}"
            f"<p><b>Посты ({len(plinks)}):</b></p><ul>{''.join(plinks[:40])}</ul></details></td></tr>")


def opinions_html(op, rest):
    rows = op["options"]
    if not rows:
        return ""
    months = month_range([{"by_month": trend_of(r)} for r in rows])
    top = by_main(rows)
    tm = max([r["n_main"] for r in rows] or [1]) or 1
    ta = max([r["n_authors"] for r in rows] or [1]) or 1
    mhead = " ".join(m[2:] for m in months)
    head = (f"<thead><tr><th class='n'>#</th><th>Вариант</th><th>Выбирают</th><th>Хвалят / ругают</th><th class='n'>Свой опыт</th>"
            f"<th>Упомянули</th><th>По месяцам<div class='mut'>{E(months[0] if months else '')} → {E(months[-1] if months else '')}</div></th><th></th></tr></thead>")
    t1 = "".join(opinion_row(i, r, months, tm, ta) for i, r in enumerate(top, 1))
    t2 = "".join(opinion_row(i, r, months, tm, ta) for i, r in enumerate(rows, 1))
    contra = sorted((r for r in rows if r["n_contra"]), key=lambda r: (-r["n_contra"], -r["n_authors"]))[:12]
    t3 = "".join(opinion_row(i, r, months, tm, ta) for i, r in enumerate(contra, 1))
    kinds = collections.Counter(r["kind"] for r in rows)
    chips = "".join(f"<button class='chip' data-ok='{E(k)}'>{E(k)} · {n}</button>" for k, n in kinds.most_common())
    fb = op.get("fallback") or {}
    return f"""<div class="warn">{md("**Как читать рейтинги.** Все числа — **разные авторы**, не посты. «Выбирают» — автор называет вариант лучшим, своим выбором или главным советом. «Хвалят / ругают» — отношение автора (зелёное / красное). «Свой опыт» — пробовал сам, а не пересказ. Продавцы своего продукта в «выбирают» и «хвалят» не считаются. «По месяцам» — сколько разных авторов писали о варианте в каждый месяц окна (наведи на столбик). Варианты из постов выписали два сводчика (" + E(' + '.join(m.split('/')[-1] for m in op.get('extractors', []))) + "), синонимы свела модель (" + E(op.get('writer', '').split('/')[-1]) + "), цитаты сверены с текстом постов.")}</div>
<h2>Рейтинг: что выбирают люди</h2>
<div class="mut">Варианты, которые хоть один автор назвал лучшим / своим выбором ({len(top)} из {len(rows)}): по «выбирают», при равенстве — по балансу «хвалят − ругают».</div>
<div class="wrap"><table>{head}<tbody>{t1}</tbody></table></div>
<h2>Сводка мнений</h2><div class="report">{md(rest)}</div>
<h2>Что ругают</h2><div class="mut">Варианты с отрицательными отзывами — по числу ругающих авторов.</div>
<div class="wrap"><table>{head}<tbody>{t3}</tbody></table></div>
<h2>Все варианты по упоминаниям</h2>
<p><input id="oq" placeholder="поиск по вариантам"></p><div><button class="chip on" data-ok="*">все виды · {len(rows)}</button>{chips}</div>
<div class="wrap"><table id="ot">{head}<tbody>{t2}</tbody></table></div>
<div class="mut">Постов в сводке: {op.get('posts_used')} от {op.get('authors')} авторов · не разобрано сводчиками: {op.get('unparsed')} · запасной сводчик: {E(json.dumps(fb, ensure_ascii=False))} · сводка ${op.get('cost', {}).get('usd')} за {op.get('seconds')} с</div>
<script>
let ok='*';const oq=document.getElementById('oq');
function of(){{const s=oq.value.toLowerCase();document.querySelectorAll('#ot tbody tr').forEach(r=>r.style.display=((ok=='*'||r.dataset.kind==ok)&&(!s||r.dataset.t.includes(s)))?'':'none')}}
document.querySelectorAll('.chip[data-ok]').forEach(b=>b.onclick=()=>{{document.querySelectorAll('.chip[data-ok]').forEach(c=>c.classList.remove('on'));b.classList.add('on');ok=b.dataset.ok;of()}});
oq.oninput=of;
</script>"""


def page(meta, tot, srcs, posts, runs, report, op=None):
    main, rest = split_report(report)
    op_main, op_rest = split_report(op["summary"]) if op else ("", "")
    if op_main:
        main = op_main
    top = max((s["rel2"] for s in srcs), default=1) or 1
    grade_cols = sorted({k for s in srcs for k in s["grades"]})
    srows = "".join(
        f"<tr><td><b>{E(s['source'])}</b><div class='mut'>{E(s['about'])}</div>"
        f"{('<div class=err>' + E('; '.join(str(x)[:90] for x in s['errors'][:2])) + '</div>') if s['errors'] else ''}</td>"
        f"<td class='n'>{s['found']}</td><td class='n'>{s['rel_ge1']}</td>"
        f"<td><div class='bar'><span style='width:{100 * s['rel2'] / top:.0f}%'></span></div><span class='n'>{s['rel2']}</span></td>"
        f"<td class='n'>{s['authors_rel2']}</td><td class='n'>{'' if s['ru_share_rel'] is None else round(100 * s['ru_share_rel'])}{'' if s['ru_share_rel'] is None else ' %'}</td>"
        f"<td class='n'>{E(s['median_date_rel2'] or '—')}</td><td class='n'>{s['credits']}<div class='mut'>${s['usd']}</div></td>"
        + "".join(f"<td class='n'>{s['grades'].get(c, '')}</td>" for c in grade_cols) + "</tr>"
        for s in srcs)
    gh = "".join(f"<th class='n'>{E(c)}</th>" for c in grade_cols)
    kinds = collections.Counter(p["kind"] for p in posts if p["kind"])
    psrcs = collections.Counter(p["source"] for p in posts)
    order = sorted(posts, key=lambda p: (-(p["rel"] if p["rel"] is not None else -1), p["date"] or ""), reverse=False)
    order = sorted(order, key=lambda p: -(p["rel"] if p["rel"] is not None else -1))
    prow = []
    for p in order:
        rel = p["rel"]
        met = " · ".join(f"{k} {v}" for k, v in p["metrics"].items() if v not in (None, ""))
        prow.append(
            f"<details class='post' data-rel='{'' if rel is None else rel}' data-src='{E(p['source'])}' data-kind='{E(p['kind'] or '')}' "
            f"data-lang='{E(p['lang'] or '')}' data-t='{E((p['gist'] + ' ' + p['text'][:600]).lower())}'>"
            f"<summary><span class='rel r{'' if rel is None else rel}'>{REL_NAME.get(rel, '?')}</span> "
            f"<span class='m'>{E(p['source'])}</span> · {E(p['date'] or 'без даты')} · {E(KIND_RU.get(p['kind'], p['kind'] or ''))}"
            f"{' · свой опыт' if p['first_hand'] else ''}{' · <b>продавец</b>' if p.get('seller') else ''} — {E(p['gist'] or p['text'][:160])}</summary>"
            f"<p><a href='{E(p['url'] or '')}' target='_blank' rel='noopener'>{E(p['url'] or '')}</a></p>"
            f"<pre>{E(p['text'][:3000])}</pre><div class='mut'>{E(met)}{' · нашли: ' + E(', '.join(p['found_by'])) if p['found_by'] else ''}</div></details>")
    rrows = "".join(
        f"<tr><td class='n'>{x.get('run')}</td><td class='m'>{E(x.get('source', ''))}</td><td class='m'>{E(str(x.get('query', ''))[:160])}</td>"
        f"<td class='n'>{E(str(x.get('status')))}</td><td class='n'>{x.get('n', '') if x.get('n') is not None else ''}</td>"
        f"<td class='n'>{x.get('new_unique', '')}</td><td class='n'>{x.get('credits', '') if x.get('credits') is not None else ''}</td></tr>"
        for x in runs)
    chips_rel = "".join(f"<button class='chip' data-f='rel' data-v='{v}'>{n} · {sum(1 for p in posts if p['rel'] == v)}</button>"
                        for v, n in ((2, "по делу"), (1, "по теме"), (0, "шум")))
    chips_src = "".join(f"<button class='chip' data-f='src' data-v='{E(s)}'>{E(s)} · {n}</button>" for s, n in psrcs.most_common())
    chips_kind = "".join(f"<button class='chip' data-f='kind' data-v='{E(k)}'>{E(KIND_RU.get(k, k))} · {n}</button>" for k, n in kinds.most_common())
    ov = " · ".join(f"{E(k)} {v}" for k, v in tot["overall"].items() if v is not None)
    ru = "—" if tot["ru_share_rel"] is None else f"{round(100 * tot['ru_share_rel'])} %"
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Мнения из соцсетей</title><style>
:root{{--bg:#fff;--fg:#1a202c;--mut:#718096;--line:#e2e8f0;--card:#f7fafc;--acc:#3182ce;--ok:#1f9d55;--mid:#d69e2e;--bad:#a0aec0}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111418;--fg:#e2e8f0;--mut:#a0aec0;--line:#2d3748;--card:#1a1f26;--acc:#63b3ed;--bad:#4a5568}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,system-ui,sans-serif;padding:16px;max-width:1200px;margin:auto}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px}} .mut{{color:var(--mut);font-size:12px}} .err{{color:#c53030;font-size:12px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:14px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px}} .card b{{font-size:22px;display:block}}
table{{width:100%;border-collapse:collapse}} th,td{{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}}
.n{{text-align:right;white-space:nowrap}} .m{{font-family:ui-monospace,monospace;font-size:12px}} .wrap{{overflow-x:auto}}
.bar{{height:7px;background:var(--line);border-radius:4px;min-width:80px;margin:3px 0}} .bar span{{display:block;height:7px;border-radius:4px;background:var(--acc)}}
blockquote{{margin:6px 0;padding:6px 10px;border-left:3px solid var(--acc);background:var(--card)}} cite{{display:block;color:var(--mut);font-size:12px;font-style:normal}}
.warn{{background:var(--card);border-left:4px solid var(--mid);padding:8px 12px;margin:10px 0}} .sum{{background:var(--card);border-radius:8px;padding:4px 14px;margin:10px 0}}
.report table{{font-size:13px}} .report h2{{font-size:15px}} .report h3{{font-size:14px}} a{{color:var(--acc);word-break:break-all}}
.chip{{border:1px solid var(--acc);color:var(--acc);background:transparent;border-radius:14px;padding:3px 10px;cursor:pointer;margin:0 4px 6px 0;font-size:12px}}
.chip.on{{background:var(--acc);color:#fff}} input{{padding:6px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);width:min(320px,100%)}}
pre{{white-space:pre-wrap;background:var(--card);padding:8px;border-radius:6px;max-height:320px;overflow:auto;font:13px/1.45 -apple-system,system-ui,sans-serif}}
.rel{{border-radius:4px;padding:0 6px;color:#fff;font-size:12px;background:var(--bad)}} .r2{{background:var(--ok)}} .r1{{background:var(--mid)}}
details.post{{border-bottom:1px solid var(--line);padding:5px 0}} summary{{cursor:pointer}}
.pc{{display:flex;height:9px;border-radius:4px;overflow:hidden;background:var(--line);min-width:90px;margin:3px 0}} .pc .p{{background:var(--ok)}} .pc .c{{background:#e53e3e}}
.spark{{display:flex;align-items:flex-end;gap:2px;height:26px}} .spark i{{display:block;width:7px;background:var(--acc);border-radius:2px 2px 0 0;min-height:1px}} .spark i.z{{background:var(--line)}}
.st{{border-radius:4px;padding:0 5px;color:#fff;font-size:11px}} .st-pro{{background:var(--ok)}} .st-contra{{background:#e53e3e}} .st-neutral{{background:var(--bad)}}
.grn{{color:var(--ok)}} .red{{color:#e53e3e}}
</style></head><body>
<h1>Мнения из соцсетей: «{E(meta['question'])}»</h1>
<div class="mut">период с {E(meta.get('since') or '—')} · {E(meta.get('created', ''))} · заказ: {E(meta.get('ordered_by') or '—')} · {tot['requests']} запросов по {tot['sources_used']} источникам · API ${tot['usd']} (≈ {round(tot['usd'] * RUB)} ₽) · поиск {tot['search_min']} мин</div>
<div class="cards">
<div class="card">Постов найдено<b>{tot['posts']}</b><span class="mut">после удаления дублей</span></div>
<div class="card">По теме<b>{tot['rel_ge1']}</b></div>
<div class="card">Прямо по делу<b>{tot['rel2']}</b></div>
<div class="card">Разных авторов<b>{tot['authors_rel2']}</b><span class="mut">среди «по делу»</span></div>
<div class="card">По-русски<b>{ru}</b><span class="mut">среди «по теме»</span></div>
<div class="card">Цена API<b>{round(tot['usd'] * RUB)} ₽</b><span class="mut">${tot['usd']}{(' · сводка мнений $' + str(tot['opinions']['cost'])) if tot.get('opinions') else ''}</span></div>
{('<div class="card">Вариантов<b>' + str(tot['opinions']['options']) + '</b><span class="mut">выбирают хоть кто-то: ' + str(tot['opinions']['chosen']) + '</span></div>') if tot.get('opinions') else ''}
<div class="card">Оценка ответа<b>{E(str(tot['overall'].get('аналитик') or '—'))}<span class="mut"> /10</span></b><span class="mut">{ov}</span></div>
</div>
{('<div class="warn">Не разобрано моделью: ' + str(tot['unmarked']) + ' постов — их нет в выводах.</div>') if tot['unmarked'] else ''}
<h2>Итог</h2><div class="sum">{md(main) if main else '<p>' + E(tot['headline']) + '</p>'}</div>
<div class="warn">{md(HOW_TO_READ)}</div>
{opinions_html(op, op_rest) if op else ''}
<h2>Источники</h2>
<div class="mut">Найдено — сколько постов нашли запросы источника (пост, найденный двумя, засчитан обоим); по делу — с полоской; авторов — разных среди «по делу»; оценки 0–10 — аналитик и два критика независимо.</div>
<div class="wrap"><table><thead><tr><th>Источник</th><th class="n">Найдено</th><th class="n">По теме</th><th>По делу</th><th class="n">Авторов</th><th class="n">Рус.</th><th class="n">Медиана даты</th><th class="n">Кредиты</th>{gh}</tr></thead><tbody>{srows}</tbody></table></div>
{('<h2>Разбор аналитика</h2><details><summary>подробный отчёт: что пишут по видам мнений, со ссылками, и что исправили критики</summary><div class="report">' + md(rest) + '</div></details>') if op else ('<h2>Отчёт</h2><div class="report">' + md(rest) + '</div>')}
<h2>Все посты</h2>
<p><input id="q" placeholder="поиск по постам"></p>
<div><button class="chip on" data-f="rel" data-v="*">все · {len(posts)}</button>{chips_rel}</div>
<div><button class="chip on" data-f="src" data-v="*">все источники</button>{chips_src}</div>
<div><button class="chip on" data-f="kind" data-v="*">все виды</button>{chips_kind}</div>
<div id="posts">{''.join(prow)}</div>
<h2>Запросы</h2><div class="mut">Что и где искали: запросы пишет модель под каждую площадку (синонимы, падежи через OR, операторы).</div>
<div class="wrap"><table><thead><tr><th class="n">#</th><th>Источник</th><th>Запрос</th><th class="n">Ответ</th><th class="n">Найдено</th><th class="n">Новых</th><th class="n">Кредиты</th></tr></thead><tbody>{rrows}</tbody></table></div>
<script>
const F={{rel:'*',src:'*',kind:'*'}};const q=document.getElementById('q');
function f(){{const s=q.value.toLowerCase();document.querySelectorAll('#posts details').forEach(d=>{{
const ok=(F.rel=='*'||d.dataset.rel==F.rel)&&(F.src=='*'||d.dataset.src==F.src)&&(F.kind=='*'||d.dataset.kind==F.kind)&&(!s||d.dataset.t.includes(s));
d.style.display=ok?'':'none'}})}}
document.querySelectorAll('.chip[data-f]').forEach(b=>b.onclick=()=>{{const k=b.dataset.f;
document.querySelectorAll('.chip[data-f='+k+']').forEach(c=>c.classList.remove('on'));b.classList.add('on');F[k]=b.dataset.v;f()}});
q.oninput=f;
</script></body></html>"""


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__)
    build(sys.argv[1])
