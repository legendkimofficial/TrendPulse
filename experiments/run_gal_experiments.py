"""GDELT GAL submission experiments: decaying-window comparison and DGIM
measurement over the fixed real-data replay input.

    python experiments/run_gal_experiments.py            # full run
    python experiments/run_gal_experiments.py --thetas 0.1 0.3

Inputs / settings (also written into the results):
* data/processed/gdelt/articles.jsonl + manifest.json (input SHA-256, window,
  counts, gaps) produced by scripts/fetch_gdelt_gal.py — REAL GDELT GAL data,
  2026-11-09..2026-11-15 UTC, English articles only.
* Keyword extraction: trendpulse.text.tokenize (single words, document
  frequency), fixed for every variant.
* Snapshot schedule: one snapshot per observation group of the input at the
  experiment granularity ``--every`` (default 4 = one per hour; the raw batch
  cadence is 15 minutes). All variants share the identical snapshot times.
* No random sampling or seed is used anywhere: the input is a fixed file
  identified by its hash.

E1  7-day decaying ranking: keep-all baseline vs threshold pruning
    (theta list configurable). Same input, half-life (48h), window (7d),
    Top K (20) and snapshot times for every variant.
    Pruning policy (identical in tests/test_gal.py): applied at each snapshot
    after syncing scores at t; a dropped keyword loses its whole state and
    starts from 1 if it is mentioned again (lost mass is never re-subtracted).
    Score error policy: measured over the union of both variants' Top-K members
    and, at the end of the stream, over every keyword the baseline still holds;
    a keyword absent from the pruned table counts as score 0. Relative error
    uses |exact - pruned| / exact with exact > 0; exact == 0 cases are reported
    separately as violations (pruned may never exceed the exact score).
    Entry events: first/re-entry per variant; matched one-to-one per keyword by
    nearest snapshot within +-MATCH_SNAPS (delay resolution = one snapshot).
    Events in the last MATCH_SNAPS snapshots cannot have their delay observed
    fully and are reported as a separate "edge" group.
    Memory: deep_sizeof of the aggregation state only (keyword table incl.
    mention lists, floats, pointers); articles store and postings index are
    separate line items. deep_sizeof counts every reachable object once per
    call (shared objects are not double-counted within one call); keyword
    strings shared with the article store are included in each figure that
    reaches them — the method and timing are stated in the report.

E2  DGIM vs exact positions queue (sliding.ExactWindowCount) over the same
    article indices and the same tracking start (i = 0 for every keyword).
    Tracked keywords (selection rule, deterministic): the final baseline
    Top-10 plus the keywords with the highest / median / lowest total mention
    count among those with >= 100 mentions. Evaluated at every experiment
    snapshot; errors only over fully observed windows (observed == N).
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trendpulse.dgim import DGIM                       # noqa: E402
from trendpulse.gal import load_articles               # noqa: E402
from trendpulse.gal_engine import GalConfig, GalEngine  # noqa: E402
from trendpulse.memory import deep_sizeof              # noqa: E402
from trendpulse.sliding import ExactWindowCount        # noqa: E402

OUT = ROOT / "experiments" / "results" / "gdelt"
K = 20
MATCH_SNAPS = 4            # event matching tolerance in snapshots
MEM_EVERY = 20             # snapshots between deep_sizeof samples


def pct(x, d=1):
    return None if x is None else f"{100 * x:.{d}f}%"


def p95(xs):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]


def topk_events(snaps, start):
    """(snapshot_idx, keyword, kind) with the engine's first/re-entry rule:
    an event fires when a keyword is in the current Top K but not in the
    previous snapshot's; 'first' if it was in no earlier Top K."""
    last, seen, out = {}, set(), []
    for s, top in enumerate(snaps):
        for w in top:
            if s >= start and w not in last:
                out.append((s, w, "first" if w not in seen else "re-entry"))
            last[w] = s
            seen.add(w)
    return out


