#!/usr/bin/env python3
"""Исполнитель навыка social-research: план запросов (JSON, его пишет модель) → поиск с пределом кредитов → дедуп →
compact.jsonl + чанки по 40 постов для разметки моделью (текст до 2 500 знаков: начало 2 000 + конец 500, подсказки
поставщика — scripts/_compact.py). Следующий шаг — scripts/expand.py (комментарии веток, полный текст страниц).

X по месяцам: строка плана [sc_x|tw_x, запрос, lang, N, sort] при окне > 7 дней (и без since_time: в запросе) сама
разворачивается в Top + Latest на каждый календарный месяц окна (since_time:/until_time: в запросе); на месяц и
сортировку — max(20, ⌈N / (2·месяцев)⌉ до кратного 20) постов. Иначе «за полгода» = последние 4 дня.

План: {"question": "...", "since": "YYYY-MM-DD", "runs": [[source, query, lang|null, limit, sort|null], ...]}
Запуск: python3 scripts/run_plan.py plan.json --tag 2026-11-10-tts --ordered-by "кто заказал и зачем" [--max sc=150 ...]
Итог: runs/<tag>/{meta,plan}.json + runs/<tag>/work/{all,compact}.jsonl, chunk_NN.jsonl, runs.jsonl;
сырые ответы API — data/raw/<tag>/ (не в git); строка «было → стало» в конце вывода.
Предел: перед каждым запросом проверяется, что уже потраченное по сервису + запас на этот запрос ≤ предела;
иначе запрос пропускается (пишется в runs.jsonl как skipped_budget)."""
import argparse, datetime as dt, json, math, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from social import search, catalog, ROOT, RAW  # noqa: E402
from _compact import norm_url, write_compact, unify_x_authors  # noqa: E402,F401  norm_url — для старых импортов

CAT = catalog()
X_SLICED = ("sc_x", "tw_x")


def service(src):
    return CAT[src]["service"]


def reserve(src, limit):
    """Запас кредитов на вызов (проверка предела ДО запроса): по цене страницы, где она известна, иначе из каталога."""
    if src == "sc_x":
        return max(1, math.ceil(limit / 20))
    if src == "sc_reddit":
        return max(1, math.ceil(limit / 25))
    if src == "tw_x":
        return 300 * max(1, math.ceil(limit / 20))  # 15 кр за твит, страница 20
    return CAT[src]["reserve"]


