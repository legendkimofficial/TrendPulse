"""Comparison experiments v0.2: what does saving memory cost?

    python experiments/run_experiments.py           # full run (~6 min)
    python experiments/run_experiments.py --quick   # first 60k articles, fewer repeats
    python experiments/run_experiments.py --figures-only

Outputs  experiments/results/results.json, summary.md, casestudies.md, figures/*.png

E0  data quality after cleaning
E1  decaying window, pruning threshold theta (c = 1e-3):
    logical size (#keywords) AND Python object bytes, Top-K agreement (overall,
    worst case, per year), score error, Top-K entry events (precision/recall,
    early/late split, |offset| distribution)
E2  decay constant c (theta = 0.5): agreement with the exact baseline AND
    temporal stability of the ranking itself (a different quantity)
E3  DGIM vs efficient exact window count: N x r; error by count band,
    logical bits AND Python object bytes, Top-10 agreement
E4  throughput with matched work: update-only, query-only, engine, API
    (median of repeated runs)
E5  sensitivity to the arbitrary order of articles within a day
E6  case studies: 16 pre-selected real events -> when does the keyword enter
    the Top 20 (exact vs approximate), with representative headlines

Measurement notes are written into summary.md next to every table.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import statistics
import sys
import time
from collections import Counter, deque
from datetime import date as Date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trendpulse.data import Article, load_stream, quality_report  # noqa: E402
from trendpulse.decaying import DecayingWindow  # noqa: E402
from trendpulse.dgim import DGIM  # noqa: E402
from trendpulse.memory import deep_sizeof  # noqa: E402
from trendpulse.sliding import ExactWindowCount  # noqa: E402

OUT = ROOT / "experiments" / "results"
FIG = OUT / "figures"

SNAP = 100           # articles between snapshots (E1/E2)
K = 20
NEW_GAP = 20         # snapshots (= 2,000 articles) outside Top K before a return counts as re-entry
MATCH_TOL = 1000     # articles: an approximate event matches an exact one within +-1/c articles
MEM_EVERY = 20_000   # articles between Python-memory samples
THETAS = [0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9]
CS = [5e-4, 1e-3, 2e-3, 5e-3]
DGIM_NS = [500, 1000, 5000]
DGIM_RS = [2, 3, 5, 9]
DGIM_EVAL = 500
REPEATS = 5

# E6: chosen before looking at results. Success = keyword enters the approximate
# Top 20 between the event date and 7 days after it.
EVENTS = [
    ("2012-10-29", "sandy", "허리케인 샌디 상륙"),
    ("2012-12-14", "newtown", "샌디훅 초등학교 총격"),
    ("2013-04-15", "boston", "보스턴 마라톤 폭탄 테러"),
    ("2013-07-13", "zimmerman", "짐머먼 무죄 평결"),
    ("2014-03-08", "malaysia", "말레이시아항공 MH370 실종"),
    ("2014-08-09", "ferguson", "퍼거슨 사건"),
    ("2014-09-30", "ebola", "미국 첫 에볼라 확진"),
    ("2015-06-17", "charleston", "찰스턴 교회 총격"),
    ("2016-02-13", "scalia", "스캘리아 대법관 사망"),
    ("2016-06-12", "orlando", "올랜도 나이트클럽 총격"),
    ("2016-06-23", "brexit", "브렉시트 국민투표"),
    ("2017-08-12", "charlottesville", "샬러츠빌 집회 폭력"),
    ("2018-02-14", "parkland", "파크랜드 고교 총격"),
    ("2020-03-11", "coronavirus", "WHO 팬데믹 선언"),
    ("2020-05-25", "floyd", "조지 플로이드 사망"),
    ("2022-02-24", "ukraine", "러시아의 우크라이나 침공"),
]
EVENT_BEFORE, EVENT_AFTER, SUCCESS_DAYS = 7, 14, 7

# Human-written cause notes for failed cases (checked against the data on 2026-11-08,
# see PROGRESS.md). Printed verbatim in casestudies.md, marked as manual analysis.
FAILURE_NOTES = {
    "malaysia": "데이터 범위 문제: 사건 후 14일간 제목에 'malaysia' 1건, 'flight' 5건뿐. "
                "HuffPost 이 데이터셋에는 MH370 보도가 거의 없다(편명 'MH370'은 숫자 포함 토큰이라 제거되기도 함).",
    "parkland": "키워드 표현 문제: 기사들은 사건을 주로 'florida'(56건)·'shooting'(57건)으로 불렀고, "
                "두 단어는 사건 다음 날(2018-02-15) Top 20에 진입했다. 'parkland'는 17건. → 바이그램·개체명 처리 필요.",
    "floyd": "희소 구간 문제: 2020년은 하루 약 6건이라 감쇠 대표 규모 1,000건이 약 6개월에 해당한다. "
             "14일간 'floyd' 4건. 'police'는 +4일에 Top 20 진입. → 날짜 기준 감쇠 비교 필요.",
}


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = min(len(xs) - 1, int(round(q * (len(xs) - 1))))
    return xs[i]


def med_range(xs):
    return {"median": statistics.median(xs), "min": min(xs), "max": max(xs), "runs": len(xs)}


def inversions(reference: list[str], other_rank: dict[str, int]) -> int:
    common = [w for w in reference if w in other_rank]
    inv = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            if other_rank[common[i]] > other_rank[common[j]]:
                inv += 1
    return inv


def topk_events(snapshots: list[list[str]], start: int, gap: int = NEW_GAP) -> list[tuple[int, str, str]]:
    """Same rule as the engine: (snapshot, keyword, 'first'|'reentry'); snapshots < start only prime state."""
    last: dict[str, int] = {}
    events = []
    for s, top in enumerate(snapshots):
        for w in top:
            prev = last.get(w)
            if s >= start:
                if prev is None:
                    events.append((s, w, "first"))
                elif s - prev > gap:
                    events.append((s, w, "reentry"))
            last[w] = s
    return events


def match_events(eb, ep, tol_snaps):
    """Greedy nearest matching per keyword. Returns signed offsets (approx - exact) in snapshots."""
    by_word: dict[str, list[int]] = {}
    for s, w, _ in ep:
        by_word.setdefault(w, []).append(s)
    used: set[tuple[int, str]] = set()
    offsets = []
    for s, w, _ in eb:
        cands = [x for x in by_word.get(w, []) if abs(x - s) <= tol_snaps and (x, w) not in used]
        if cands:
            x = min(cands, key=lambda v: (abs(v - s), v))
            used.add((x, w))
            offsets.append(x - s)
    return offsets


def event_stats(eb, ep):
    offs = [o * SNAP for o in match_events(eb, ep, MATCH_TOL // SNAP)]
    n = len(offs)
    absd = [abs(o) for o in offs]
    late = [o for o in offs if o > 0]
    return {
        "events_exact": len(eb), "events_approx": len(ep), "matched": n,
        "recall": n / len(eb) if eb else 1.0, "precision": n / len(ep) if ep else 1.0,
        "same_snapshot_pct": 100 * sum(1 for o in offs if o == 0) / n if n else 0.0,
        "early_pct": 100 * sum(1 for o in offs if o < 0) / n if n else 0.0,
        "late_pct": 100 * len(late) / n if n else 0.0,
        "abs_offset_median": pct(absd, 0.5) or 0, "abs_offset_p95": pct(absd, 0.95) or 0,
        "abs_offset_max": max(absd) if absd else 0,
        "late_median": pct(late, 0.5), "late_p95": pct(late, 0.95), "late_max": max(late) if late else None,
    }


# ---------------------------------------------------------------------- E1 / E2 runs
def run_decay(docs, c, theta=None, snap=SNAP, mem_every=MEM_EVERY, mem_from=0, base_tops=None):
    """One pass. Records Top K per snapshot, size per snapshot, Python bytes every mem_every,
    and (if base_tops given) the max under-estimate of the exact Top K keywords."""
    dw = DecayingWindow(c, theta)
    tops, sizes, max_err, mem = [], [], [], []
    s = 0
    for i, toks in enumerate(docs):
        dw.add(toks)
        if (i + 1) % snap == 0:
            tops.append(dw.top_k(K))
            sizes.append(len(dw))
            if base_tops is not None:
                max_err.append(max(sc - dw.score(w) for w, sc in base_tops[s]))
            s += 1
        if (i + 1) % mem_every == 0 and i + 1 >= mem_from:
            mem.append({"t": i + 1, "bytes": deep_sizeof([dw._vals, dw._heap]),
                        "keywords": len(dw), "heap": dw.heap_size})
    return {"tops": tops, "sizes": sizes, "max_err": max_err, "mem": mem,
            "peak": dw.peak_size, "dropped": dw.dropped_total, "bound": dw.error_bound()}


def distinct_in_window(docs, W, snap=SNAP):
    window: deque = deque()
    live: Counter = Counter()
    out = []
    for i, toks in enumerate(docs):
        window.append(toks)
        live.update(toks)
        if len(window) > W:
            for w in window.popleft():
                live[w] -= 1
                if live[w] <= 0:
                    del live[w]
        if (i + 1) % snap == 0:
            out.append(len(live))
    return out


def compare(base, pr, start, snap_years):
    p10, p20, same, inv = [], [], [], []
    by_year: dict[str, list] = {}
    worst = (2.0, None)
    flips = []
    for s in range(start, len(base["tops"])):
        b = [w for w, _ in base["tops"][s]]
        p = [w for w, _ in pr["tops"][s]]
        o20 = len(set(b) & set(p)) / K
        p10.append(len(set(b[:10]) & set(p[:10])) / 10)
        p20.append(o20)
        same.append(b == p)
        by_year.setdefault(snap_years[s], []).append((o20, b == p))
        if o20 < worst[0]:
            worst = (o20, s)
        rank_p = {w: r for r, w in enumerate(p)}
        n_inv = inversions(b, rank_p)
        inv.append(n_inv)
        if n_inv and len(flips) < 5:
            bs, ps = dict(base["tops"][s]), dict(pr["tops"][s])
            for i in range(len(b) - 1):
                w1, w2 = b[i], b[i + 1]
                if w1 in rank_p and w2 in rank_p and rank_p[w1] > rank_p[w2]:
                    flips.append({"snapshot": s, "exact": [w1, round(bs[w1], 3), w2, round(bs[w2], 3)],
                                  "approx": [round(ps[w1], 3), round(ps[w2], 3)]})
                    break
    eb = topk_events([[w for w, _ in t] for t in base["tops"]], start)
    ep = topk_events([[w for w, _ in t] for t in pr["tops"]], start)
    return {
        "topk_overlap_10_mean": statistics.mean(p10), "topk_overlap_20_mean": statistics.mean(p20),
        "topk_overlap_20_min": worst[0], "topk_overlap_20_min_snapshot": worst[1],
        "same_order_pct": 100 * sum(same) / len(same),
        "inversions_mean": statistics.mean(inv), "inversions_max": max(inv),
        "by_year": {y: {"overlap20": statistics.mean(o for o, _ in v),
                        "same_order_pct": 100 * sum(1 for _, x in v if x) / len(v), "snapshots": len(v)}
                    for y, v in sorted(by_year.items())},
        "events": event_stats(eb, ep),
        "rank_flip_examples": flips,
    }


def mean_bytes(mem, start_t):
    xs = [m["bytes"] for m in mem if m["t"] >= start_t]
    return statistics.mean(xs) if xs else None


def e1(docs, dates):
    c = 1e-3
    start = int(10 / c / SNAP)
    start_t = start * SNAP
    snap_dates = [dates[min((s + 1) * SNAP - 1, len(dates) - 1)] for s in range(len(docs) // SNAP)]
    years = [d[:4] for d in snap_dates]
    print(f"[E1] exact baseline c={c}")
    base = run_decay(docs, c, None, mem_from=start_t)
    distinct = distinct_in_window(docs, round(1 / c))
    mem_b = mean_bytes(base["mem"], start_t)
    kw_b = statistics.mean(base["sizes"][start:])
    rows = []
    curves = {"t": [(s + 1) * SNAP for s in range(len(base["sizes"]))], "dates": snap_dates,
              "baseline": base["sizes"], "distinct_in_window": distinct}
    mem_samples = {"exact": base["mem"]}
    for theta in THETAS:
        pr = run_decay(docs, c, theta, mem_from=start_t, base_tops=base["tops"])
        cmp_ = compare(base, pr, start, years)
        kw_p = statistics.mean(pr["sizes"][start:])
        mem_p = mean_bytes(pr["mem"], start_t)
        worst_s = cmp_["topk_overlap_20_min_snapshot"]
        row = {
            "theta": theta, "keywords_mean_approx": round(kw_p), "keywords_mean_exact": round(kw_b),
            "keyword_reduction_pct": 100 * (1 - kw_p / kw_b),
            "keywords_peak_approx": pr["peak"], "keywords_final_exact": base["sizes"][-1],
            "heap_entries_mean": statistics.mean(m["heap"] for m in pr["mem"] if m["t"] >= start_t),
            "pybytes_mean_approx": mem_p, "pybytes_mean_exact": mem_b,
            "pybytes_reduction_pct": 100 * (1 - mem_p / mem_b),
            "keywords_vs_distinct_in_window": kw_p / statistics.mean(distinct[start:]),
            "score_err_max": max(pr["max_err"][start:]), "score_err_bound": pr["bound"],
            "dropped": pr["dropped"], "worst_date": snap_dates[worst_s] if worst_s is not None else None,
            **cmp_,
        }
        rows.append(row)
        if theta == 0.5:
            curves["approx_0.5"] = pr["sizes"]
            mem_samples["approx_0.5"] = pr["mem"]
        ev = row["events"]
        print(f"  theta={theta}: keywords -{row['keyword_reduction_pct']:.1f}% bytes -{row['pybytes_reduction_pct']:.1f}% "
              f"overlap20 {row['topk_overlap_20_mean']:.4f} (min {row['topk_overlap_20_min']:.2f}) "
              f"same-order {row['same_order_pct']:.1f}% events P {ev['precision']:.3f} R {ev['recall']:.3f} "
              f"late {ev['late_pct']:.1f}%")
    return {"c": c, "rows": rows, "eval_from_article": start_t, "curves": curves, "memory_samples": mem_samples}


def stability(tops, start, lag):
    vals = []
    for s in range(start, len(tops) - lag):
        a = {w for w, _ in tops[s]}
        b = {w for w, _ in tops[s + lag]}
        vals.append(len(a & b) / K)
    return statistics.mean(vals)


def e2(docs, dates):
    rows = []
    years = [dates[min((s + 1) * SNAP - 1, len(dates) - 1)][:4] for s in range(len(docs) // SNAP)]
    for c in CS:
        start = int(10 / c / SNAP)
        base = run_decay(docs, c, None, mem_every=10 ** 12)
        pr = run_decay(docs, c, 0.5, mem_every=10 ** 12, base_tops=base["tops"])
        cmp_ = compare(base, pr, start, years)
        kw_b = statistics.mean(base["sizes"][start:])
        kw_p = statistics.mean(pr["sizes"][start:])
        rows.append({"c": c, "scale_1_over_c": round(1 / c),
                     "half_life": round(math.log(0.5) / math.log(1 - c)),
                     "keywords_mean_approx": round(kw_p), "keywords_mean_exact": round(kw_b),
                     "keyword_reduction_pct": 100 * (1 - kw_p / kw_b),
                     "topk_overlap_20_mean": cmp_["topk_overlap_20_mean"],
                     "same_order_pct": cmp_["same_order_pct"],
                     "stability_100": stability(pr["tops"], start, 1),
                     "stability_1000": stability(pr["tops"], start, 10),
                     "events_exact": cmp_["events"]["events_exact"],
                     "event_recall": cmp_["events"]["recall"], "event_precision": cmp_["events"]["precision"]})
        r = rows[-1]
        print(f"[E2] c={c}: -{r['keyword_reduction_pct']:.1f}% overlap {r['topk_overlap_20_mean']:.4f} "
              f"same-order {r['same_order_pct']:.1f}% stability100 {r['stability_100']:.3f} 1000 {r['stability_1000']:.3f}")
    return {"theta": 0.5, "rows": rows}


# ---------------------------------------------------------------------- E3
BANDS = [(1, 4), (5, 9), (10, 10 ** 9)]
BAND_NAMES = ["1-4", "5-9", "10+"]


def e3(docs, tracked):
    rows, examples = [], []
    for N in DGIM_NS:
        ex = {w: ExactWindowCount(N) for w in tracked}
        exact_snaps = []
        exact_bytes = []
        for t, toks in enumerate(docs):
            for w in toks:
                e = ex.get(w)
                if e is not None:
                    e.add(t)
            if (t + 1) % DGIM_EVAL == 0 and t >= N:
                for e in ex.values():
                    e.expire(t)
                exact_snaps.append((t, {w: e.count(t) for w, e in ex.items()},
                                    sum(e.memory_bits() for e in ex.values())))
                if len(exact_snaps) % 40 == 0:
                    exact_bytes.append(deep_sizeof([ex]))
        exact_bits = statistics.mean(b for _, _, b in exact_snaps)
        for r in DGIM_RS:
            for estimator in (["midpoint", "textbook"] if (N == 1000 and r == 2) else ["midpoint"]):
                dg = {w: DGIM(N, r, estimator) for w in tracked}
                rel, bits, p10, inv, dg_bytes = [], [], [], [], []
                band = {b: [] for b in BANDS}
                si = 0
                for t, toks in enumerate(docs):
                    for w in toks:
                        d = dg.get(w)
                        if d is not None:
                            d.add(t)
                    if si < len(exact_snaps) and exact_snaps[si][0] == t:
                        truth = exact_snaps[si][1]
                        est = {w: d.estimate(t) for w, d in dg.items()}   # estimate() expires first
                        for w, x in truth.items():
                            if x > 0:
                                e_ = abs(est[w] - x) / x
                                rel.append(e_)
                                for lo, hi in BANDS:
                                    if lo <= x <= hi:
                                        band[(lo, hi)].append(e_)
                        bits.append(sum(d.memory_bits() for d in dg.values()))
                        if (si + 1) % 40 == 0:
                            dg_bytes.append(deep_sizeof([dg]))
                        top_true = sorted(truth, key=lambda w: (-truth[w], w))[:10]
                        order_est = sorted(est, key=lambda w: (-est[w], w))
                        p10.append(len(set(top_true) & set(order_est[:10])) / 10)
                        inv.append(inversions(top_true, {w: i for i, w in enumerate(order_est)}))
                        if N == 1000 and r == 2 and estimator == "midpoint" and len(examples) < 8:
                            for i in range(len(top_true) - 1):
                                a, b = top_true[i], top_true[i + 1]
                                if truth[a] > truth[b] and est[a] < est[b]:
                                    examples.append({"t": t, "higher": a, "lower": b,
                                                     "exact": [truth[a], truth[b]], "dgim": [est[a], est[b]]})
                                    break
                        si += 1
                row = {"N": N, "r": r, "estimator": estimator, "bound": 1 / (2 * (r - 1)),
                       "rel_err_mean": statistics.mean(rel), "rel_err_p95": pct(rel, 0.95), "rel_err_max": max(rel),
                       "rel_err_by_count": {name: (statistics.mean(band[b]) if band[b] else None)
                                            for name, b in zip(BAND_NAMES, BANDS)},
                       "logical_bits_dgim": statistics.mean(bits), "logical_bits_exact": exact_bits,
                       "logical_dgim_vs_exact_pct": 100 * statistics.mean(bits) / exact_bits,
                       "pybytes_dgim": statistics.mean(dg_bytes) if dg_bytes else None,
                       "pybytes_exact": statistics.mean(exact_bytes) if exact_bytes else None,
                       "top10_overlap_mean": statistics.mean(p10), "top10_inversions_mean": statistics.mean(inv)}
                if row["pybytes_dgim"] and row["pybytes_exact"]:
                    row["pybytes_dgim_vs_exact_pct"] = 100 * row["pybytes_dgim"] / row["pybytes_exact"]
                rows.append(row)
                print(f"[E3] N={N} r={r} {estimator}: err {row['rel_err_mean']:.3f} max {row['rel_err_max']:.3f} "
                      f"logical {row['logical_dgim_vs_exact_pct']:.0f}% py {row.get('pybytes_dgim_vs_exact_pct', 0):.0f}%")
    return {"tracked_keywords": len(tracked), "eval_every": DGIM_EVAL, "rows": rows, "rank_flip_examples": examples}


# ---------------------------------------------------------------------- E4 throughput
def timed(fn, repeats):
    out = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t0)
    return out


def e4(docs, articles, tracked, repeats):
    n = len(docs)
    res = {"repeats": repeats}
    holder: dict = {}

    def upd(theta):
        def f():
            dw = DecayingWindow(1e-3, theta)
            for toks in docs:
                dw.add(toks)
            holder["dw"] = dw
        return f

    for name, theta in (("exact", None), ("approx_0.5", 0.5), ("approx_0.1", 0.1)):
        ts = timed(upd(theta), repeats)
        res[f"update_{name}"] = {**med_range([n / t for t in ts]), "unit": "articles/s"}
        dw = holder["dw"]
        q = timed(lambda: [dw.top_k(K) for _ in range(50)], repeats)
        res[f"topk_query_{name}"] = {**med_range([t / 50 * 1000 for t in q]), "unit": "ms/query",
                                     "keywords": len(dw)}
        print(f"[E4] {name}: update {res[f'update_{name}']['median']:,.0f}/s, top_k {res[f'topk_query_{name}']['median']:.2f} ms")

    tset = set(tracked)

    def run_counters(make):
        def f():
            cs = {w: make() for w in tset}
            for t, toks in enumerate(docs):
                for w in toks:
                    cc = cs.get(w)
                    if cc is not None:
                        cc.add(t)
            holder["cs"] = cs
        return f

    t_end = n - 1
    for name, make, query in (
            ("dgim_N1000_r2", lambda: DGIM(1000, 2), lambda c: c.estimate(t_end)),
            ("exact_window_N1000", lambda: ExactWindowCount(1000), lambda c: c.count(t_end))):
        ts = timed(run_counters(make), repeats)
        res[f"update_{name}"] = {**med_range([n / t for t in ts]), "unit": "articles/s"}
        cs = holder["cs"]
        q = timed(lambda: [query(c) for c in cs.values()], repeats)
        res[f"query_all_{name}"] = {**med_range([x * 1000 for x in q]), "unit": "ms per query of all tracked"}
        print(f"[E4] {name}: update {res[f'update_{name}']['median']:,.0f}/s")

    # Engine end-to-end (all counters + snapshots every 50 articles)
    from trendpulse.engine import EngineConfig, TrendEngine
    start_date = "2016-01-01" if articles[-1].date > "2016-06-01" else articles[len(articles) // 2].date
    # v0.2 setting (single words) and the v0.3 default (words + phrases, burst window)
    for key, cfg in (("engine_step", EngineConfig(phrases=False, decay_unit="article")), ("engine_step_v03", EngineConfig())):
        eng = TrendEngine(articles, cfg)
        rates = []
        k = 0
        for _ in range(max(2, repeats // 2)):
            eng.reset(start_date=start_date)
            k = min(20_000, len(articles) - eng.pos)
            t0 = time.perf_counter()
            eng.step(k)
            rates.append(k / (time.perf_counter() - t0))
        res[key] = {**med_range(rates), "unit": "articles/s", "articles": k, "snapshot_every": 50,
                    "start_date": start_date}
    # API latency (Flask test client, no network)
    from app import create_app
    app = create_app(articles, start_date=start_date)
    client = app.test_client()
    lat_state, lat_step = [], []
    for _ in range(30):
        t0 = time.perf_counter()
        client.get("/api/state")
        lat_state.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        client.post("/api/step", json={"n": 50})
        lat_step.append((time.perf_counter() - t0) * 1000)
    res["api_state_ms"] = med_range(lat_state)
    res["api_step50_ms"] = med_range(lat_step)
    print(f"[E4] engine {res['engine_step']['median']:,.0f}/s (v0.3 default {res['engine_step_v03']['median']:,.0f}/s), /api/state {res['api_state_ms']['median']:.1f} ms, "
          f"/api/step(50) {res['api_step50_ms']['median']:.1f} ms")
    return res


# ---------------------------------------------------------------------- E5 within-day order
def e5(articles, seeds=(1, 2, 3)):
    def day_tops(arts):
        dw = DecayingWindow(1e-3, 0.5)
        out = {}
        for i, a in enumerate(arts):
            dw.add(a.tokens)
            if i + 1 == len(arts) or arts[i + 1].date != a.date:
                out[a.date] = [w for w, _ in dw.top_k(K)]
        return out

    def first_entry(tops):
        fe = {}
        for d in sorted(tops):
            for w in tops[d]:
                fe.setdefault(w, d)
        return fe

    orig = day_tops(articles)
    fe0 = first_entry(orig)
    days = sorted(orig)[10:]          # skip the first days (window still filling)
    by_day: dict[str, list[Article]] = {}
    for a in articles:
        by_day.setdefault(a.date, []).append(a)
    rows = []
    for seed in seeds:
        rng = random.Random(seed)
        shuffled = []
        for d in sorted(by_day):
            lst = by_day[d][:]
            rng.shuffle(lst)
            shuffled.extend(lst)
        tops = day_tops(shuffled)
        fe = first_entry(tops)
        ov = [len(set(orig[d]) & set(tops[d])) / K for d in days]
        same = [orig[d] == tops[d] for d in days]
        common = [w for w in fe0 if w in fe]
        shift = [abs((Date.fromisoformat(fe[w]) - Date.fromisoformat(fe0[w])).days) for w in common]
        rows.append({"seed": seed, "overlap20_mean": statistics.mean(ov), "overlap20_min": min(ov),
                     "same_order_pct": 100 * sum(same) / len(same),
                     "first_entry_same_day_pct": 100 * sum(1 for x in shift if x == 0) / len(shift),
                     "first_entry_shift_p95_days": pct(shift, 0.95),
                     "keywords_only_in_one_order": len(set(fe0) ^ set(fe))})
        print(f"[E5] seed {seed}: overlap {rows[-1]['overlap20_mean']:.4f} same-order {rows[-1]['same_order_pct']:.1f}% "
              f"first-entry same day {rows[-1]['first_entry_same_day_pct']:.1f}%")
    return {"rows": rows, "days_evaluated": len(days)}


# ---------------------------------------------------------------------- E6 case studies
def e6(articles):
    dates = [a.date for a in articles]
    windows = []
    for d, kw, label in EVENTS:
        if not (dates[0] <= d <= dates[-1]):
            continue
        ev = Date.fromisoformat(d)
        windows.append({"date": d, "keyword": kw, "label": label,
                        "lo": (ev - timedelta(days=EVENT_BEFORE)).isoformat(),
                        "hi": (ev + timedelta(days=EVENT_AFTER)).isoformat()})
    base = DecayingWindow(1e-3, None)
    appr = DecayingWindow(1e-3, 0.5)
    state = {w["keyword"] + w["date"]: {"exact_entry": None, "approx_entry": None, "first_mention": None,
                                       "in_top_before": False, "best_rank": None} for w in windows}
    postings: dict[str, list[int]] = {}
    for i, a in enumerate(articles):
        base.add(a.tokens)
        appr.add(a.tokens)
        for w in a.tokens:
            postings.setdefault(w, []).append(i)
        active = [w for w in windows if w["lo"] <= a.date <= w["hi"]]
        if not active:
            continue
        bt = [w for w, _ in base.top_k(K)]
        at = [w for w, _ in appr.top_k(K)]
        for w in active:
            st = state[w["keyword"] + w["date"]]
            kw = w["keyword"]
            if a.date < w["date"]:
                if kw in at:
                    st["in_top_before"] = True
                continue
            if kw in a.tokens and st["first_mention"] is None:
                st["first_mention"] = i
            if kw in bt and st["exact_entry"] is None:
                st["exact_entry"] = i
            if kw in at:
                r = at.index(kw) + 1
                st["best_rank"] = r if st["best_rank"] is None else min(st["best_rank"], r)
                if st["approx_entry"] is None:
                    st["approx_entry"] = i
                    lst = postings.get(kw, [])
                    st["evidence"] = [{"id": j, "date": articles[j].date, "headline": articles[j].headline}
                                      for j in reversed(lst[-3:])]
                    co: Counter = Counter()
                    for j in lst[-30:]:
                        co.update(x for x in articles[j].tokens if x != kw)
                    st["cooccurring"] = [x for x, _ in co.most_common(5)]
    rows = []
    for w in windows:
        st = state[w["keyword"] + w["date"]]
        ev = Date.fromisoformat(w["date"])

        def days(idx):
            return None if idx is None else (Date.fromisoformat(dates[idx]) - ev).days

        ae, ee = st["approx_entry"], st["exact_entry"]
        rows.append({
            "event": w["label"], "date": w["date"], "keyword": w["keyword"],
            "first_mention_day": days(st["first_mention"]),
            "exact_entry_day": days(ee), "approx_entry_day": days(ae),
            "approx_minus_exact_articles": (ae - ee) if (ae is not None and ee is not None) else None,
            "best_rank_approx": st["best_rank"], "already_in_top_before": st["in_top_before"],
            "success": ae is not None and days(ae) <= SUCCESS_DAYS,
            "articles_per_day_in_window": round(sum(1 for d in dates if w["lo"] <= d <= w["hi"])
                                                / (EVENT_BEFORE + EVENT_AFTER + 1), 1),
            "evidence": st.get("evidence", []), "cooccurring": st.get("cooccurring", []),
        })
        r = rows[-1]
        print(f"[E6] {w['keyword']:16s} exact day {r['exact_entry_day']}, approx day {r['approx_entry_day']}, "
              f"diff {r['approx_minus_exact_articles']} articles, success {r['success']}")
    return {"rows": rows, "criteria": {"window_days": [-EVENT_BEFORE, EVENT_AFTER], "success_days": SUCCESS_DAYS,
                                        "k": K, "c": 1e-3, "theta": 0.5}}


# ---------------------------------------------------------------------- figures
COL = {"blue": "#2a78d6", "orange": "#eb6834", "aqua": "#1baf7a", "ink": "#0b0b0b",
       "ink2": "#52514e", "grid": "#e4e3df", "surface": "#fcfcfb", "ref": "#8a8984"}


def setup_mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    names = {f.name for f in font_manager.fontManager.ttflist}
    font = next((f for f in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR") if f in names),
                "DejaVu Sans")
    plt.rcParams.update({
        "font.family": font, "axes.unicode_minus": False, "font.size": 11,
        "figure.facecolor": COL["surface"], "axes.facecolor": COL["surface"],
        "savefig.facecolor": COL["surface"], "axes.edgecolor": COL["grid"],
        "axes.labelcolor": COL["ink2"], "xtick.color": COL["ink2"], "ytick.color": COL["ink2"],
        "axes.grid": True, "grid.color": COL["grid"], "grid.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False, "axes.titleweight": "bold",
        "axes.titlesize": 13, "axes.titlecolor": COL["ink"], "axes.titlelocation": "left",
        "legend.frameon": False, "lines.linewidth": 2,
    })
    return plt


def figures(res):
    plt = setup_mpl()
    FIG.mkdir(parents=True, exist_ok=True)
    for old in FIG.glob("*.png"):
        old.unlink()
    rows = res["E1"]["rows"]
    th = [r["theta"] for r in rows]
    cv = res["E1"]["curves"]

    # F1 stored keywords over time (logical size)
    fig, ax = plt.subplots(figsize=(10, 4.6))
    x = cv["t"]
    ax.plot(x, cv["baseline"], color=COL["orange"], label="정확 기준: 모든 키워드 유지")
    ax.plot(x, cv["approx_0.5"], color=COL["blue"], label="감쇠 윈도우 + 제거 (θ=0.5)")
    ax.plot(x, cv["distinct_in_window"], color=COL["aqua"], linestyle=(0, (4, 3)),
            label="참고: 최근 1,000건 안의 고유 키워드")
    ticks = list(range(0, len(x), max(1, len(x) // 6)))
    ax.set_xticks([x[i] for i in ticks], [cv["dates"][i][:7] for i in ticks])
    ax.set_ylabel("저장 키워드 수 (개)")
    ax.set_xlabel("기사 순서 (2018년 이후는 기사 수가 적어 짧게 보임)")
    ax.set_title("저장 키워드 수: 정확 기준은 계속 늘고, 제거 방식은 일정")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.legend(loc="upper left")
    ax.annotate(f"{cv['baseline'][-1]:,}", (x[-1], cv["baseline"][-1]), textcoords="offset points",
                xytext=(-4, 6), ha="right", color=COL["ink"], fontsize=10)
    fig.tight_layout()
    fig.savefig(FIG / "f1_keywords_over_time.png", dpi=180)
    plt.close(fig)

    # F2 logical vs Python-object reduction (same unit: %, one axis)
    fig, ax = plt.subplots(figsize=(7.2, 4.3))
    ax.plot(th, [r["keyword_reduction_pct"] for r in rows], color=COL["blue"], marker="o", markersize=7,
            label="저장 키워드 수 감소율")
    ax.plot(th, [r["pybytes_reduction_pct"] for r in rows], color=COL["orange"], marker="s", markersize=7,
            label="Python 집계 객체 크기 감소율")
    for t_, r in zip(th, rows):
        ax.annotate(f"{r['keyword_reduction_pct']:.0f}%", (t_, r["keyword_reduction_pct"]), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontsize=9, color=COL["ink"])
        ax.annotate(f"{r['pybytes_reduction_pct']:.0f}%", (t_, r["pybytes_reduction_pct"]), textcoords="offset points",
                    xytext=(0, -16), ha="center", fontsize=9, color=COL["ink"])
    ax.set_title("키워드 수와 실제 객체 크기는 다르게 줄어든다")
    ax.set_xlabel("제거 임계값 θ")
    ax.set_ylabel("정확 기준 대비 감소율 (%)")
    lo = min(r["pybytes_reduction_pct"] for r in rows)
    ax.set_ylim(max(0, lo - 12), 100)
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(FIG / "f2_memory_logical_vs_python.png", dpi=180)
    plt.close(fig)

    # F3 ranking agreement vs theta
    fig, ax = plt.subplots(figsize=(7.2, 4.3))
    ax.plot(th, [100 * r["topk_overlap_20_mean"] for r in rows], color=COL["blue"], marker="o", markersize=7,
            label="Top 20 구성원 겹침 (평균)")
    ax.plot(th, [100 * r["topk_overlap_20_min"] for r in rows], color=COL["aqua"], marker="^", markersize=7,
            label="Top 20 구성원 겹침 (최저 시점)")
    ax.plot(th, [r["same_order_pct"] for r in rows], color=COL["orange"], marker="s", markersize=7,
            label="20개 순서까지 같은 시점 비율")
    ax.set_title("θ를 키우면 구성원보다 순서가 먼저 흔들린다")
    ax.set_xlabel("제거 임계값 θ")
    ax.set_ylabel("%")
    vals = [r["same_order_pct"] for r in rows] + [100 * r["topk_overlap_20_min"] for r in rows]
    ax.set_ylim(max(0, min(vals) - 6), 101)
    ax.legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(FIG / "f3_rank_agreement.png", dpi=180)
    plt.close(fig)

    # F4 score error vs bound
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    idx = list(range(len(th)))
    ax.bar(idx, [r["score_err_max"] for r in rows], width=0.6, color=COL["blue"], label="관측된 최대 점수 과소추정")
    ax.plot(idx, [r["score_err_bound"] for r in rows], color=COL["ref"], linestyle=(0, (4, 3)), marker="o",
            markersize=6, label="이론 상한 θ/(1-θ)")
    ax.set_xticks(idx, [str(t) for t in th])
    ax.set_xlabel("제거 임계값 θ")
    ax.set_ylabel("감쇠 점수 차이")
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.yaxis.set_minor_formatter(plt.NullFormatter())
    ax.set_title("점수 오차는 항상 이론 상한 아래")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(FIG / "f4_score_error.png", dpi=180)
    plt.close(fig)

    # F5 Top-K entry events: precision/recall and timing split
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.0))
    ax = axes[0]
    ax.plot(th, [100 * r["events"]["recall"] for r in rows], color=COL["blue"], marker="o", markersize=7, label="재현율")
    ax.plot(th, [100 * r["events"]["precision"] for r in rows], color=COL["orange"], marker="s", markersize=7, label="정밀도")
    ax.set_title("Top 20 진입 이벤트: 정확 기준과의 대응")
    ax.set_xlabel("제거 임계값 θ")
    ax.set_ylabel("%")
    lo = min(min(100 * r["events"]["recall"], 100 * r["events"]["precision"]) for r in rows)
    ax.set_ylim(max(0, lo - 4), 100.8)
    ax.legend(loc="lower left")
    ax = axes[1]
    ax.plot(th, [r["events"]["late_pct"] for r in rows], color=COL["orange"], marker="s", markersize=7,
            label="정확 기준보다 늦게")
    ax.plot(th, [r["events"]["early_pct"] for r in rows], color=COL["aqua"], marker="^", markersize=7,
            label="정확 기준보다 이르게")
    for t_, r in zip(th, rows):
        if r["events"]["late_pct"] <= 0:
            continue
        ax.annotate(f"{r['events']['late_pct']:.1f}%", (t_, r["events"]["late_pct"]), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontsize=9, color=COL["ink"])
    ax.set_ylim(0, max(r["events"]["late_pct"] for r in rows) * 1.35 + 0.5)
    ax.set_title("대응된 이벤트 중 시점이 다른 비율 (나머지는 같은 스냅샷)")
    ax.set_xlabel("제거 임계값 θ")
    ax.set_ylabel("대응된 이벤트 중 비율 (%)")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(FIG / "f5_entry_events.png", dpi=180)
    plt.close(fig)

    # F6 DGIM error / logical memory
    e3r = [r for r in res["E3"]["rows"] if r["estimator"] == "midpoint"]
    sub = [r for r in e3r if r["N"] == 1000]
    rs = [r["r"] for r in sub]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axes[0]
    ax.plot(rs, [100 * r["rel_err_mean"] for r in sub], color=COL["blue"], marker="o", markersize=7, label="평균")
    ax.plot(rs, [100 * r["rel_err_p95"] for r in sub], color=COL["orange"], marker="s", markersize=7, label="95번째 백분위")
    ax.plot(rs, [100 * r["rel_err_max"] for r in sub], color=COL["aqua"], marker="^", markersize=7, label="최대")
    ax.plot(rs, [100 * r["bound"] for r in sub], color=COL["ref"], linestyle=(0, (4, 3)), label="이론 상한 1/(2(r-1))")
    ax.set_xticks(rs)
    ax.set_xlabel("크기별 최대 버킷 수 r")
    ax.set_ylabel("상대 오차 (%)")
    ax.set_title("DGIM 상대 오차 (N = 1,000)")
    ax.legend(loc="upper right")
    ax = axes[1]
    for N, color, mk in zip(DGIM_NS, (COL["blue"], COL["orange"], COL["aqua"]), ("o", "s", "^")):
        s2 = [r for r in e3r if r["N"] == N]
        ax.plot([r["r"] for r in s2], [r["logical_dgim_vs_exact_pct"] for r in s2], color=color, marker=mk,
                markersize=7, label=f"N = {N:,}")
    ax.axhline(100, color=COL["ref"], linestyle=(0, (4, 3)), linewidth=1.5)
    ax.annotate("정확 집계와 같은 저장량", (rs[-1], 100), textcoords="offset points", xytext=(0, -15),
                ha="right", fontsize=9, color=COL["ink2"])
    ax.set_xticks(rs)
    ax.set_xlabel("크기별 최대 버킷 수 r")
    ax.set_ylabel("정확 집계 대비 논리 저장량 (%)")
    ax.set_title("DGIM 논리 저장량 (압축 저장 가정)")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(FIG / "f6_dgim_error_memory.png", dpi=180)
    plt.close(fig)

    # F7 case studies: day of Top 20 entry relative to the event date
    e6r = res["E6"]["rows"]
    fig, ax = plt.subplots(figsize=(10, 5.4))
    ys = list(range(len(e6r)))[::-1]
    for y, r in zip(ys, e6r):
        ex, ap = r["exact_entry_day"], r["approx_entry_day"]
        if ex is not None:
            ax.scatter([ex], [y], s=110, color=COL["orange"], zorder=3, edgecolor=COL["surface"], linewidth=2)
        if ap is not None:
            ax.scatter([ap], [y], s=40, color=COL["blue"], zorder=4, edgecolor=COL["surface"], linewidth=1.5)
        if ap is None and ex is None:
            ax.annotate("관찰 기간 내 Top 20 진입 없음", (EVENT_AFTER, y), xytext=(0, -4), textcoords="offset points",
                        ha="right", fontsize=9, color=COL["ink2"])
    ax.scatter([], [], s=110, color=COL["orange"], label="정확 기준 Top 20 진입")
    ax.scatter([], [], s=40, color=COL["blue"], label="근사(θ=0.5) Top 20 진입")
    ax.axvspan(0, SUCCESS_DAYS, color=COL["blue"], alpha=0.06, lw=0)
    ax.axvline(0, color=COL["ref"], linestyle=(0, (4, 3)), linewidth=1.2)
    ax.set_yticks(ys, [f"{r['keyword']} ({r['date']})" for r in e6r])
    ax.set_xlim(-0.8, EVENT_AFTER + 0.5)
    ax.set_xlabel("사건일 기준 경과 일수 (음영 = 성공 기준 7일)")
    ax.set_title(f"실제 사건 {len(e6r)}개: 키워드가 Top 20에 들어온 날")
    ax.legend(loc="upper right")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(FIG / "f7_case_studies.png", dpi=180)
    plt.close(fig)


# ---------------------------------------------------------------------- summary
def fmtp(x, d=1):
    return "-" if x is None else f"{100 * x:.{d}f}%"


def dash(x):
    return "-" if x is None else x


def summary_md(res) -> str:
    meta = res["meta"]
    L = ["# 실험 결과 요약 (v0.2)\n",
         f"- 데이터: HuffPost 기사 {res['articles']:,}건 ({res['first_date']} ~ {res['last_date']}), 정리 규칙 v2 (E0)",
         f"- 실행 환경: Python {meta['python']} / {meta['platform']} / CPU {meta['cpus']}개 · 실행 일시 {meta['run_at']} · 소요 {res['elapsed_sec']}초",
         f"- 공통: K={K}, 스냅샷 {SNAP}건마다, 준비 구간 10/c건은 평가 제외, 이벤트 대응 허용 범위 ±{MATCH_TOL:,}건(=1/c)",
         "- **메모리 용어**: '저장 키워드 수' = 논리 지표(개수). 'Python 객체 크기' = `deep_sizeof`(trendpulse/memory.py)로 잰 "
         "집계 자료구조(딕셔너리·키 문자열·float·힙)의 바이트. 기사 원문·관련 기사 인덱스·프로세스 전체 메모리는 포함하지 않음(README 참고).\n"]
    q = res["E0"]
    L += ["## E0. 데이터 정리\n",
          f"원본 {q['raw_rows']:,}행 → 빈 제목 {q['dropped_empty_headline']}건, 중복 링크 {q['dropped_duplicate_link']}건 제거 → {q['kept']:,}건. "
          f"같은 날짜·같은 제목이지만 링크가 다른 {q['kept_same_date_and_headline_extra']}건은 서로 다른 기사일 수 있어 유지. "
          f"키워드가 하나도 남지 않는 제목 {q['kept_without_tokens']}건(집계에 영향 없음).\n"]
    e1r = res["E1"]
    L += [f"## E1. 제거 임계값 θ (c = {e1r['c']}, 반감기 ≈ 693건)\n",
          "### 메모리 (평가 구간 평균)\n",
          "| θ | 저장 키워드 (근사 / 정확) | 키워드 수 감소율 | 힙 항목 평균 | Python 객체 크기 (근사 / 정확) | 객체 크기 감소율 |",
          "|---|---|---|---|---|---|"]
    for r in e1r["rows"]:
        L.append(f"| {r['theta']} | {r['keywords_mean_approx']:,} / {r['keywords_mean_exact']:,} | {r['keyword_reduction_pct']:.1f}% | "
                 f"{r['heap_entries_mean']:,.0f} | {r['pybytes_mean_approx'] / 1e6:.2f} MB / {r['pybytes_mean_exact'] / 1e6:.2f} MB | "
                 f"{r['pybytes_reduction_pct']:.1f}% |")
    L += [f"\nPython 객체 크기는 {MEM_EVERY:,}건마다 측정한 값의 평균. 제거 방식에는 기준 방식에 없는 힙(오래된 항목 포함)이 있어 "
          "키워드 수만큼 줄지 않는다. 정확 기준의 크기는 본 단어 수에 비례해 계속 커지므로 감소율은 측정 구간 길이에 따라 달라진다.\n",
          "### 순위 재현 (정확 기준과 비교)\n",
          "| θ | Top10 구성원 겹침 | Top20 구성원 겹침 평균 | 최저 (시점) | 20개 순서까지 동일 | 역전 쌍 평균/최대 | 최대 점수 오차 / 상한 |",
          "|---|---|---|---|---|---|---|"]
    for r in e1r["rows"]:
        L.append(f"| {r['theta']} | {100 * r['topk_overlap_10_mean']:.2f}% | {100 * r['topk_overlap_20_mean']:.2f}% | "
                 f"{100 * r['topk_overlap_20_min']:.0f}% ({r['worst_date']}) | {r['same_order_pct']:.1f}% | "
                 f"{r['inversions_mean']:.2f} / {r['inversions_max']} | {r['score_err_max']:.3f} / {r['score_err_bound']:.3f} |")
    r5 = next(r for r in e1r["rows"] if r["theta"] == 0.5)
    L += ["\n'구성원 겹침' = 같은 시점 두 방식의 Top K 집합이 겹치는 비율(순서 무관). 사람이 판단한 화제를 맞힌 비율이 아니다(E6 참고).\n",
          "#### 연도별 (θ = 0.5)\n", "| 연도 | Top20 겹침 | 순서 동일 | 스냅샷 수 |", "|---|---|---|---|"]
    for y, v in r5["by_year"].items():
        L.append(f"| {y} | {100 * v['overlap20']:.2f}% | {v['same_order_pct']:.1f}% | {v['snapshots']} |")
    L += ["\n### Top 20 진입 이벤트 (첫 진입 + 재진입, 재진입 기준 2,000건)\n",
          "| θ | 이벤트 (정확 / 근사) | 재현율 | 정밀도 | 같은 스냅샷 | 더 이르게 | 더 늦게 | 시점차 절댓값 중앙값 / p95 / 최대 | 늦은 경우 중앙값 / p95 / 최대 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in e1r["rows"]:
        e = r["events"]
        L.append(f"| {r['theta']} | {e['events_exact']:,} / {e['events_approx']:,} | {100 * e['recall']:.1f}% | {100 * e['precision']:.1f}% | "
                 f"{e['same_snapshot_pct']:.1f}% | {e['early_pct']:.1f}% | {e['late_pct']:.1f}% | "
                 f"{e['abs_offset_median']} / {e['abs_offset_p95']} / {e['abs_offset_max']} | "
                 f"{dash(e['late_median'])} / {dash(e['late_p95'])} / {dash(e['late_max'])} |")
    L += [f"\n시점차 단위는 기사 수이며 스냅샷 간격({SNAP}건)보다 작은 차이는 구분할 수 없다(기사 한 건 단위 비교는 E6). "
          "부호 있는 평균은 이르고 늦은 경우가 상쇄되므로 쓰지 않는다. '더 이르게'가 생기는 이유: 제거 방식은 점수를 과소추정할 뿐이지만, "
          "경쟁 키워드가 더 많이 과소추정되면 상대 순위가 먼저 오를 수 있다.\n",
          "### 순위 역전 사례 (θ = 0.9)\n"]
    for ex in next(r for r in e1r["rows"] if r["theta"] == 0.9)["rank_flip_examples"]:
        d = e1r["curves"]["dates"][ex["snapshot"]]
        L.append(f"- {d}: 정확 {ex['exact'][0]}={ex['exact'][1]} > {ex['exact'][2]}={ex['exact'][3]}, 근사 {ex['approx'][0]} < {ex['approx'][1]}")
    L += ["\n## E2. 감쇠 상수 c (θ = 0.5)\n",
          "| c | 1/c | 반감기 | 저장 키워드 (근사 / 정확) | 감소율 | Top20 겹침 | 정확 기준과 순서 동일 | 시간 안정성 (100건 후) | 시간 안정성 (1,000건 후) | 이벤트 재현율 / 정밀도 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["E2"]["rows"]:
        L.append(f"| {r['c']} | {r['scale_1_over_c']:,} | {r['half_life']:,} | {r['keywords_mean_approx']:,} / {r['keywords_mean_exact']:,} | "
                 f"{r['keyword_reduction_pct']:.1f}% | {100 * r['topk_overlap_20_mean']:.2f}% | {r['same_order_pct']:.1f}% | "
                 f"{100 * r['stability_100']:.1f}% | {100 * r['stability_1000']:.1f}% | "
                 f"{100 * r['event_recall']:.1f}% / {100 * r['event_precision']:.1f}% |")
    L += ["\n'정확 기준과 순서 동일'은 근사가 정확 계산을 얼마나 재현하는지, '시간 안정성'은 근사 순위의 Top 20이 100건·1,000건 뒤에도 "
          "얼마나 남아 있는지다(서로 다른 개념). 1/c는 감쇠의 대표 규모일 뿐 경계가 있는 창이 아니며, 긴 스트림에서 최근 1/c건이 "
          "전체 가중치의 약 63%를 차지한다.\n"]
    e3r = res["E3"]
    L += [f"## E3. DGIM vs 효율적 정확 구간 집계 (추적 키워드 {e3r['tracked_keywords']:,}개, {e3r['eval_every']}건마다 평가)\n",
          "| N | r | 추정 | 평균 상대오차 | 등장 1-4 | 5-9 | 10+ | p95 | 최대 / 상한 | 논리 bit (DGIM / 정확) | 논리 비율 | Python 객체 (DGIM / 정확) | 객체 비율 | Top10 겹침 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in e3r["rows"]:
        b = r["rel_err_by_count"]
        pyd = f"{r['pybytes_dgim'] / 1e3:,.0f} KB" if r["pybytes_dgim"] else "-"
        pye = f"{r['pybytes_exact'] / 1e3:,.0f} KB" if r["pybytes_exact"] else "-"
        L.append(f"| {r['N']:,} | {r['r']} | {r['estimator']} | {100 * r['rel_err_mean']:.1f}% | {fmtp(b['1-4'])} | "
                 f"{fmtp(b['5-9'])} | {fmtp(b['10+'])} | {100 * r['rel_err_p95']:.1f}% | "
                 f"{100 * r['rel_err_max']:.1f}% / {100 * r['bound']:.1f}% | {r['logical_bits_dgim']:,.0f} / {r['logical_bits_exact']:,.0f} | "
                 f"{r['logical_dgim_vs_exact_pct']:.0f}% | {pyd} / {pye} | "
                 f"{r.get('pybytes_dgim_vs_exact_pct', 0):.0f}% | {100 * r['top10_overlap_mean']:.1f}% |")
    L += ["\n- 논리 bit = 압축 저장을 가정한 모델(DGIM: 버킷당 log₂N + log₂log₂N bit, 정확: min(등장 수·log₂N, N) bit; "
          "정확 쪽 구현은 비트맵으로 실제 전환하지 않음). 실제 구현은 Python 리스트·deque와 절대 타임스탬프를 쓰므로 "
          "'Python 객체' 열이 실제 구현 크기다.",
          "- midpoint와 textbook은 가장 오래된 버킷에 대해 항상 0.5 차이가 난다. 차이는 **등장 1-4회 구간에서 크고** 10회 이상에서는 거의 같다. "
          "전체 평균 개선이 순위 품질 개선으로 이어지지 않는다(Top10 겹침 동일).\n",
          "### DGIM 순위 역전 사례 (N=1000, r=2)\n"]
    for ex in e3r["rank_flip_examples"]:
        L.append(f"- 기사 #{ex['t']:,}: 정확 {ex['higher']}={ex['exact'][0]} > {ex['lower']}={ex['exact'][1]} 인데 DGIM {ex['dgim'][0]} < {ex['dgim'][1]}")
    e4r = res["E4"]

    def mr(x, unit=""):
        return f"{x['median']:,.0f}{unit} ({x['min']:,.0f}–{x['max']:,.0f})"

    def mrf(x, unit=""):
        return f"{x['median']:.2f}{unit} ({x['min']:.2f}–{x['max']:.2f})"

    L += [f"\n## E4. 처리 성능 (작업 범위를 맞춘 측정, {e4r['repeats']}회 반복 중앙값 (최소–최대))\n",
          "| 측정 | 정확 기준 | 근사 θ=0.5 | 근사 θ=0.1 |", "|---|---|---|---|",
          f"| 입력 갱신만 (기사/s) | {mr(e4r['update_exact'])} | {mr(e4r['update_approx_0.5'])} | {mr(e4r['update_approx_0.1'])} |",
          f"| Top {K} 조회 1회 (ms) | {mrf(e4r['topk_query_exact'])} | {mrf(e4r['topk_query_approx_0.5'])} | {mrf(e4r['topk_query_approx_0.1'])} |",
          f"| 조회 시 저장 키워드 수 | {e4r['topk_query_exact']['keywords']:,} | {e4r['topk_query_approx_0.5']['keywords']:,} | {e4r['topk_query_approx_0.1']['keywords']:,} |",
          "", "| 측정 | DGIM (N=1000, r=2) | 정확 구간 집계 (N=1000) |", "|---|---|---|",
          f"| 입력 갱신만 (기사/s) | {mr(e4r['update_dgim_N1000_r2'])} | {mr(e4r['update_exact_window_N1000'])} |",
          f"| 추적 키워드 전체 조회 1회 (ms) | {mrf(e4r['query_all_dgim_N1000_r2'])} | {mrf(e4r['query_all_exact_window_N1000'])} |",
          "",
          f"- 엔진 전체(모든 집계 + 50건마다 스냅샷, {e4r['engine_step']['start_date']}부터 {e4r['engine_step']['articles']:,}건): "
          f"{mr(e4r['engine_step'])} 기사/s (단어만) · {mr(e4r['engine_step_v03'])} 기사/s (v0.3 기본: 단어+구, 급상승 창 포함)"
          if "engine_step_v03" in e4r else f"{mr(e4r['engine_step'])} 기사/s",
          f"- API 응답(v0.3 기본 설정, Flask 테스트 클라이언트, 네트워크 제외, 30회): `/api/state` {mrf(e4r['api_state_ms'], ' ms')}, "
          f"`/api/step`(50건) {mrf(e4r['api_step50_ms'], ' ms')}",
          "- 입력 갱신 측정에는 스냅샷·조회·평가 작업이 없다. 조회는 입력이 끝난 상태에서 따로 잰다. "
          "Top K 조회는 저장된 키워드 전체를 훑으므로(heapq.nlargest) 저장 키워드 수에 비례한다.\n"]
    e5r = res["E5"]
    L += [f"## E5. 같은 날짜 안 기사 순서의 영향 (θ=0.5, 하루 끝 시점 비교, {e5r['days_evaluated']:,}일)\n",
          "| 섞기 seed | Top20 겹침 평균 | 최저 | 순서 동일 | 첫 진입일 동일 | 첫 진입일 차이 p95(일) | 한쪽에만 진입한 키워드 |",
          "|---|---|---|---|---|---|---|"]
    for r in e5r["rows"]:
        L.append(f"| {r['seed']} | {100 * r['overlap20_mean']:.2f}% | {100 * r['overlap20_min']:.0f}% | {r['same_order_pct']:.1f}% | "
                 f"{r['first_entry_same_day_pct']:.1f}% | {r['first_entry_shift_p95_days']} | {r['keywords_only_in_one_order']} |")
    L += ["\n하루 끝 시점만 비교한다(하루 중간 시점은 순서에 따라 당연히 다르다).\n",
          "## E6. 실제 사건 사례 (요약 — 상세와 팀 평가 양식은 casestudies.md)\n",
          "| 사건 | 날짜 | 키워드 | 정확 Top20 진입 (일) | 근사 진입 (일) | 근사−정확 (기사) | 최고 순위 | 자동 판정 |",
          "|---|---|---|---|---|---|---|---|"]
    for r in res["E6"]["rows"]:
        L.append(f"| {r['event']} | {r['date']} | {r['keyword']} | {dash(r['exact_entry_day'])} | {dash(r['approx_entry_day'])} | "
                 f"{dash(r['approx_minus_exact_articles'])} | {dash(r['best_rank_approx'])} | "
                 f"{'성공' if r['success'] else '실패'}{' (사건 전부터 Top 20)' if r['already_in_top_before'] else ''} |")
    n_ok = sum(1 for r in res["E6"]["rows"] if r["success"])
    L.append(f"\n자동 판정 성공 {n_ok}/{len(res['E6']['rows'])} (사전 기준: 사건일~7일 안에 근사 Top 20 진입). 일 = 사건일 기준 경과 일수.\n")
    return "\n".join(L) + "\n"


def casestudies_md(res) -> str:
    c = res["E6"]["criteria"]
    L = ["# 사례 평가: 실제 사건을 화제 키워드로 발견했는가\n",
         f"- 사전에 정한 사건 {len(res['E6']['rows'])}개, 사건마다 키워드 1개. 관찰 기간: 사건일 {c['window_days'][0]}일 ~ +{c['window_days'][1]}일.",
         f"- 자동 판정 기준: 사건일부터 {c['success_days']}일 안에 근사(c={c['c']}, θ={c['theta']}) Top {c['k']}에 진입하면 성공.",
         "- 진입 시점은 **기사 한 건 단위**로 비교했다(관찰 기간의 매 기사마다 두 방식의 Top 20 확인).",
         "- `팀 평가` 열은 사람이 채운다: 키워드가 사건을 이해할 수 있는 표현인가(표현), 대표 기사가 사건을 뒷받침하는가(근거). 1~3점.\n",
         "| 사건 | 키워드 | 첫 등장 | 정확 진입 | 근사 진입 | 근사−정확 (기사) | 하루 기사 수 | 자동 판정 | 팀 평가: 표현 | 팀 평가: 근거 |",
         "|---|---|---|---|---|---|---|---|---|---|"]

    def d(x):
        return "-" if x is None else (f"+{x}일" if x >= 0 else f"{x}일")

    for r in res["E6"]["rows"]:
        note = " (사건 전부터 Top 20)" if r["already_in_top_before"] else ""
        L.append(f"| {r['event']} ({r['date']}) | {r['keyword']} | {d(r['first_mention_day'])} | {d(r['exact_entry_day'])} | "
                 f"{d(r['approx_entry_day'])} | {dash(r['approx_minus_exact_articles'])} | "
                 f"{r['articles_per_day_in_window']} | {'성공' if r['success'] else '실패'}{note} | | |")
    L.append("\n## 근사 Top 20 진입 시점의 대표 기사와 함께 등장한 단어\n")
    for r in res["E6"]["rows"]:
        L.append(f"### {r['event']} — `{r['keyword']}`")
        if not r["evidence"]:
            L.append("- 관찰 기간 안에 근사 Top 20 진입 없음")
            if r["keyword"] in FAILURE_NOTES:
                L.append(f"- 원인 메모(사람 작성): {FAILURE_NOTES[r['keyword']]}")
            L.append("")
            continue
        L.append(f"- 함께 등장: {', '.join(r['cooccurring'])}")
        for ev in r["evidence"]:
            L.append(f"- [#{ev['id']}] {ev['date']} · {ev['headline']}")
        L.append("")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="first 60k articles only")
    ap.add_argument("--figures-only", action="store_true", help="redraw figures/markdown from results.json")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.figures_only:
        res = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    else:
        arts = load_stream()
        if args.quick:
            arts = arts[:60000]
        docs = [a.tokens for a in arts]
        dates = [a.date for a in arts]
        res = {"articles": len(arts), "first_date": dates[0], "last_date": dates[-1], "quick": args.quick,
               "meta": {"python": platform.python_version(), "platform": platform.platform(),
                        "cpus": os.cpu_count(), "run_at": time.strftime("%Y-%m-%d %H:%M")}}
        t0 = time.time()
        res["E0"] = quality_report()
        res["E1"] = e1(docs, dates)
        res["E2"] = e2(docs, dates)
        base = run_decay(docs, 1e-3, None, mem_every=10 ** 12)
        tracked = sorted({w for top in base["tops"] for w, _ in top})
        res["E3"] = e3(docs, tracked)
        res["E4"] = e4(docs, arts, tracked, 2 if args.quick else REPEATS)
        res["E5"] = e5(arts)
        res["E6"] = e6(arts)
        res["elapsed_sec"] = round(time.time() - t0, 1)
        (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "summary.md").write_text(summary_md(res), encoding="utf-8")
    (OUT / "casestudies.md").write_text(casestudies_md(res), encoding="utf-8")
    figures(res)
    print(f"done -> {OUT}")


if __name__ == "__main__":
    main()