MATCH_TOL_S = 3600     # event matching tolerance: +-1 hour


def match_events(exact_e, approx_e, times):
    """Nearest one-to-one match per keyword within +-1h (times[s] = epoch s)."""
    by_word = {}
    for s, w, _ in approx_e:
        by_word.setdefault(w, []).append(s)
    used, pairs, unmatched = set(), [], []
    for s, w, kind in exact_e:
        cands = [x for x in by_word.get(w, [])
                 if abs(times[x] - times[s]) <= MATCH_TOL_S and (x, w) not in used]
        if cands:
            x = min(cands, key=lambda v: (abs(times[v] - times[s]), v))
            used.add((x, w))
            pairs.append((s, w, kind, x))
        else:
            unmatched.append((s, w, kind))
    spurious = [(s, w, k) for s, w, k in approx_e if (s, w) not in used]
    return pairs, unmatched, spurious


def e1_decaying(arts, thetas, every):
    print(f"[E1] variants: baseline + theta in {thetas} (all batch snapshots compared)")
    engines = {"baseline": GalEngine(arts, GalConfig(k=K, threshold=None))}
    for th in thetas:
        engines[f"theta={th}"] = GalEngine(arts, GalConfig(k=K, threshold=th))
    names = list(engines)
    times = []               # epoch seconds of each compared snapshot
    tops = {n: [] for n in names}
    mem = {n: [] for n in names}
    err = {n: {"abs": [], "rel": [], "viol_neg": 0} for n in names[1:]}
    t0 = time.time()
    snap_i = 0
    while True:
        progressed = False
        for eng in engines.values():
            if not eng.finished:
                eng.step(24)                      # ~6h of stream per chunk
                progressed = True
        idx = min(len(e.snapshots) for e in engines.values())
        while snap_i < idx:
            ref = engines["baseline"]
            times.append(ref.snapshots[snap_i]["t"])
            base_words = [r["kw"] for r in ref.snapshots[snap_i]["top"]]
            for n in names[1:]:
                pr = engines[n]
                pr_words = [r["kw"] for r in pr.snapshots[snap_i]["top"]]
                tops[n].append((base_words, pr_words))
                for w in set(base_words) | set(pr_words):
                    e = ref.kw[w].S if w in ref.kw else 0.0
                    p = pr.kw[w].S if w in pr.kw else 0.0
                    d = abs(e - p)
                    err[n]["abs"].append(d)
                    if e > 0:
                        err[n]["rel"].append(d / e)
                    elif p > 0:
                        err[n]["viol_neg"] += 1
            if snap_i % MEM_EVERY == 0:
                for n, eng in engines.items():
                    mem[n].append(deep_sizeof([eng.kw]))
            snap_i += 1
        if not progressed:
            break
    elapsed = time.time() - t0
    print(f"  replay done: {snap_i} snapshots compared in {elapsed:.0f}s")
    results = {"snapshot_cadence": "every observation batch (~15 min)",
               "snapshots": snap_i,
               "replay_elapsed_sec": round(elapsed, 1), "variants": {}}
    base_tops = [b for b, _ in tops[names[1]]]
    t0 = times[0]
    start_snap = next(i for i, t in enumerate(times) if t >= t0 + 144 * 3600)
    base_events = topk_events(base_tops, start_snap)
    for n in names[1:]:
        ov, same_order = [], []
        for i, (b, p) in enumerate(tops[n]):
            if i < start_snap:
                continue
            ov.append(len(set(b) & set(p)) / K)
            same_order.append(b == p)
        ev = topk_events([p for _, p in tops[n]], start_snap)
        pairs, unmatched, spurious = match_events(base_events, ev, times)
        offs = [times[x] - times[s] for s, w, k, x in pairs]     # seconds
        t_end = times[-1]
        edge = [e for e in base_events if times[e[0]] >= t_end - MATCH_TOL_S]
        kinds_base = {"first": sum(1 for _, _, k in base_events if k == "first"),
                      "re-entry": sum(1 for _, _, k in base_events if k == "re-entry")}
        kinds_matched = {"first": sum(1 for _, _, k, _ in pairs if k == "first"),
                         "re-entry": sum(1 for _, _, k, _ in pairs if k == "re-entry")}
        results["variants"][n] = {
            "theta": float(n.split("=")[1]),
            "topk_overlap_mean": round(statistics.mean(ov), 4),
            "topk_overlap_min": round(min(ov), 3),
            "same_order_pct": round(100 * sum(same_order) / len(same_order), 1),
            "abs_err_mean": round(statistics.mean(err[n]["abs"]), 4),
            "abs_err_max": round(max(err[n]["abs"]), 4),
            "rel_err_mean": round(statistics.mean(err[n]["rel"]), 4),
            "rel_err_p95": round(p95(err[n]["rel"]), 4),
            "rel_err_max": round(max(err[n]["rel"]), 4),
            "pruned_exceeds_exact_cases": err[n]["viol_neg"],
            "keywords_baseline_final": len(engines["baseline"].kw),
            "keywords_variant_final": len(engines[n].kw),
            "keywords_variant_peak": engines[n].peak_kw,
            "dropped_events": engines[n].dropped_events,
            "mem_bytes_mean": round(statistics.mean(mem[n])),
            "mem_bytes_baseline_mean": round(statistics.mean(mem["baseline"])),
            "mem_reduction_pct": round(100 * (1 - statistics.mean(mem[n]) / statistics.mean(mem["baseline"])), 1),
            "events_baseline": len(base_events), "events_variant": len(ev),
            "events_matched": len(pairs),
            "recall": round(len(pairs) / len(base_events), 4) if base_events else 1.0,
            "precision": round(len(pairs) / len(ev), 4) if ev else 1.0,
            "kinds_baseline": kinds_base, "kinds_matched": kinds_matched,
            "matched_same_snapshot_pct": round(100 * sum(1 for o in offs if o == 0) / len(offs), 1) if offs else 0,
            "matched_offset_median_min": round(statistics.median(offs) / 60, 1) if offs else None,
            "matched_offset_max_min": round(max(offs) / 60, 1) if offs else None,
            "match_tolerance": "+-60 min",
            "unmatched_edge_events": len(edge),
            "unmatched_other": len(unmatched) - len(edge) if unmatched else 0,
            "spurious_variant_events": len(spurious),
        }
        r = results["variants"][n]
        print(f"  {n}: overlap {r['topk_overlap_mean']:.4f} (min {r['topk_overlap_min']}), "
              f"|err| mean {r['abs_err_mean']} max {r['abs_err_max']}, "
              f"mem -{r['mem_reduction_pct']}%, events R {r['recall']:.3f} P {r['precision']:.3f}")
    # separate line items: article store + postings index (one engine)
    arts_bytes = deep_sizeof([arts])
    posts = engines["baseline"].postings
    posts_bytes = deep_sizeof([posts])
    results["line_items"] = {
        "article_store_bytes": arts_bytes,
        "postings_index_bytes": posts_bytes,
        "note": "deep_sizeof at end of run; article store = GalArticle objects incl. "
                "title/desc/URL strings and token tuples; postings = dict[str, array('i')]. "
                "Aggregation-state bytes (per variant) are sampled every "
                f"{MEM_EVERY} snapshots and averaged.",
    }
    return results


