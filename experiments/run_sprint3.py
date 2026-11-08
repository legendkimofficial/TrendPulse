"""Sprint 3 (v0.3) experiments.

    python experiments/run_sprint3.py              # full run (~10 min)
    python experiments/run_sprint3.py --figures-only

Outputs  experiments/results/v03/results.json, summary.md, figures/*.png

E7   phrases (two-word keywords) vs single words: memory, agreement with the
     exact computation, update speed, example rankings
E8   per-day decay vs per-article decay: reaction in sparse years, evidence
     behind Top 20 keywords per year
E9   burst detection: does it surface events earlier than the Top 20, and
     does it show keywords the Top 20 does not
E10  rank-uncertainty flags: guarantee check (no unflagged swap) and how much
     tighter per-keyword bounds are than the global bound; memory cost
E11  case studies: v0.2's 16 events (keyword alternatives added *after* seeing
     v0.2 results) + 10 held-out events registered before this run

All runs use c = 0.001 (article) or a 7-day half-life (day), theta = 0.5, K = 20.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import time
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date as Date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trendpulse.data import load_stream  # noqa: E402
from trendpulse.decaying import DecayingWindow  # noqa: E402
from trendpulse.memory import deep_sizeof  # noqa: E402
from trendpulse.ranking import ABSORB_RATIO, absorb, burst_scores, outside_candidates, uncertainty  # noqa: E402

OUT = ROOT / "experiments" / "results" / "v03"
FIG = OUT / "figures"

K = 20
SNAP = 100
C_ARTICLE = 1e-3
HALF_LIFE_DAYS = 7.0
THETA = 0.5
BURST_RATIO = 7.0
BURST_MIN = 3.0
BURST_K = 10
MEM_EVERY = 20_000
SUCCESS_DAYS, BEFORE, AFTER = 7, 7, 14

# (date, expressions, label, group). The first expression is the v0.2 keyword.
# group "v0.2": the 16 events of v0.2; the extra expressions were added AFTER seeing
#               the v0.2 results (post-hoc, e.g. "school shooting" for Parkland).
# group "holdout": registered on 2026-11-08 before this script was first run.
EVENTS = [
    ("2012-10-29", ["sandy", "hurricane sandy"], "허리케인 샌디 상륙", "v0.2"),
    ("2012-12-14", ["newtown", "sandy hook"], "샌디훅 초등학교 총격", "v0.2"),
    ("2013-04-15", ["boston", "boston marathon"], "보스턴 마라톤 폭탄 테러", "v0.2"),
    ("2013-07-13", ["zimmerman", "george zimmerman"], "짐머먼 무죄 평결", "v0.2"),
    ("2014-03-08", ["malaysia", "malaysia airlines"], "말레이시아항공 MH370 실종", "v0.2"),
    ("2014-08-09", ["ferguson", "michael brown"], "퍼거슨 사건", "v0.2"),
    ("2014-09-30", ["ebola"], "미국 첫 에볼라 확진", "v0.2"),
    ("2015-06-17", ["charleston", "charleston shooting"], "찰스턴 교회 총격", "v0.2"),
    ("2016-02-13", ["scalia", "antonin scalia"], "스캘리아 대법관 사망", "v0.2"),
    ("2016-06-12", ["orlando", "orlando shooting"], "올랜도 나이트클럽 총격", "v0.2"),
    ("2016-06-23", ["brexit"], "브렉시트 국민투표", "v0.2"),
    ("2017-08-12", ["charlottesville"], "샬러츠빌 집회 폭력", "v0.2"),
    ("2018-02-14", ["parkland", "school shooting", "florida school"], "파크랜드 고교 총격", "v0.2"),
    ("2020-03-11", ["coronavirus", "coronavirus pandemic"], "WHO 팬데믹 선언", "v0.2"),
    ("2020-05-25", ["floyd", "george floyd"], "조지 플로이드 사망", "v0.2"),
    ("2022-02-24", ["ukraine", "russia ukraine"], "러시아의 우크라이나 침공", "v0.2"),
    ("2015-11-13", ["paris", "paris attacks"], "파리 테러", "holdout"),
    ("2017-08-25", ["harvey", "hurricane harvey"], "허리케인 하비", "holdout"),
    ("2017-09-20", ["maria", "puerto rico"], "허리케인 마리아", "holdout"),
    ("2017-10-05", ["weinstein", "harvey weinstein"], "와인스타인 보도", "holdout"),
    ("2018-09-16", ["kavanaugh", "brett kavanaugh"], "캐버노 의혹 보도", "holdout"),
    ("2019-04-15", ["notre", "notre dame"], "노트르담 화재", "holdout"),
    ("2019-09-24", ["impeachment", "impeachment inquiry"], "트럼프 탄핵 조사 착수", "holdout"),
    ("2020-01-26", ["kobe", "kobe bryant"], "코비 브라이언트 사망", "holdout"),
    ("2021-01-06", ["capitol", "capitol riot"], "미국 의사당 난입", "holdout"),
    ("2022-06-24", ["roe", "abortion rights"], "로 대 웨이드 판결 폐기", "holdout"),
]

CONFIGS = {
    "uni_article": {"phrases": False, "unit": "article", "label": "단어 · 기사 수 감쇠 (v0.2)"},
    "phr_article": {"phrases": True, "unit": "article", "label": "단어+구 · 기사 수 감쇠"},
    "phr_day": {"phrases": True, "unit": "day", "label": "단어+구 · 날짜 감쇠 7일 (v0.3 기본)"},
}


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def decay_c(unit):
    return 1.0 - 0.5 ** (1.0 / HALF_LIFE_DAYS) if unit == "day" else C_ARTICLE


def short_c(unit):
    hl = math.log(0.5) / math.log(1 - decay_c(unit))
    return 1.0 - 0.5 ** (1.0 / (hl / BURST_RATIO))


def ranked(window, k, phrases):
    if not phrases:
        return window.top_k(k)
    return absorb(window.top_k(3 * k), k, ABSORB_RATIO)


class Stream:
    """Articles + per-config token lists + calendar helpers."""

    def __init__(self, arts):
        self.arts = arts
        self.uni = [a.tokens for a in arts]
        self.phr = [a.tokens + a.phrases for a in arts]
        self.day = [Date.fromisoformat(a.date).toordinal() for a in arts]
        self.steps_day = [0] + [self.day[i] - self.day[i - 1] for i in range(1, len(arts))]
        self.dates = [a.date for a in arts]

    def docs(self, phrases):
        return self.phr if phrases else self.uni

    def steps(self, unit):
        return self.steps_day if unit == "day" else None

    def first_idx_of_day(self, ordinal):
        return bisect_left(self.day, ordinal)


def feed(window, docs, steps, i):
    window.add(docs[i], 1 if steps is None else steps[i])


# ---------------------------------------------------------------------- E7 phrases
def e7(S: Stream, repeats=3):
    start = int(10 / C_ARTICLE / SNAP)
    res = {"rows": {}, "examples": {}}
    example_dates = ["2013-04-17", "2016-02-16", "2020-05-29"]
    ex_idx = {S.first_idx_of_day(Date.fromisoformat(d).toordinal() + 1) - 1: d for d in example_dates}
    for name, phrases in (("uni_article", False), ("phr_article", True)):
        docs = S.docs(phrases)
        ap, ex = DecayingWindow(C_ARTICLE, THETA), DecayingWindow(C_ARTICLE)
        kw_a, kw_e, mem_a, mem_e, ov, same = [], [], [], [], [], []
        examples = {}
        for i in range(len(docs)):
            ap.add(docs[i])
            ex.add(docs[i])
            if (i + 1) % SNAP == 0 and (i + 1) // SNAP >= start:
                a = [w for w, _ in ranked(ap, K, phrases)]
                b = [w for w, _ in ranked(ex, K, phrases)]
                ov.append(len(set(a) & set(b)) / K)
                same.append(a == b)
                kw_a.append(len(ap))
                kw_e.append(len(ex))
            if (i + 1) % MEM_EVERY == 0 and i + 1 >= start * SNAP:
                mem_a.append(deep_sizeof([ap._vals, ap._heap]))
                mem_e.append(deep_sizeof([ex._vals]))
            if i in ex_idx:
                examples[ex_idx[i]] = [w for w, _ in ranked(ap, K, phrases)]
        speeds = []
        for _ in range(repeats):
            w = DecayingWindow(C_ARTICLE, THETA)
            t0 = time.perf_counter()
            for d in docs:
                w.add(d)
            speeds.append(len(docs) / (time.perf_counter() - t0))
        res["rows"][name] = {
            "keywords_approx": statistics.mean(kw_a), "keywords_exact": statistics.mean(kw_e),
            "keywords_exact_final": len(ex),
            "pybytes_approx": statistics.mean(mem_a), "pybytes_exact": statistics.mean(mem_e),
            "overlap20": statistics.mean(ov), "overlap20_min": min(ov), "same_order_pct": 100 * sum(same) / len(same),
            "update_rate_median": statistics.median(speeds), "update_rate_min": min(speeds),
            "update_rate_max": max(speeds), "tokens_per_article": statistics.mean(len(d) for d in docs),
        }
        res["examples"][name] = examples
        r = res["rows"][name]
        print(f"[E7] {name}: kw {r['keywords_approx']:.0f}/{r['keywords_exact']:.0f} "
              f"bytes {r['pybytes_approx'] / 1e6:.2f}/{r['pybytes_exact'] / 1e6:.2f} MB "
              f"overlap {r['overlap20']:.4f} same {r['same_order_pct']:.1f}% speed {r['update_rate_median']:,.0f}/s")
    return res


# ---------------------------------------------------------------------- E8/E9/E11 case runs
def case_run(S: Stream, cfg_name: str):
    """One pass for one configuration: per-article Top 20 / burst checks inside event
    windows, plus a day-end evidence check for E8."""
    cfg = CONFIGS[cfg_name]
    phrases, unit = cfg["phrases"], cfg["unit"]
    docs, steps = S.docs(phrases), S.steps(unit)
    long_w = DecayingWindow(decay_c(unit), THETA)
    short_w = DecayingWindow(short_c(unit), THETA)
    postings: dict[str, list[int]] = {}
    ev_state = []
    for d, exprs, label, group in EVENTS:
        o = Date.fromisoformat(d).toordinal()
        ev_state.append({"date": d, "exprs": exprs, "label": label, "group": group, "o": o,
                         "lo": o - BEFORE, "hi": o + AFTER, "top_entry": None, "burst_entry": None,
                         "top_expr": None, "burst_expr": None, "before": False, "best_rank": None,
                         "first_mention": None, "evidence": [], "burst_before": False})
    evidence_by_year: dict[str, list] = {}
    burst_novel: list[float] = []
    n = len(docs)
    for i in range(n):
        feed(long_w, docs, steps, i)
        feed(short_w, docs, steps, i)
        for w in docs[i]:
            postings.setdefault(w, []).append(i)
        o = S.day[i]
        day_end = i + 1 == n or S.day[i + 1] != o
        active = [e for e in ev_state if e["lo"] <= o <= e["hi"]]
        need_rank = bool(active) and (day_end or any(o >= e["o"] for e in active))
        if need_rank:
            top = [w for w, _ in ranked(long_w, K, phrases)]
            brows = burst_scores(short_w, long_w, BURST_MIN, 3 * BURST_K)
            if phrases:      # same rule as the engine: absorb on the excess values
                burst = [w for w, _ in absorb([(r["keyword"], r["excess"]) for r in brows], BURST_K, ABSORB_RATIO)]
            else:
                burst = [r["keyword"] for r in brows[:BURST_K]]
            for e in active:
                hit_t = next((x for x in e["exprs"] if x in top), None)
                hit_b = next((x for x in e["exprs"] if x in burst), None)
                if o < e["o"]:
                    if day_end and hit_t:
                        e["before"] = True
                    if day_end and hit_b:
                        e["burst_before"] = True
                    continue
                if e["first_mention"] is None and any(x in docs[i] for x in e["exprs"]):
                    e["first_mention"] = i
                if hit_t:
                    r = top.index(hit_t) + 1
                    e["best_rank"] = r if e["best_rank"] is None else min(e["best_rank"], r)
                    if e["top_entry"] is None:
                        e["top_entry"], e["top_expr"] = i, hit_t
                        e["evidence"] = [{"id": j, "date": S.dates[j], "headline": S.arts[j].headline}
                                         for j in reversed(postings.get(hit_t, [])[-3:])]
                if hit_b and e["burst_entry"] is None:
                    e["burst_entry"], e["burst_expr"] = i, hit_b
        # E8 evidence check + E9 novelty, once per day (after 2012-06 so windows are warm)
        if day_end and S.dates[i] >= "2012-06-01":
            top = [w for w, _ in ranked(long_w, K, phrases)]
            lo_idx = S.first_idx_of_day(o - 6)
            counts = [bisect_right(postings.get(w, []), i) - bisect_left(postings.get(w, []), lo_idx) for w in top]
            evidence_by_year.setdefault(S.dates[i][:4], []).append(
                (statistics.median(counts), sum(1 for x in counts if x <= 2) / len(counts)))
            if o % 7 == 0:
                b = [r["keyword"] for r in burst_scores(short_w, long_w, BURST_MIN, BURST_K)]
                if b:
                    burst_novel.append(sum(1 for w in b if w not in top) / len(b))
    rows = []
    for e in ev_state:
        def day_of(idx):
            return None if idx is None else S.day[idx] - e["o"]
        td, bd = day_of(e["top_entry"]), day_of(e["burst_entry"])
        rows.append({"event": e["label"], "date": e["date"], "group": e["group"], "exprs": e["exprs"],
                     "top_day": td, "top_expr": e["top_expr"], "burst_day": bd, "burst_expr": e["burst_expr"],
                     "in_top_before": e["before"], "in_burst_before": e["burst_before"],
                     "best_rank": e["best_rank"], "first_mention_day": day_of(e["first_mention"]),
                     "top_success": td is not None and td <= SUCCESS_DAYS,
                     "burst_success": bd is not None and bd <= SUCCESS_DAYS,
                     "evidence": e["evidence"]})
    ev = {y: {"median_mentions_7d": statistics.mean(m for m, _ in v),
              "thin_share": statistics.mean(t for _, t in v), "days": len(v)}
          for y, v in sorted(evidence_by_year.items())}
    return {"config": cfg_name, "label": cfg["label"], "rows": rows, "evidence_by_year": ev,
            "burst_novel_share": statistics.mean(burst_novel) if burst_novel else None}


# ---------------------------------------------------------------------- E10 uncertainty
def e10(S: Stream):
    start = int(10 / C_ARTICLE / SNAP)
    docs = S.uni
    ex = DecayingWindow(C_ARTICLE)
    ap = DecayingWindow(C_ARTICLE, THETA, track_error=True)
    plain = DecayingWindow(C_ARTICLE, THETA)
    B = ap.error_bound()
    pairs = flagged_key = flagged_glob = swaps = missed = 0
    last_flag_key = last_flag_glob = last_out = last_missed = snaps = 0
    mem_track, mem_plain = [], []
    for i, d in enumerate(docs):
        ex.add(d)
        ap.add(d)
        plain.add(d)
        if (i + 1) % MEM_EVERY == 0 and (i + 1) // SNAP >= start:
            mem_track.append(deep_sizeof([ap._vals, ap._heap, ap._born]))
            mem_plain.append(deep_sizeof([plain._vals, plain._heap]))
        if (i + 1) % SNAP or (i + 1) // SNAP < start:
            continue
        snaps += 1
        top = ap.top_k(K)
        errs = [ap.key_error(w) for w, _ in top]
        outside, complete = outside_candidates(ap, top)
        flags = uncertainty(top, errs, outside, B, complete)
        for j in range(K - 1):
            pairs += 1
            fk = flags[j]["swap_below"]
            fg = top[j][1] - top[j + 1][1] < B
            sw = ex.score(top[j][0]) < ex.score(top[j + 1][0])
            flagged_key += fk
            flagged_glob += fg
            swaps += sw
            missed += sw and not fk
        # last row: does some keyword outside the approx Top K beat it in the exact ranking?
        inside = {w for w, _ in top}
        exact_top = ex.top_k(K + 1)
        beaten = any(w not in inside and s > ex.score(top[-1][0]) for w, s in exact_top)
        last_flag_key += flags[-1]["swap_below"]
        # global bound: any kept keyword within B below the last row (or B itself) could overtake it
        last_flag_glob += bool(outside) or B > top[-1][1]
        last_out += beaten
        last_missed += beaten and not flags[-1]["swap_below"]
    res = {"snapshots": snaps, "pairs": pairs, "swaps": swaps, "missed_swaps": missed,
           "flag_rate_key": flagged_key / pairs, "flag_rate_global": flagged_glob / pairs,
           "flag_precision_key": swaps / flagged_key if flagged_key else None,
           "last_row_flag_rate_key": last_flag_key / snaps, "last_row_flag_rate_global": last_flag_glob / snaps,
           "last_row_dropouts": last_out, "last_row_missed": last_missed,
           "pybytes_with_tracking": statistics.mean(mem_track), "pybytes_without": statistics.mean(mem_plain)}
    print(f"[E10] pairs {pairs}, swaps {swaps}, missed {missed}, flag rate key {res['flag_rate_key']:.3f} "
          f"vs global {res['flag_rate_global']:.3f}; last-row dropouts {last_out}, missed {last_missed}; "
          f"memory +{100 * (res['pybytes_with_tracking'] / res['pybytes_without'] - 1):.1f}%")
    return res


# ---------------------------------------------------------------------- figures
COL = {"blue": "#2a78d6", "orange": "#eb6834", "aqua": "#1baf7a", "ink": "#0b0b0b",
       "ink2": "#52514e", "grid": "#e4e3df", "surface": "#fcfcfb", "ref": "#8a8984"}
SERIES = {"uni_article": COL["orange"], "phr_article": COL["blue"], "phr_day": COL["aqua"]}
MARK = {"uni_article": "s", "phr_article": "o", "phr_day": "^"}


def figures(res):
    from run_experiments import setup_mpl
    plt = setup_mpl()
    FIG.mkdir(parents=True, exist_ok=True)
    for old in FIG.glob("*.png"):
        old.unlink()

    # F8 phrases: memory (two panels, one unit each)
    r7 = res["E7"]["rows"]
    names = ["uni_article", "phr_article"]
    labels = ["단어만", "단어 + 구"]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
    for ax, key_a, key_e, title, scale, unit in (
            (axes[0], "keywords_approx", "keywords_exact", "저장 키워드 수 (평균)", 1, "개"),
            (axes[1], "pybytes_approx", "pybytes_exact", "Python 집계 객체 크기 (평균)", 1e6, "MB")):
        x = list(range(len(names)))
        a = [r7[n][key_a] / scale for n in names]
        e = [r7[n][key_e] / scale for n in names]
        ax.bar([i - 0.18 for i in x], e, width=0.34, color=COL["orange"], label="정확 기준", edgecolor=COL["surface"], linewidth=2)
        ax.bar([i + 0.18 for i in x], a, width=0.34, color=COL["blue"], label="근사 (θ=0.5)", edgecolor=COL["surface"], linewidth=2)
        for i in x:
            ax.annotate(f"{e[i]:,.1f}" if scale > 1 else f"{e[i]:,.0f}", (i - 0.18, e[i]), textcoords="offset points",
                        xytext=(0, 3), ha="center", fontsize=9)
            ax.annotate(f"{a[i]:,.1f}" if scale > 1 else f"{a[i]:,.0f}", (i + 0.18, a[i]), textcoords="offset points",
                        xytext=(0, 3), ha="center", fontsize=9)
        ax.set_xticks(x, labels)
        ax.set_title(title)
        ax.set_ylabel(unit)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(FIG / "f8_phrase_memory.png", dpi=180)
    plt.close(fig)

    # F9 case studies across configurations
    runs = res["E11"]
    rows0 = runs["uni_article"]["rows"]
    fig, ax = plt.subplots(figsize=(10.5, 7.6))
    ys = list(range(len(rows0)))[::-1]
    offs = {"uni_article": 0.22, "phr_article": 0.0, "phr_day": -0.22}
    for name in ("uni_article", "phr_article", "phr_day"):
        xs, yy = [], []
        for y, r in zip(ys, runs[name]["rows"]):
            if r["top_day"] is not None:
                xs.append(r["top_day"])
                yy.append(y + offs[name])
        ax.scatter(xs, yy, s=46, color=SERIES[name], marker=MARK[name], label=CONFIGS[name]["label"],
                   edgecolor=COL["surface"], linewidth=1, zorder=3)
    ax.axvspan(0, SUCCESS_DAYS, color=COL["blue"], alpha=0.06, lw=0)
    ax.axvline(0, color=COL["ref"], linestyle=(0, (4, 3)), linewidth=1.2)
    split = sum(1 for r in rows0 if r["group"] == "v0.2")
    ax.axhline(ys[split - 1] - 0.5, color=COL["ink2"], linewidth=0.8)
    ax.annotate("사전 등록 사건 10개 (v0.3)", (AFTER, ys[split - 1] - 0.5), xytext=(0, -12), textcoords="offset points",
                ha="right", fontsize=9, color=COL["ink2"])
    ax.set_yticks(ys, [f"{r['exprs'][0]} ({r['date']})" for r in rows0])
    ax.set_xlim(-0.8, AFTER + 0.5)
    ax.set_xlabel("사건일 기준 경과 일수 (음영 = 성공 기준 7일, 점 없음 = 관찰 기간 내 진입 없음)")
    ax.set_title("사건 키워드가 Top 20에 들어온 날: 설정별 비교")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(FIG / "f9_case_configs.png", dpi=180)
    plt.close(fig)

    # F10 evidence behind Top 20 per year (article vs day decay)
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.9))
    for name in ("phr_article", "phr_day"):
        ev = runs[name]["evidence_by_year"]
        yrs = sorted(ev)
        axes[0].plot(yrs, [ev[y]["median_mentions_7d"] for y in yrs], color=SERIES[name], marker=MARK[name],
                     markersize=6, label=CONFIGS[name]["label"])
        axes[1].plot(yrs, [100 * ev[y]["thin_share"] for y in yrs], color=SERIES[name], marker=MARK[name],
                     markersize=6, label=CONFIGS[name]["label"])
    axes[0].set_title("Top 20 키워드의 최근 7일 기사 수 (중앙값)")
    axes[0].set_ylabel("기사 수")
    axes[0].set_yscale("log")
    axes[0].yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:g}"))
    axes[1].set_title("근거가 얇은 키워드 비율 (최근 7일 기사 ≤ 2건)")
    axes[1].set_ylabel("%")
    for ax, loc in zip(axes, ("lower left", "upper left")):
        ax.tick_params(axis="x", rotation=45)
        ax.legend(loc=loc, fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG / "f10_day_vs_article.png", dpi=180)
    plt.close(fig)

    # F11 burst vs Top 20 entry day (phrase, article decay)
    rows = runs["phr_article"]["rows"]
    fig, ax = plt.subplots(figsize=(10, 7.0))
    ys = list(range(len(rows)))[::-1]
    for y, r in zip(ys, rows):
        if r["top_day"] is not None:
            ax.scatter([r["top_day"]], [y + 0.12], s=50, color=COL["blue"], marker="o", zorder=3,
                       edgecolor=COL["surface"], linewidth=1)
        if r["burst_day"] is not None:
            ax.scatter([r["burst_day"]], [y - 0.12], s=50, color=COL["orange"], marker="D", zorder=3,
                       edgecolor=COL["surface"], linewidth=1)
    ax.scatter([], [], s=50, color=COL["blue"], marker="o", label="Top 20 진입")
    ax.scatter([], [], s=50, color=COL["orange"], marker="D", label="급상승 Top 10 진입")
    ax.axvspan(0, SUCCESS_DAYS, color=COL["blue"], alpha=0.06, lw=0)
    ax.axvline(0, color=COL["ref"], linestyle=(0, (4, 3)), linewidth=1.2)
    ax.set_yticks(ys, [f"{r['exprs'][0]} ({r['date']})" for r in rows])
    ax.set_xlim(-0.8, AFTER + 0.5)
    ax.set_xlabel("사건일 기준 경과 일수")
    ax.set_title("급상승 목록 vs Top 20 (단어+구, 기사 수 감쇠)")
    ax.legend(loc="upper right")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(FIG / "f11_burst_vs_top.png", dpi=180)
    plt.close(fig)

    # F12 uncertainty flags: how often each rule flags a pair vs how often pairs really swap
    r10 = res["E10"]
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    cats = ["전체 상한 θ/(1-θ)로 표시", "키워드별 상한으로 표시 (v0.3)", "실제로 순서가 바뀐 쌍"]
    vals = [100 * r10["flag_rate_global"], 100 * r10["flag_rate_key"], 100 * r10["swaps"] / r10["pairs"]]
    cols = [COL["orange"], COL["blue"], COL["ref"]]
    ax.bar(range(3), vals, width=0.55, color=cols, edgecolor=COL["surface"], linewidth=2)
    for i, v in enumerate(vals):
        ax.annotate(f"{v:.2f}%" if v < 10 else f"{v:.1f}%", (i, v), textcoords="offset points", xytext=(0, 3),
                    ha="center", fontsize=10)
    ax.set_xticks(range(3), cats, fontsize=10)
    ax.set_ylabel("Top 20 인접 쌍 중 %")
    ax.set_title(f"순위 불확실성 표시: 표시하지 않았는데 바뀐 쌍 {r10['missed_swaps']}건")
    fig.tight_layout()
    fig.savefig(FIG / "f12_uncertainty.png", dpi=180)
    plt.close(fig)


# ---------------------------------------------------------------------- summary
def dash(x):
    return "-" if x is None else x


def dd(x):
    return "-" if x is None else (f"+{x}일" if x >= 0 else f"{x}일")


def summary_md(res):
    m = res["meta"]
    L = ["# 스프린트 3 (v0.3) 실험 결과\n",
         f"- 데이터: {res['articles']:,}건 · 실행: Python {m['python']} / {m['platform']} / CPU {m['cpus']}개 · "
         f"{m['run_at']} · 소요 {res['elapsed_sec']}초",
         f"- 공통: θ={THETA}, K={K}, 기사 수 감쇠 c={C_ARTICLE}(반감기 693건) / 날짜 감쇠 반감기 {HALF_LIFE_DAYS}일, "
         f"급상승은 반감기 1/{BURST_RATIO:g}·최소 점수 {BURST_MIN:g}·Top {BURST_K}, 구 흡수 비율 {ABSORB_RATIO}\n"]
    r7 = res["E7"]["rows"]
    L += ["## E7. 두 단어 묶기 (구) — 기사 수 감쇠\n",
          "| 설정 | 기사당 토큰 | 저장 키워드 (근사 / 정확) | 감소율 | Python 객체 (근사 / 정확) | 감소율 | Top20 겹침 / 최저 | 순서 동일 | 입력 갱신 (기사/s, 중앙값) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for n, lab in (("uni_article", "단어만 (v0.2)"), ("phr_article", "단어 + 구 (v0.3)")):
        r = r7[n]
        L.append(f"| {lab} | {r['tokens_per_article']:.2f} | {r['keywords_approx']:,.0f} / {r['keywords_exact']:,.0f} | "
                 f"{100 * (1 - r['keywords_approx'] / r['keywords_exact']):.1f}% | {r['pybytes_approx'] / 1e6:.2f} / "
                 f"{r['pybytes_exact'] / 1e6:.2f} MB | {100 * (1 - r['pybytes_approx'] / r['pybytes_exact']):.1f}% | "
                 f"{100 * r['overlap20']:.2f}% / {100 * r['overlap20_min']:.0f}% | {r['same_order_pct']:.1f}% | "
                 f"{r['update_rate_median']:,.0f} ({r['update_rate_min']:,.0f}–{r['update_rate_max']:,.0f}) |")
    L += ["\n구를 넣으면 정확 기준이 기억해야 할 키워드가 크게 늘어나므로(마지막 시점 "
          f"{r7['phr_article']['keywords_exact_final']:,}개) 제거의 효과가 더 커진다. 대신 근사 쪽 저장량과 갱신 비용도 늘어난다.\n",
          "### 같은 시점의 Top 20 비교\n"]
    ex = res["E7"]["examples"]
    for d in sorted(ex["uni_article"]):
        L.append(f"- **{d}** 단어만: {', '.join(ex['uni_article'][d])}")
        L.append(f"  - 단어 + 구: {', '.join(ex['phr_article'][d])}")
    L.append("")
    runs = res["E11"]
    L += ["## E11. 사례 평가 (Top 20 진입, 사건일~7일 = 성공)\n",
          "| 설정 | v0.2 사건 16개 | 사전 등록 사건 10개 | 합계 |", "|---|---|---|---|"]
    for n in ("uni_article", "phr_article", "phr_day"):
        rows = runs[n]["rows"]
        a = sum(r["top_success"] for r in rows if r["group"] == "v0.2")
        b = sum(r["top_success"] for r in rows if r["group"] == "holdout")
        L.append(f"| {CONFIGS[n]['label']} | {a}/16 | {b}/10 | {a + b}/26 |")
    L += ["\n- v0.2 사건의 추가 표현(예: parkland → school shooting)은 **v0.2 결과를 본 뒤** 정한 것이라 사후 분석이다. "
          "공정한 비교는 사전 등록 사건 10개 열이다.",
          "- 사건 키워드는 사건별 표현 목록 중 **하나라도** Top 20에 들어오면 진입으로 본다.\n",
          "| 사건 | 그룹 | 표현 | 단어·기사 | 구·기사 | 구·날짜 | 급상승(구·기사) | 최고 순위(구·기사) |", "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(runs["uni_article"]["rows"]):
        p, dday = runs["phr_article"]["rows"][i], runs["phr_day"]["rows"][i]

        def cell(x):
            if x["in_top_before"]:
                return "사건 전부터"
            return dd(x["top_day"]) + (f" `{x['top_expr']}`" if x["top_expr"] else "")
        L.append(f"| {r['event']} ({r['date']}) | {'사전 등록' if r['group'] == 'holdout' else 'v0.2'} | {', '.join(r['exprs'])} | "
                 f"{cell(r)} | {cell(p)} | {cell(dday)} | "
                 f"{'사건 전부터' if p['in_burst_before'] else dd(p['burst_day'])} | {dash(p['best_rank'])} |")
    L += ["\n## E8. 날짜 기준 감쇠 vs 기사 수 기준 감쇠 (단어 + 구)\n",
          "| 연도 | 기사 수 감쇠: 최근 7일 기사 수 중앙값 | 근거 얇음(≤2건) 비율 | 날짜 감쇠: 최근 7일 기사 수 중앙값 | 근거 얇음 비율 |",
          "|---|---|---|---|---|"]
    ea, ed = runs["phr_article"]["evidence_by_year"], runs["phr_day"]["evidence_by_year"]
    for y in sorted(ea):
        L.append(f"| {y} | {ea[y]['median_mentions_7d']:.1f} | {100 * ea[y]['thin_share']:.1f}% | "
                 f"{ed[y]['median_mentions_7d']:.1f} | {100 * ed[y]['thin_share']:.1f}% |")
    L += ["\n하루 끝마다 Top 20 키워드 각각이 최근 7일 동안 몇 건의 제목에 나왔는지 센 값. 기사 수 감쇠는 희소 구간에서 "
          "몇 달 전 기사가 순위를 지탱하고(최근 근거가 얇음), 날짜 감쇠는 최근 기사만 반영하지만 기사가 적을 때 1~2건짜리 키워드가 순위에 오른다.\n",
          "## E9. 급상승 탐지 (단어 + 구, 기사 수 감쇠)\n"]
    rows = runs["phr_article"]["rows"]
    bs = sum(r["burst_success"] for r in rows)
    earlier = sum(1 for r in rows if r["burst_day"] is not None and (r["top_day"] is None or r["burst_day"] < r["top_day"]))
    same = sum(1 for r in rows if r["burst_day"] is not None and r["top_day"] is not None and r["burst_day"] == r["top_day"])
    only = sum(1 for r in rows if r["burst_success"] and not r["top_success"])
    L += [f"- 급상승 Top 10에 사건일~7일 안에 진입: **{bs}/26** (Top 20 진입은 {sum(r['top_success'] for r in rows)}/26)",
          f"- 급상승이 Top 20보다 이른 날 잡은 사건 {earlier}개, 같은 날 {same}개, 급상승에서만 잡힌 사건 {only}개",
          f"- 급상승 목록 중 같은 시점 Top 20에 없는 키워드 비율(주 1회 측정 평균): "
          f"{100 * runs['phr_article']['burst_novel_share']:.1f}% → Top 20과 다른 정보를 보여 준다",
          f"- 날짜 감쇠(v0.3 기본)에서는 급상승 Top 10 진입 {sum(r['burst_success'] for r in runs['phr_day']['rows'])}/26. "
          f"반감기 1일짜리 짧은 창이 희소 구간에서 최소 점수({BURST_MIN:g})를 넘기 어려워 목록이 비는 날이 있다\n"]
    r10 = res["E10"]
    L += ["## E10. 순위 불확실성 표시 (단어만, 기사 수 감쇠 — 원점수 기준 보장)\n",
          "| 항목 | 값 |", "|---|---|",
          f"| 평가 스냅샷 / Top 20 인접 쌍 | {r10['snapshots']:,} / {r10['pairs']:,} |",
          f"| 실제로 순서가 바뀐 쌍 (정확 기준과 비교) | {r10['swaps']:,} ({100 * r10['swaps'] / r10['pairs']:.2f}%) |",
          f"| **표시하지 않았는데 바뀐 쌍 (보장 위반)** | **{r10['missed_swaps']}** |",
          f"| 표시 비율: 전체 상한 θ/(1-θ) / 키워드별 상한 | {100 * r10['flag_rate_global']:.1f}% / {100 * r10['flag_rate_key']:.2f}% |",
          f"| 표시한 쌍 중 실제로 바뀐 비율 (키워드별) | {100 * r10['flag_precision_key']:.1f}% |" if r10["flag_precision_key"] is not None else "| 표시한 쌍 중 실제로 바뀐 비율 | - |",
          f"| 마지막 순위가 Top 20 밖으로 밀린 시점 / 그중 표시 안 한 것 | {r10['last_row_dropouts']} / **{r10['last_row_missed']}** |",
          f"| 마지막 순위 표시 비율: 전체 상한 / 키워드별 | {100 * r10['last_row_flag_rate_global']:.1f}% / {100 * r10['last_row_flag_rate_key']:.1f}% |",
          f"| 키워드별 상한을 위한 추가 메모리 (Python 객체) | {r10['pybytes_without'] / 1e6:.2f} → {r10['pybytes_with_tracking'] / 1e6:.2f} MB "
          f"(+{100 * (r10['pybytes_with_tracking'] / r10['pybytes_without'] - 1):.1f}%) |",
          "\n키워드별 상한: 키워드가 (다시) 들어온 뒤로 잃은 점수의 상한이 θ/(1-θ)·(1-c)^경과 로 줄어든다. 오래 머문 키워드는 사실상 정확하다. "
          "보장은 원점수 순위에 대한 것이며, 구 흡수로 숨겨지는 단어가 정확 계산과 달라지는 경우는 포함하지 않는다.\n"]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--figures-only", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.figures_only:
        res = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    else:
        arts = load_stream()
        S = Stream(arts)
        res = {"articles": len(arts), "meta": {"python": platform.python_version(), "platform": platform.platform(),
                                               "cpus": os.cpu_count(), "run_at": time.strftime("%Y-%m-%d %H:%M")},
               "events": [{"date": d, "exprs": e, "label": l, "group": g} for d, e, l, g in EVENTS]}
        t0 = time.time()
        res["E7"] = e7(S)
        res["E10"] = e10(S)
        res["E11"] = {}
        for name in CONFIGS:
            print(f"[E11] {name} ...")
            res["E11"][name] = case_run(S, name)
            rows = res["E11"][name]["rows"]
            print(f"      top success {sum(r['top_success'] for r in rows)}/26, burst {sum(r['burst_success'] for r in rows)}/26")
        res["elapsed_sec"] = round(time.time() - t0, 1)
        (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "summary.md").write_text(summary_md(res), encoding="utf-8")
    sys.path.insert(0, str(ROOT / "experiments"))
    figures(res)
    print(f"done -> {OUT}")


if __name__ == "__main__":
    main()