def month_windows(since, now=None):
    """Окно [since, сейчас) → календарные месяцы: [(since_epoch, until_epoch | None, 'ГГГГ-ММ')]."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cur = dt.datetime.strptime(since[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    out = []
    while cur < now:
        nxt = (cur.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        out.append((int(cur.timestamp()), int(nxt.timestamp()) if nxt < now else None, cur.strftime("%Y-%m")))
        cur = nxt
    return out


LEVELS = ("каждый месяц Top + Latest", "свежий месяц Top + Latest, старые — только Top",
          "окна по 2 месяца: свежее Top + Latest, старые — Top", "окна по 3 месяца: свежее Top + Latest, старые — Top",
          "одно окно на весь период: Top + Latest")


def _windows(since, level):
    """Окна нарезки от НОВЫХ к старым (бюджет кончается на старых, а не на свежих). level 2/3 — окна по 2/3 месяца,
    level 4 — одно окно на весь период."""
    wins = month_windows(since)[::-1]
    k = {2: 2, 3: 3, 4: len(wins)}.get(level, 1)
    out = []
    for j in range(0, len(wins), k):
        g = wins[j:j + k]  # g[0] — самый новый месяц группы
        out.append((g[-1][0], g[0][1], g[-1][2] if len(g) == 1 else f"{g[-1][2]}…{g[0][2]}"))
    return out


def expand_rows(P, level=None):
    """Строки плана → вызовы: (номер строки плана, source, query, lang, limit, sort, срез | None).
    X при окне > 7 дней — срезы от новых к старым; level — {сервис: 0–4}, насколько ужата нарезка под бюджет (LEVELS)."""
    level = level or {}
    since = P.get("since")
    days = (dt.datetime.now(dt.timezone.utc) - dt.datetime.strptime(since[:10], "%Y-%m-%d").replace(
        tzinfo=dt.timezone.utc)).days if since else 0
    out = []
    for i, (src, q, lang, limit, sort) in enumerate(P["runs"]):
        if src in X_SLICED and days > 7 and "since_time:" not in q and not P.get("no_month_slices"):
            lv = level.get(service(src), 0)
            wins = _windows(since, lv)
            per = max(20, 20 * math.ceil(math.ceil(limit / (2 * len(wins))) / 20))
            for n, (s, e, label) in enumerate(wins):
                qq = f"{q} since_time:{s}" + (f" until_time:{e}" if e else "")
                for srt in (("top", "latest") if lv == 0 or n == 0 else ("top",)):
                    out.append((i, src, qq, lang, per, srt, f"{label} {srt}"))
        else:
            out.append((i, src, q, lang, limit, sort, None))
    return out


def plan_cost(rows):
    cost = {}
    for _, src, _, _, limit, _, _ in rows:
        if src in CAT:
            cost[service(src)] = cost.get(service(src), 0) + reserve(src, limit)
    return cost


def fit_plan(P, cap):
    """Для каждого сервиса с X — наименьшее ужатие нарезки, при котором оценка расхода плана по этому сервису
    укладывается в предел. Не уложилась и на последнем уровне — берётся он, хвост уйдёт в skipped_budget
    (предупреждение печатается до первого платного вызова). → (rows, {сервис: уровень}, оценка, уложились ли)."""
    level, fits = {}, True
    for sv in sorted({service(r[0]) for r in P["runs"] if r[0] in X_SLICED and r[0] in CAT}):
        for lv in range(len(LEVELS)):
            level[sv] = lv
            if plan_cost(expand_rows(P, level)).get(sv, 0) <= cap.get(sv, 0):
                break
        else:
            fits = False
    rows = expand_rows(P, level)
    cost = plan_cost(rows)
    return rows, level, cost, fits and all(cost.get(sv, 0) <= cap.get(sv, 0) for sv in cost)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--ordered-by", default="", help="кто заказал и чьё решение")
    ap.add_argument("--max-sc", type=int, default=150)
    ap.add_argument("--max-unif", type=int, default=300)
    ap.add_argument("--max-tw", type=int, default=6000)
    ap.add_argument("--max", action="append", default=[], metavar="SERVICE=N",
                    help="предел для любого сервиса, например --max vk=0 --max yandex=30 --max tgstat=20")
    a = ap.parse_args()
    P = json.loads(Path(a.plan).read_text())
    cap = {"sc": a.max_sc, "unif": a.max_unif, "tw": a.max_tw, "hn": 10**9}
    for kv in a.max:
        k, v = kv.split("=", 1)
        cap[k] = int(v)
    spent = {}
    first_bal, last_bal = {}, {}
    run_dir = ROOT / "runs" / a.tag
    out = run_dir / "work"
    out.mkdir(parents=True, exist_ok=True)
    for stale in ("expand_state.json", "runs.jsonl"):  # новый поиск под тем же --tag — добор и журнал заново
        if (out / stale).exists():
            (out / stale).unlink()
    (run_dir / "plan.json").write_text(json.dumps(P, ensure_ascii=False, indent=1))
    (run_dir / "meta.json").write_text(json.dumps({"question": P["question"], "since": P.get("since"), "tag": a.tag,
                                                   "created": time.strftime("%Y-%m-%d %H:%M"), "ordered_by": a.ordered_by,
                                                   "raw_dir": f"data/raw/{a.tag}", "budget": cap}, ensure_ascii=False, indent=1))
    seen, allrecs, t0, tw_seen = {}, [], time.time(), []
    runs_f = (out / "runs.jsonl").open("a")
    rows_plan, level, est, fits = fit_plan(P, cap)
    nokey = sorted({src for _, src, *_ in rows_plan  # без ключа и с ненулевым пределом (с нулевым строка и так пропустится)
                    if src in CAT and not CAT[src]["key_ok"] and cap.get(CAT[src]["service"], 0) > 0})
    if nokey:  # до первого платного вызова: без ключа строка ушла бы в пустой прогон
        print("НЕТ КЛЮЧА для источников: " + ", ".join(nokey) + " — положите ключ в папку ключей (scripts/_keys.py, "
              "образцы — keys.example/) или уберите эти строки из плана; `python3 scripts/social.py --list` покажет, "
              "что готово.", flush=True)
        sys.exit(2)
    if any(level.values()) or not fits:
        warn = {"оценка": est, "предел": {k: cap.get(k, 0) for k in est},
                "нарезка X": {sv: LEVELS[lv] for sv, lv in level.items()}, "уложились": fits}
        print("ВНИМАНИЕ, бюджет: " + json.dumps(warn, ensure_ascii=False), flush=True)
    for i, src, q, lang, limit, sort, sl in rows_plan:
        if src not in CAT:
            print(json.dumps({"run": i, "source": src, "status": "unknown_source"}, ensure_ascii=False), flush=True)
            continue
        sv = service(src)
        spent.setdefault(sv, 0)
        if spent[sv] + reserve(src, limit) > cap.get(sv, 0):  # нет предела для сервиса → 0, запрос пропускается
            row = {"run": i, "source": src, "query": q, "slice": sl, "status": "skipped_budget", "spent": spent[sv],
                   "cap": cap.get(sv, 0)}
            print(json.dumps(row, ensure_ascii=False), flush=True)
            runs_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            continue
        recs, meta = search(src, q, since=P.get("since"), lang=lang, limit=limit, tag=a.tag, sort=sort,
                            context=P["question"] if src == "sc_everywhere" else None)
        spent[sv] += meta.get("credits") or 0
        if meta.get("balance_after") is not None:
            first_bal.setdefault(sv, (meta["balance_after"] + (meta.get("credits") or 0)))
            last_bal[sv] = meta["balance_after"]
        new = 0
        tw_seen += [r for r in recs if r.get("source") == "tw_x"]
        for r in recs:
            u = norm_url(r.get("url"))
            if not u:
                continue
            if u in seen:
                seen[u]["found_by"].append(f"{src}#{i}")
                continue
            r["url_norm"], r["found_by"] = u, [f"{src}#{i}"]
            seen[u] = r
            allrecs.append(r)
            new += 1
        row = {"run": i, "source": src, "query": q, "slice": sl, "status": meta.get("status"), "n": meta.get("n"), "new_unique": new,
               "credits": meta.get("credits"), "usd": meta.get("usd"), "elapsed_s": meta.get("elapsed_s"),
               "error": str(meta.get("error"))[:200] if meta.get("error") else None}
        print(json.dumps(row, ensure_ascii=False), flush=True)
        runs_f.write(json.dumps(row, ensure_ascii=False) + "\n")
    runs_f.close()
    unify_x_authors(allrecs, tw_seen)
    with (out / "all.jsonl").open("w") as f:
        for r in allrecs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    rows, chunks = write_compact(out, allrecs)
    m = json.loads((run_dir / "meta.json").read_text())
    m["spent"] = spent
    m["balance"] = {k: [first_bal[k], last_bal[k]] for k in last_bal}
    (run_dir / "meta.json").write_text(json.dumps(m, ensure_ascii=False, indent=1))
    print(json.dumps({"tag": a.tag, "run_dir": str(run_dir), "compact": str(out / "compact.jsonl"), "unique": len(rows), "chunks": chunks, "spent": spent,
                      "balance": {k: f"{first_bal[k]} → {last_bal[k]}" for k in last_bal},
                      "minutes": round((time.time() - t0) / 60, 1)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