def e2_dgim(arts, every):
    print(f"[E2] DGIM over the same replay input (every={every})")
    # deterministic keyword selection (rule stated in the module docstring)
    counts = {}
    for a in arts:
        for w in a.tokens:
            counts[w] = counts.get(w, 0) + 1
    eligible = sorted((c, w) for w, c in counts.items() if c >= 100)
    top_freq = eligible[-1]
    median_freq = eligible[len(eligible) // 2]
    low_freq = eligible[min(50, len(eligible) - 1)]
    # final-baseline Top-10 comes from a quick baseline replay (hourly snapshots)
    eng = GalEngine(arts, GalConfig(k=10))
    eng.replay_all(every=4)
    final_top10 = [r["kw"] for r in eng.snapshots[-1]["top"]]
    tracked = list(dict.fromkeys(final_top10 + [top_freq[1], median_freq[1], low_freq[1]]))
    print(f"  tracked ({len(tracked)}): {tracked} | mention counts "
          f"top={top_freq[0]:,} median={median_freq[0]:,} low={low_freq[0]:,}")
    rows = []
    for N, r in ((200, 2), (1000, 2), (1000, 3), (5000, 2)):
        dg = {w: DGIM(N, r) for w in tracked}
        ex = {w: ExactWindowCount(N) for w in tracked}
        abs_errs, rel_errs, buckets, dg_bits, ex_bits = [], [], [], [], []
        zero_fp = 0
        exact_mismatch = 0
        dg_py, ex_py = [], []
        pos = 0
        snap_i = 0
        while pos < len(arts):
            t_batch = arts[pos].obs
            while pos < len(arts) and arts[pos].obs == t_batch:
                for w in arts[pos].tokens:
                    d = dg.get(w)
                    if d is not None:
                        d.add(pos)
                        ex[w].add(pos)
                pos += 1
            snap_i += 1
            if snap_i % every or pos < N:
                continue
            obs_n = pos
            for w in tracked:
                truth = sum(1 for a in arts[max(0, obs_n - N):obs_n] if w in a.tokens)
                if ex[w].count(obs_n - 1) != truth:
                    exact_mismatch += 1
                est = dg[w].estimate(obs_n - 1)
                if truth == 0:
                    if est != 0.0:
                        zero_fp += 1
                    continue
                abs_errs.append(abs(est - truth))
                rel_errs.append(abs(est - truth) / truth)
                buckets.append(dg[w].num_buckets)
                dg_bits.append(dg[w].memory_bits())
                ex_bits.append(ex[w].memory_bits())
            if snap_i % (MEM_EVERY * every) == 0:
                dg_py.append(deep_sizeof([dg]) / len(tracked))
                ex_py.append(deep_sizeof([ex]) / len(tracked))
        rows.append({
            "N": N, "r": r, "tracked": len(tracked), "eval_points": len(rel_errs),
            "abs_err_mean": round(statistics.mean(abs_errs), 3),
            "abs_err_max": round(max(abs_errs), 3),
            "rel_err_mean": round(statistics.mean(rel_errs), 4),
            "rel_err_p95": round(p95(rel_errs), 4),
            "rel_err_max": round(max(rel_errs), 4),
            "rel_err_bound": round(1 / (2 * (r - 1)), 3),
            "zero_truth_false_positive": zero_fp,
            "exact_queue_mismatch": exact_mismatch,
            "buckets_mean": round(statistics.mean(buckets), 1),
            "buckets_max": max(buckets),
            "theo_bits_dgim_mean": round(statistics.mean(dg_bits)),
            "theo_bits_exact_mean": round(statistics.mean(ex_bits)),
            "pybytes_dgim_mean": round(statistics.mean(dg_py)),
            "pybytes_exact_mean": round(statistics.mean(ex_py)),
        })
        rr = rows[-1]
        print(f"  N={N:,} r={r}: |err| mean {rr['abs_err_mean']} max {rr['abs_err_max']}, "
              f"rel {pct(rr['rel_err_mean'], 2)} p95 {pct(rr['rel_err_p95'], 2)} "
              f"max {pct(rr['rel_err_max'], 1)} / bound {pct(rr['rel_err_bound'], 0)}, "
              f"buckets {rr['buckets_mean']}, bits {rr['theo_bits_dgim_mean']:,} vs "
              f"{rr['theo_bits_exact_mean']:,}")
    return {"every": every, "tracked_keywords": tracked,
            "mention_counts": {"top": top_freq[0], "median": median_freq[0], "low": low_freq[0]},
            "rows": rows}


def summary_md(res):
    L = ["# GDELT GAL 제출 실험 결과\n",
         f"- 실행: `python experiments/run_gal_experiments.py` · {res['meta']['run_at']} · "
         f"Python {res['meta']['python']} / {res['meta']['platform']}",
         f"- 입력(실측 GDELT GAL): `{res['input']['path']}`",
         f"  SHA-256 `{res['input']['sha256']}`, 관측 창 {res['input']['window']}, "
         f"영어 기사 {res['input']['articles']:,}건, 수집 공백 {res['input']['gaps']} 구간",
         f"- 키워드 추출 규칙: `trendpulse.text.tokenize`(소문자·길이≥3·불용어·숫자 제외, 문서빈도 1회). "
         f"모든 변형이 동일 규칙·입력·반감기(48h)·기간(7일)·Top {K}·스냅샷 시각 사용.",
         f"- 스냅샷: {res['E1']['snapshot_cadence']} (E1은 전 배치 비교, E2는 --every={res['E2']['every']}배치=1시간 간격 평가). "
         f"지연 해상도 = 스냅샷 간격(15분).",
         "- 시드/표본 추출 없음: 고정 입력 파일을 해시로 식별해 전수 재생.\n",
         "## E1. 7일 감쇠 순위: 전체 유지 기준 vs 임계값 제거\n",
         "| θ | Top20 겹침 평균(최저) | 순서 동일 | 절대오차 평균/최대 | 상대오차 평균/p95/최대 | "
         "근사>기준 사례 | 저장 키워드(종료시) | 집계 상태 MB (근사/기준) | 감소율 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for n, r in res["E1"]["variants"].items():
        L.append(f"| {r['theta']} | {r['topk_overlap_mean']:.4f} ({r['topk_overlap_min']}) "
                 f"| {r['same_order_pct']}% | {r['abs_err_mean']} / {r['abs_err_max']} "
                 f"| {r['rel_err_mean']:.4f} / {r['rel_err_p95']:.4f} / {r['rel_err_max']:.4f} "
                 f"| {r['pruned_exceeds_exact_cases']} | {r['keywords_variant_final']:,} / "
                 f"{r['keywords_baseline_final']:,} | {r['mem_bytes_mean']/1e6:.1f} / "
                 f"{r['mem_bytes_baseline_mean']/1e6:.1f} | {r['mem_reduction_pct']}% |")
    li = res["E1"]["line_items"]
    L += ["",
          f"- 점수 오차 대상: 두 변형 Top-K 합집합(제거 테이블에 없으면 0점). 상대오차는 기준 점수>0일 때만, "
          f"기준 0 & 근사>0은 위반으로 별도 집계.",
          f"- 임계값 적용 시점: 각 스냅샷 동기화 직후. 제거된 키워드는 상태 전체 폐기(만료 큐 포함), "
          f"재등장 시 1부터 재시작(과거 기여 중복 차감 없음 — tests/test_gal.py PruningTest).",
          f"- 진입 이벤트: 허용 범위 ±{MATCH_TOL_S // 60}분, 키워드별 최근접 일대일 대응. "
          f"종료 ±60분 이내 사건은 지연 관측이 불충분해 'edge'로 구분.",
          f"- 메모리(별도 항목): 기사 저장 {li['article_store_bytes']/1e6:.0f} MB, "
          f"관련 기사 인덱스 {li['postings_index_bytes']/1e6:.0f} MB. {li['note']}\n",
          "## E1 이벤트 대응\n",
          "| θ | 기준 이벤트(first/re-entry) | 변형 이벤트 | 대응 | 재현율 | 정밀도 | "
          "대응 first/re-entry | 같은 스냅샷 | 시점차 중앙/최대(분) | 미대응(edge/기타) | 변형 단독 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, r in res["E1"]["variants"].items():
        kb = r["kinds_baseline"]
        km = r["kinds_matched"]
        L.append(f"| {r['theta']} | {r['events_baseline']} ({kb['first']}/{kb['re-entry']}) "
                 f"| {r['events_variant']} | {r['events_matched']} | {r['recall']:.3f} "
                 f"| {r['precision']:.3f} | {km['first']}/{km['re-entry']} "
                 f"| {r['matched_same_snapshot_pct']}% "
                 f"| {r['matched_offset_median_min']} / {r['matched_offset_max_min']} "
                 f"| {r['unmatched_edge_events']} / {r['unmatched_other']} "
                 f"| {r['spurious_variant_events']} |")
    L += ["\n## E2. DGIM vs 정확 슬라이딩 큐 (같은 추적 시작점 i=0, 같은 최근 N개)\n",
          "추적 키워드 선택 규칙: 최종 기준 Top-10 + 총 언급 수 최대/중위/최소(단 100회 이상). "
          f"대상: {', '.join(res['E2']['tracked_keywords'])} "
          f"(언급 수 top {res['E2']['mention_counts']['top']:,} / "
          f"median {res['E2']['mention_counts']['median']:,} / "
          f"low {res['E2']['mention_counts']['low']:,})",
          "",
          "| N | r | 평가 수 | 절대오차 평균/최대 | 상대오차 평균/p95/최대/상한 | 0구간 오탐 | "
          "정확 큐 불일치 | 버킷 평균/최대 | 논리 bit DGIM/정확 | Python B DGIM/정확 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["E2"]["rows"]:
        L.append(f"| {r['N']:,} | {r['r']} | {r['eval_points']:,} "
                 f"| {r['abs_err_mean']} / {r['abs_err_max']} "
                 f"| {r['rel_err_mean']:.4f} / {r['rel_err_p95']:.4f} / {r['rel_err_max']:.4f} / "
                 f"{r['rel_err_bound']:.3f} | {r['zero_truth_false_positive']} "
                 f"| {r['exact_queue_mismatch']} | {r['buckets_mean']} / {r['buckets_max']} "
                 f"| {r['theo_bits_dgim_mean']:,} / {r['theo_bits_exact_mean']:,} "
                 f"| {r['pybytes_dgim_mean']:,} / {r['pybytes_exact_mean']:,} |")
    L += ["",
          "- 논리 bit = `memory_bits()` 모델(버킷당 log₂N+log₂log₂N / 정확은 min(등장수·log₂N, N)). "
          "Python B = `deep_sizeof` 실제 객체 크기(키워드당 평균) — 두 단위는 별개로 해석.",
          "- 기존 HuffPost 실험(experiments/results/{,v03/})은 과거 기록으로 참고용이며 이번 실행과 무관함(재실행하지 않음; "
          "데이터 미보관 상태, 원본 로더는 data.py 참조).\n"]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thetas", nargs="*", type=float, default=[0.05, 0.1, 0.3])
    ap.add_argument("--every", type=int, default=4, help="batches per experiment snapshot")
    args = ap.parse_args()
    arts = load_articles()
    manifest = json.loads((ROOT / "data" / "processed" / "gdelt" / "manifest.json")
                          .read_text(encoding="utf-8"))
    res = {
        "meta": {"python": platform.python_version(), "platform": platform.platform(),
                 "run_at": time.strftime("%Y-%m-%d %H:%M")},
        "input": {"path": "data/processed/gdelt/articles.jsonl",
                  "sha256": manifest["input_sha256"],
                  "window": manifest["window"],
                  "articles": manifest["counts"]["kept"],
                  "gaps": len(manifest["collection_gaps"])},
    }
    t0 = time.time()
    res["E1"] = e1_decaying(arts, args.thetas, args.every)
    res["E2"] = e2_dgim(arts, args.every)
    res["elapsed_sec"] = round(time.time() - t0, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    (OUT / "summary.md").write_text(summary_md(res), encoding="utf-8")
    print(f"done -> {OUT} ({res['elapsed_sec']}s)")


if __name__ == "__main__":
    main()
