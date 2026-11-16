"""Verification of the decaying window and DGIM implementations
(branch ``project-verification``).

Run from the repository root:

    python verify/verify_algorithms.py                 # default synthetic stream
    python verify/verify_algorithms.py --articles 24000
    python verify/verify_algorithms.py --seed 20261116

Sections
--------
V1  Decaying window, small crafted inputs vs hand-summed weights
    (same-day articles, long time gaps, dropped-keyword reappearance, the
    theta pruning rule itself; article and day decay units).
V2  Decaying window, synthetic replay: keep-everything baseline vs pruning
    under the same article input / keyword extraction rule / decay rate.
    Stored keywords, Python aggregate bytes, Top-K member agreement,
    missed entry events and detection delay (with the end-of-stream
    "still waiting" vs "already left the baseline ranking" split),
    score error against the implementation's own bounds.
V3  DGIM, small inputs (no mention, window not yet full, old mentions
    expiring, bucket merging, duplicate timestamp, both estimators).
V4  DGIM, synthetic replay: several N and r over keywords with different
    frequencies; absolute/relative error, bucket counts, theoretical bits
    vs actual Python object size, compared with the efficient exact
    window count (sliding.ExactWindowCount) on the same window.
V5  Data source detection: local GDELT article data / HuffPost dataset /
    synthetic fallback, recorded per result.

Results are printed and written to verify/results_algorithms.{json,md}.
The script exits non-zero if any check fails. SKIPPED items (e.g. GDELT
data not present) do not fail the run.

Decay units supported by the current code (stated in every result):
``DecayingWindow.add(keywords, steps)`` decays by (1-c)**steps, and the
engine (EngineConfig.decay_unit) uses exactly two units:
  * "article": steps = 1 per article,
  * "day":     steps = calendar days since the previous article
               (0 for articles on the same day).
Both units are exercised below.
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
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trendpulse.data import RAW_PATH as HUFFPOST_RAW              # noqa: E402
from trendpulse.decaying import DecayingWindow                    # noqa: E402
from trendpulse.dgim import DGIM                                  # noqa: E402
from trendpulse.memory import deep_sizeof                         # noqa: E402
from trendpulse.sliding import ExactWindowCount                   # noqa: E402
from trendpulse.text import phrases, tokenize                     # noqa: E402

OUT_JSON = Path(__file__).with_name("results_algorithms.json")
OUT_MD = Path(__file__).with_name("results_algorithms.md")

K = 20                    # Top-K size, same as experiments/run_experiments.py
SNAP = 50                 # articles between ranking snapshots (delay resolution)
NEW_GAP = 20              # snapshots outside Top K before a return = re-entry
MEM_EVERY = 2_000         # articles between deep_sizeof samples
WARMUP_STEPS = 10.0       # skip the first 10/c decay steps (warm-up), as E1 does

THETAS_ARTICLE = [0.1, 0.5, 0.9]
THETA_DAY = 0.5
C_ARTICLE = 1e-3          # engine default; half-life ~ 693 articles
HALF_LIFE_DAYS = 7.0      # engine default for decay_unit="day"
C_DAY = 1.0 - 0.5 ** (1.0 / HALF_LIFE_DAYS)   # ~ 0.0943 per day

DGIM_NS = [500, 2000, 8000]
DGIM_RS = [2, 3, 5]
DGIM_EVAL = 250           # articles between DGIM evaluation points

# ---------------------------------------------------------------- check registry
CHECKS: list[dict] = []
REPORT: list[str] = []


def check(cid: str, desc: str, ok: bool | None, detail: str = "", repro: str | None = None,
          status: str | None = None) -> bool:
    """Record one verification item. ok=None with status='SKIP' -> not run."""
    st = status or ("PASS" if ok else "FAIL")
    CHECKS.append({"id": cid, "desc": desc, "status": st, "detail": detail, "repro": repro})
    mark = {"PASS": "[통과]", "FAIL": "[실패]", "SKIP": "[미실행]"}[st]
    print(f"  {mark} {cid} {desc}" + (f" -- {detail}" if detail else ""))
    return ok is True or st == "SKIP"


def table(headers: list[str], rows: list[list], title: str = "") -> None:
    if title:
        REPORT.append(f"\n#### {title}\n")
    REPORT.append("| " + " | ".join(headers) + " |")
    REPORT.append("|" + "---|" * len(headers))
    for r in rows:
        REPORT.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    REPORT.append("")


def pct(x, d=1):
    return None if x is None else f"{100 * x:.{d}f}%"


def pctq(xs, q, d=1):
    if not xs:
        return None
    xs = sorted(xs)
    return pct(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], d)


# ---------------------------------------------------------------- synthetic data
_REAL = """election court senate congress vaccine virus outbreak climate wildfire
flood storm earthquake economy inflation market jobs strike union school teacher
police reform immigration border refugee trial verdict jury lawsuit judge appeal
governor mayor council budget tax tariff trade export energy oil gas pipeline
rail airline airport flight nuclear missile treaty summit election campaign debate
poll primary caucus cabinet minister premier chancellor bankruptcy lawsuit merger
startup smartphone chip robot satellite rocket telescope fossil reef glacier
forest river drought harvest farmer fishing tourism hotel airline crime murder
robbery fraud arrest indictment probe investigation audit lawsuit hospital nurse
vaccine antibiotic cancer diabetes obesity mental therapy insurance pension wage
rent housing mortgage suburb highway bridge tunnel subway stadium olympic
championship tournament coach player striker goalkeeper referee album festival
concert theater film series episode comedy drama documentary novel author
animated brand fashion restaurant recipe menu chef wine coffee harvest festival
holiday parade festival carnival protest rally march boycott petition lawsuit
foundation charity donor scholarship university professor student tuition
laboratory researcher experiment discovery species bird whale coral turtle
forest canopy wildfire smoke ash rescue evacuation shelter aid donation
helicopter drone radar sensor battery grid solar wind hydrogen turbine
pipeline refinery mine copper lithium cobalt silicon software browser
encryption privacy hacker breach password dataset algorithm startup founder
investor acquisition lawsuit patent trademark copyright royalty estate
inheritance divorce custody adoption foster retirement veteran funeral
memorial monument museum exhibition sculpture painting auction bidder
collection antique manuscript archive library encyclopedia journal essay
editorial column headline broadcast anchor correspondent documentary""".split()

_LETTERS = "abcdefghijklmnopqrstuvwxyz"
_TRACK = [("tracka", 0.005), ("trackb", 0.02), ("trackc", 0.08), ("trackd", 0.30)]
# tracke: burst-style mentions, all concentrated in one stretch of the stream
_TRACK_BURST = ("tracke", 0.45, 0.05, 0.9)      # (word, start_frac, len_frac, prob)
_BURSTS = [("bursta", 0.20, 600), ("burstb", 0.35, 500), ("bursta", 0.55, 400),
           ("burstc", 0.70, 550), ("burstd", 0.85, 500)]
_FILLERS = ["says", "amid", "after", "new", "over", "under", "watch", "best"]


def synthetic_articles(n: int, seed: int) -> list[dict]:
    """Deterministic synthetic news stream (no local GDELT/HuffPost data).

    Headlines are Zipf-sampled over a fixed vocabulary + injected burst events;
    keywords are then extracted with the repository's own tokenizer
    (tokenize + phrases), i.e. the same extraction rule the app uses.
    Calendar days hold ~20 articles, with 30-day and 60-day quiet gaps at 40%/70%
    of the stream so the day decay unit sees long intervals.
    """
    rng = random.Random(seed)
    vocab = _REAL[:160] + [f"topic{_LETTERS[i // 26]}{_LETTERS[i % 26]}" for i in range(160)]
    weights = [1.0 / (i + 1) for i in range(len(vocab))]
    counts = []
    left = n
    while left > 0:
        k = min(left, max(1, int(rng.gauss(20, 5))))
        counts.append(k)
        left -= k
    day_ords: list[int] = []
    d = date(2018, 1, 1).toordinal()
    for j, k in enumerate(counts):
        day_ords.extend([d] * k)
        d += 1
        if j == int(len(counts) * 0.4):
            d += 29            # 30-day gap
        if j == int(len(counts) * 0.7):
            d += 59            # 60-day gap

    arts = []
    for i in range(n):
        ws = rng.choices(vocab, weights, k=rng.randint(3, 8))
        for word, start, length in _BURSTS:
            if start * n <= i < start * n + length and rng.random() < 0.8:
                ws.append(word)
        for word, p in _TRACK:
            if rng.random() < p:
                ws.append(word)
        if _TRACK_BURST[1] * n <= i < (_TRACK_BURST[1] + _TRACK_BURST[2]) * n \
                and rng.random() < _TRACK_BURST[3]:
            ws.append(_TRACK_BURST[0])
        if rng.random() < 0.15:
            ws.insert(rng.randrange(len(ws)), rng.choice(_FILLERS))   # filtered by tokenizer
        headline = " ".join(w.capitalize() for w in ws)
        toks = tuple(tokenize(headline))
        arts.append({
            "i": i,
            "date": date.fromordinal(day_ords[i]).isoformat(),
            "headline": headline,
            "tokens": toks,
            "keyset": tuple(toks) + tuple(phrases(headline)),   # engine v0.3 default
        })
    return arts


# ================================================================ V1: small inputs
def direct_sum(mention_T: list[int], T_now: int, c: float) -> float:
    """Hand computation: add up each article's weight (1-c)**(T_now - T_i)."""
    return sum((1.0 - c) ** (T_now - ti) for ti in mention_T)


def naive_pruned_ref(docs, steps, c, theta):
    """Textbook per-document pruning: decay, add 1, drop everything < theta."""
    ref: dict[str, float] = {}
    out = []
    T = 0
    for toks, k in zip(docs, steps):
        T += k
        for w in list(ref):
            ref[w] *= (1.0 - c) ** k
        for w in toks:
            ref[w] = ref.get(w, 0.0) + 1.0
        for w in [x for x, s in ref.items() if s < theta]:
            del ref[w]
        out.append(dict(ref))
    return out, T


def v1_decaying_small() -> None:
    print("\n== V1. 감쇠 윈도우: 작은 입력 직접 합산 대조 ==")
    c, theta = 0.1, 0.5
    # day-unit crafted stream: (steps to previous article, keywords)
    # docs 1-2 same day; 30-day gap before doc 4; daily fillers so pruning runs;
    # alpha reappears on day 72 long after being dropped.
    docs = [
        (0, {"alpha"}),            # day 0
        (0, {"alpha", "beta"}),    # day 0  (same day: no decay between)
        (1, {"beta"}),             # day 1
        (30, {"gamma", "alpha"}),  # day 31 (30-day gap)
        (1, {"delta"}),            # day 32
    ] + [(1, {f"filler{i}"}) for i in range(33, 71)] + [   # days 33..70 daily filler
        (2, {"alpha"}),            # day 72: dropped keyword reappears
        (1, {"beta", "gamma"}),    # day 73
    ]
    steps = [s for s, _ in docs]
    keysets = [t for _, t in docs]
    T_of = []
    T = 0
    for s in steps:
        T += s
        T_of.append(T)

    mentions: dict[str, list[tuple[int, int]]] = defaultdict(list)   # (doc index, T)
    for di, (T_i, toks) in enumerate(zip(T_of, keysets)):
        for w in toks:
            mentions[w].append((di, T_i))

    base = DecayingWindow(c, None)
    pruned = DecayingWindow(c, theta, track_error=True)
    naive, T_end = naive_pruned_ref(keysets, steps, c, theta)
    mismatch = []
    snap_score: dict[tuple[str, int], float] = {}
    EXPECTED = {                                   # hand values, keyed (keyword, doc ordinal)
        ("alpha", 2): 2.0,                       # two same-day articles: 1 + 1, no decay
        ("alpha", 3): 2 * 0.9,                   # one day later
        ("beta", 3): 1.9,                        # 0.9 (day 0) + 1
        ("alpha", 4): 2 * 0.9 ** 31 + 1,         # 30-day gap then mention
        ("gamma", 4): 1.0,
        ("delta", 5): 1.0,
    }
    for i, toks in enumerate(keysets):
        base.add(toks, steps[i])
        pruned.add(toks, steps[i])
        Ti = T_of[i]
        for w in set(mentions) | set(naive[i]):
            # hand sum: add up each *past* article's weight (1-c)**(T_now - T_i);
            # articles are identified by doc index, so same-day docs are counted separately
            exp = sum((1.0 - c) ** (Ti - ti) for di, ti in mentions.get(w, []) if di <= i)
            got = base.score(w)
            if abs(got - exp) > 1e-9 * max(1.0, exp):
                mismatch.append((i, w, "baseline", exp, got))
        if (i + 1) in {t for _, t in EXPECTED}:
            for (w, t) in EXPECTED:
                if t == i + 1:
                    snap_score[(w, t)] = base.score(w)
        if set(naive[i]) != {w for w, _ in pruned.items()}:
            mismatch.append((i, "-", "pruned-set", sorted(naive[i]), sorted(pruned.items())))
    check("C1.1", "기준(전체 유지) 점수 == 기사 가중치 직접 합산(일 단위, 같은 날/긴 간격 포함)",
          not mismatch, f"{len(docs)}건 전 시점 전 키워드 일치 (오차 < 1e-9)" if not mismatch
          else f"첫 불일치: {mismatch[0]}", repro=None if not mismatch else str(mismatch[:3]))

    # hand-written expected values, captured at their own stream position
    bad = [(w, t, e, snap_score[(w, t)]) for (w, t), e in EXPECTED.items()
           if abs(snap_score[(w, t)] - e) > 1e-9]
    check("C1.2", "손계산 기댓값(같은 시각 2건, 30일 간격 등) 대조", not bad,
          "" if not bad else f"불일치: {bad}")

    # dropped keyword reappearance: alpha < theta on day 39 (0.4633...), back on day 72
    drop_day = next(T_of[i] for i, ref in enumerate(naive) if "alpha" not in ref)
    re_day = T_of[-2]                                     # day 72
    i_re = len(docs) - 2
    pr_alpha = None
    b2, p2 = DecayingWindow(c, None), DecayingWindow(c, theta, track_error=True)
    for i, toks in enumerate(keysets):
        b2.add(toks, steps[i])
        p2.add(toks, steps[i])
        if i == i_re:                       # compare both scores at the same time point
            pr_alpha = p2.score("alpha")
            base_alpha = b2.score("alpha")
    err = base_alpha - pr_alpha
    ok = (drop_day == 39 and abs(pr_alpha - 1.0) < 1e-12
          and abs(err) <= p2.key_error("alpha") + 1e-9
          and err <= p2.error_bound() + 1e-9 and err >= -1e-9)
    check("C1.3", "제거된 키워드 재등장: 점수 1부터 재시작, 오차 ≤ key_error·상한", ok,
          f"drop일={drop_day}(39 예상), 재등장 점수={pr_alpha:.6f}(1.0 예상), "
          f"기준={base_alpha:.6f}, 오차={err:.6f} ≤ key_error={p2.key_error('alpha'):.3f}, 상한={p2.error_bound():.2f}")

    # the pruning rule itself, randomized (both units), vs the naive reference
    rng = random.Random(11)
    vocab = [f"v{i}" for i in range(120)]
    rdocs = [set(rng.choices(vocab, [1 / (i + 1) for i in range(120)], k=rng.randint(1, 6)))
             for _ in range(2000)]
    rsteps = [rng.choice([0, 0, 0, 1, 1, 2, 5]) for _ in rdocs]
    bad_set = bad_score = 0
    pr3 = DecayingWindow(0.05, 0.3)
    ref3, _ = naive_pruned_ref(rdocs, rsteps, 0.05, 0.3)
    for i, (toks, k) in enumerate(zip(rdocs, rsteps)):
        pr3.add(toks, k)
        if set(ref3[i]) != {w for w, _ in pr3.items()}:
            bad_set += 1
        for w, s in ref3[i].items():
            if abs(pr3.score(w) - s) > 1e-9:
                bad_score += 1
    check("C1.4", "제거 규칙(θ 미만 삭제)이 교과서 규칙과 임의 입력(일 단위 steps 혼합)에서 동일",
          bad_set == 0 and bad_score == 0,
          f"2000건 전 시점 집합·점수 일치" if not bad_set else f"집합 불일치 {bad_set}회, 점수 불일치 {bad_score}회")

    adocs = rdocs[:1500]
    dw4 = DecayingWindow(0.01)
    m4 = 0
    for i, toks in enumerate(adocs):
        dw4.add(toks)
    for w in {x for toks in adocs for x in toks}:
        if abs(dw4.score(w) - sum(0.99 ** (len(adocs) - 1 - i)
                                  for i, toks in enumerate(adocs) if w in toks)) > 1e-9:
            m4 += 1
    check("C1.5", "기사 단위(steps=1) 감쇠도 직접 합산과 일치", m4 == 0,
          f"1500건·전 키워드 일치" if not m4 else f"불일치 {m4}개 키워드")
    REPORT.append(f"\n**V1 설정**: c={c}, θ={theta}, 감쇠 단위=일(steps=날짜 차이, 같은 날 0) 및 기사(steps=1). "
                  f"직접 합산은 score_t(w)=Σ_(i≤t, w∈doc_i) (1-c)^(T_t-T_i)을 키워드별로 float으로 계산.\n")


# ================================================================ V2: replay, decaying
def run_pair(keysets, steps_list, c, theta, snap=SNAP, mem_every=MEM_EVERY):
    """One baseline (keep all) + one pruned window over the same input."""
    base = DecayingWindow(c, None)
    pr = DecayingWindow(c, theta, track_error=True)
    snaps = {"base_tops": [], "pr_tops": [], "t": [], "max_err": [], "key_err_viol": [],
             "base_size": [], "pr_size": []}
    mem = {"base": [], "pr": []}
    for i, toks in enumerate(keysets):
        st = 1 if steps_list is None else steps_list[i]
        base.add(toks, st)
        pr.add(toks, st)
        if (i + 1) % snap == 0:
            snaps["base_size"].append(len(base))
            snaps["pr_size"].append(len(pr))
            bt = dict(base.top_k(K))
            pt = dict(pr.top_k(K))
            mx = 0.0
            kev = []
            for w in set(bt) | set(pt):
                d_ = base.score(w) - pr.score(w)
                mx = max(mx, d_)
            for w in pt:
                d_ = base.score(w) - pr.score(w)
                if d_ > pr.key_error(w) + 1e-9:
                    kev.append((i, w, d_, pr.key_error(w)))
            snaps["base_tops"].append(bt)
            snaps["pr_tops"].append(pt)
            snaps["t"].append(i)
            snaps["max_err"].append(mx)
            snaps["key_err_viol"].extend(kev)
        if (i + 1) % mem_every == 0:
            mem["base"].append((i, deep_sizeof([base]), len(base)))
            mem["pr"].append((i, deep_sizeof([pr]), len(pr), pr.heap_size))
    # end-of-stream scan over *every* keyword the baseline keeps
    end_max, end_neg, end_bound_viol = 0.0, 0, 0
    for w, s in base.items():
        d_ = s - pr.score(w)
        end_max = max(end_max, d_)
        if d_ < -1e-9:
            end_neg += 1
        if d_ > pr.error_bound() + 1e-9:
            end_bound_viol += 1
    return {"snaps": snaps, "mem": mem, "peak_pr": pr.peak_size, "peak_base": base.peak_size,
            "dropped": pr.dropped_total, "bound": pr.error_bound(),
            "end": {"max_err": end_max, "neg": end_neg, "bound_viol": end_bound_viol,
                    "n_base": len(base), "n_pr": len(pr)}}


def topk_events(snap_words: list[list[str]], start: int, gap: int = NEW_GAP):
    """Same rule as the engine / experiments: (snapshot, keyword, kind)."""
    last: dict[str, int] = {}
    events = []
    for s, top in enumerate(snap_words):
        for w in top:
            prev = last.get(w)
            if s >= start and (prev is None or s - prev > gap):
                events.append((s, w, "first" if prev is None else "reentry"))
            last[w] = s
    return events


def match_tagged(exact_e, approx_e, tol_snaps):
    by_word: dict[str, list[int]] = defaultdict(list)
    for s, w, _ in approx_e:
        by_word[w].append(s)
    used: set[tuple[int, str]] = set()
    pairs, unmatched = [], []
    for s, w, kind in exact_e:
        cands = [x for x in by_word.get(w, []) if abs(x - s) <= tol_snaps and (x, w) not in used]
        if cands:
            x = min(cands, key=lambda v: (abs(v - s), v))
            used.add((x, w))
            pairs.append((s, w, x))
        else:
            unmatched.append((s, w, kind))
    spurious = [(s, w, k) for s, w, k in approx_e if (s, w) not in used]
    return pairs, unmatched, spurious


def classify_unmatched(unmatched, approx_e, base_final: set, pr_final: set):
    by_word: dict[str, list[int]] = defaultdict(list)
    for s, w, _ in approx_e:
        by_word[w].append(s)
    late, waiting, departed = [], [], []
    for s, w, _ in unmatched:
        later = [x for x in by_word.get(w, []) if x > s]
        if later:                                   # entered, but beyond tolerance
            late.append((s, w, later[0] - s))
        elif w in base_final:
            waiting.append((s, w))                  # could still match after the observation
        else:
            departed.append((s, w))                 # left the baseline Top K: match impossible
    return late, waiting, departed


def v2_compare(arts, steps_list, c, thetas, unit_label, warmup_steps, cid):
    """Baseline vs pruned over the same stream; returns rows keyed by theta."""
    keysets = [a["keyset"] for a in arts]
    print(f"\n== V2. 감쇠 윈도우 재생 비교 (단위: {unit_label}, c={c:.6g}) ==")
    # warm-up: skip the first 10/c decay steps (article index / day ordinal)
    if steps_list is None:
        start_i = int(warmup_steps / c)
    else:
        T = 0
        start_i = len(arts) - 1
        for i, k in enumerate(steps_list):
            T += k
            if T >= warmup_steps / c:
                start_i = i
                break
    start_snap = start_i // SNAP
    tol_steps = 1.0 / c
    tol_snaps = max(1, round(tol_steps / SNAP)) if steps_list is None else \
        max(1, math.ceil(tol_steps / (SNAP * arts_per_unit(arts, steps_list))))
    rows = {}
    for theta in thetas:
        r = run_pair(keysets, steps_list, c, theta)
        base_words = [list(t) for t in r["snaps"]["base_tops"]]
        pr_words = [list(t) for t in r["snaps"]["pr_tops"]]
        n_snaps_eval = len(base_words) - start_snap
        ov10, ov20, same = [], [], []
        for s in range(start_snap, len(base_words)):
            b, p = base_words[s], pr_words[s]
            ov10.append(len(set(b[:10]) & set(p[:10])) / 10)
            ov20.append(len(set(b) & set(p)) / K)
            same.append(b == p)
        eb = topk_events(base_words, start_snap)
        ep = topk_events(pr_words, start_snap)
        pairs, unmatched, spurious = match_tagged(eb, ep, tol_snaps)
        offs = [(x - s) * SNAP for s, w, x in pairs]
        absd = [abs(o) for o in offs]
        late, waiting, departed = classify_unmatched(
            unmatched, ep, set(base_words[-1]), set(pr_words[-1]))
        kw_b = statistics.mean(r["snaps"]["base_size"][start_snap:])
        kw_p = statistics.mean(r["snaps"]["pr_size"][start_snap:])
        mem_b = statistics.mean(x[1] for x in r["mem"]["base"] if x[0] >= start_i)
        mem_p = statistics.mean(x[1] for x in r["mem"]["pr"] if x[0] >= start_i)
        heap_p = statistics.mean(x[3] for x in r["mem"]["pr"] if x[0] >= start_i)
        row = {
            "theta": theta,
            "keywords_mean_approx": round(kw_p), "keywords_mean_exact": round(kw_b),
            "keyword_reduction_pct": round(100 * (1 - kw_p / kw_b), 1),
            "keywords_peak_approx": r["peak_pr"],
            "pybytes_mean_approx": round(mem_p), "pybytes_mean_exact": round(mem_b),
            "pybytes_reduction_pct": round(100 * (1 - mem_p / mem_b), 1),
            "heap_entries_mean": round(heap_p),
            "dropped_total": r["dropped"],
            "topk_overlap10_mean": round(statistics.mean(ov10), 4),
            "topk_overlap20_mean": round(statistics.mean(ov20), 4),
            "topk_overlap20_min": round(min(ov20), 3),
            "same_order_pct": round(100 * sum(same) / len(same), 1),
            "events_exact": len(eb), "events_approx": len(ep),
            "matched": len(pairs),
            "recall": round(len(pairs) / len(eb), 4) if eb else 1.0,
            "precision": round(len(pairs) / len(ep), 4) if ep else 1.0,
            "same_snapshot_pct": round(100 * sum(1 for o in offs if o == 0) / len(offs), 1) if offs else 0.0,
            "early_pct": round(100 * sum(1 for o in offs if o < 0) / len(offs), 1) if offs else 0.0,
            "late_pct": round(100 * sum(1 for o in offs if o > 0) / len(offs), 1) if offs else 0.0,
            "abs_offset_median": statistics.median(absd) if absd else 0,
            "abs_offset_p95": sorted(absd)[min(len(absd) - 1, int(round(0.95 * (len(absd) - 1))))] if absd else 0,
            "abs_offset_max": max(absd) if absd else 0,
            "unmatched_late_entries": len(late),
            "unmatched_late_delays_articles": sorted((d * SNAP for _, _, d in late)),
            "unmatched_waiting_at_end": len(waiting),
            "unmatched_departed_baseline": len(departed),
            "spurious_approx_events": len(spurious),
            "score_err_max_topk_union": round(max(r["snaps"]["max_err"][start_snap:]), 4),
            "score_err_bound": round(r["bound"], 4),
            "key_err_violations": len(r["snaps"]["key_err_viol"]),
            "end_neg": r["end"]["neg"], "end_bound_viol": r["end"]["bound_viol"],
            "end_max_err_all_keywords": round(r["end"]["max_err"], 4),
            "first_key_err_viol": r["snaps"]["key_err_viol"][:1],
            "start_article": start_i, "n_snapshots_eval": n_snaps_eval,
            "tol_snaps": tol_snaps,
        }
        rows[theta] = row
        print(f"  θ={theta}: 키워드 -{row['keyword_reduction_pct']}% 객체 -{row['pybytes_reduction_pct']}% "
              f"Top20 겹침 {row['topk_overlap20_mean']:.4f}(최저 {row['topk_overlap20_min']}) "
              f"이벤트 R {row['recall']:.3f} P {row['precision']:.3f} "
              f"대기 {row['unmatched_waiting_at_end']} 이탈 {row['unmatched_departed_baseline']}")
    # checks (identical for every theta row; fail on any)
    for theta, row in rows.items():
        ok = (row["end_neg"] == 0 and row["end_bound_viol"] == 0 and row["key_err_violations"] == 0)
        check(f"{cid}.a", f"점수 오차 상한(θ={theta}): 실제 오차가 상한 θ/(1-θ)와 key_error를 넘지 않음", ok,
              f"TopK 합집합 최대 오차 {row['score_err_max_topk_union']} ≤ 상한 {row['score_err_bound']}, "
              f"전체 키워드 최대 {row['end_max_err_all_keywords']}, 상한 초과 {row['end_bound_viol']}건, "
              f"key_error 초과 {row['key_err_violations']}건, 음수(기준 초과) {row['end_neg']}건",
              repro=None if ok else str(row["first_key_err_viol"]))
        check(f"{cid}.b", f"Top K 구성원 일치율 산출(θ={theta}) · 두 구현 유사성 지표", True,
              f"Top10 {pct(row['topk_overlap10_mean'], 2)}, Top20 평균 {pct(row['topk_overlap20_mean'], 2)}, "
              f"최저 {pct(row['topk_overlap20_min'], 1)}, 순서까지 동일 {row['same_order_pct']}%")
        check(f"{cid}.c", f"신규 진입 누락·탐지 지연(θ={theta}) · 측정 간격 {SNAP}건", True,
              f"기준 이벤트 {row['events_exact']}개 중 대응 {row['matched']}개(재현율 {pct(row['recall'])}), "
              f"누락 {row['events_exact'] - row['matched']}개 = 늦은 진입(허용 범위 밖) {row['unmatched_late_entries']} "
              f"+ 관측 종료까지 대기 {row['unmatched_waiting_at_end']} + 기준 Top K 이탈 {row['unmatched_departed_baseline']}. "
              f"대응된 이벤트 시점차(기사 수) 중앙값 {row['abs_offset_median']}, p95 {row['abs_offset_p95']}, "
              f"최대 {row['abs_offset_max']} (허용 범위 ±{round(tol_steps)}{'건' if steps_list is None else '일'})")
    return rows, start_i


def arts_per_unit(arts, steps_list):
    if steps_list is None:
        return 1.0
    total_days = sum(steps_list)
    return len(arts) / max(1, total_days)      # articles per day


# ================================================================ V3: DGIM small
def dgim_invariants(dg: DGIM) -> tuple[bool, str]:
    b = dg._buckets
    sizes = [s for _, s in b]
    if sizes != sorted(sizes):
        return False, f"sizes not ascending with age: {sizes}"
    for s in sizes:
        if s & (s - 1):
            return False, f"size {s} not a power of two"
        if sizes.count(s) > dg.r:
            return False, f"{sizes.count(s)} buckets of size {s} > r={dg.r}"
    for i in range(1, len(b)):
        if b[i - 1][0] <= b[i][0]:
            return False, "timestamps not strictly decreasing (newest first)"
    return True, ""


def v3_dgim_small() -> None:
    print("\n== V3. DGIM: 작은 입력 검증 ==")
    # C3.1 no mention at all
    dg = DGIM(10, 2)
    ok = dg.estimate(0) == 0.0 and dg.estimate(100) == 0.0 and dg.num_buckets == 0
    check("C3.1", "언급 없음: 추정치 0, 버킷 0", ok,
          f"estimate(0)={dg.estimate(0)}, estimate(100)={dg.estimate(100)}, buckets={dg.num_buckets}")

    # C3.2 window not yet full + merging; N=10, mentions at t=0,1,2, r=2
    dg = DGIM(10, 2)
    for t in (0, 1, 2):
        dg.add(t)
    # buckets (newest first): [2,1], [1,2]  <- 0&1 merged, newer timestamp kept
    ok_layout = dg._buckets == [[2, 1], [1, 2]]
    est = dg.estimate(2)                       # 2.5: truth 3, true range [2, 3]
    ok = ok_layout and est == 2.5 and 2.0 <= est <= 3.0
    check("C3.2", "N 미축적 상태 + 버킷 합치기: 합친 버킷은 최신 타임스탬프 유지, 추정치는 참 범위 안",
          ok, f"buckets={dg._buckets} (기대 [[2,1],[1,2]]), estimate(2)={est} (기대 2.5, 참 범위 [2,3])",
          repro=None if ok else "DGIM(10,2); add(0); add(1); add(2) -> buckets/estimate")

    # C3.3 old mentions falling out of the window
    e9 = dg.estimate(9)                        # window (-1, 9]: nothing expired, 2.5
    e11 = dg.estimate(11)                      # (1, 11]: [1,2] expires -> 1.0 exact
    e12 = dg.estimate(12)                      # (2, 12]: [2,1] expires -> 0
    ok = e9 == 2.5 and e11 == 1.0 and e12 == 0.0
    check("C3.3", "오래된 언급이 창 밖으로 나가면 만료되어 추정치 감소", ok,
          f"estimate(9)={e9}(2.5), estimate(11)={e11}(1.0, 정확), estimate(12)={e12}(0.0)")

    # C3.4 duplicate timestamp ignored (document frequency)
    dg2 = DGIM(10, 2)
    dg2.add(5)
    dg2.add(5)                                 # same t: ignored
    dg2.add(6)
    ok = dg2.estimate(6) == 2.0 and dg2.last_t == 6
    check("C3.4", "같은 시각 중복 add는 무시(제목 1회 이상 → 1)", ok,
          f"estimate(6)={dg2.estimate(6)} (기대 2.0)")

    # C3.5 midpoint vs textbook: exactly half the oldest bucket apart
    dgm, dgt = DGIM(64, 2, "midpoint"), DGIM(64, 2, "textbook")
    rng = random.Random(5)
    bits = [rng.random() < 0.4 for _ in range(300)]
    diff_ok = True
    for t, b_ in enumerate(bits):
        if b_:
            dgm.add(t)
            dgt.add(t)
        if abs((dgm.estimate(t) - dgt.estimate(t))) != 0.5 * (1 if dgm._buckets else 0):
            if dgm._buckets:
                diff_ok = False
                break
    check("C3.5", "midpoint와 textbook 추정치 차이는 버킷이 있으면 0.5, 비어 있으면 0", diff_ok,
          "300 step 무작위 스트림 전 시점 확인" if diff_ok else f"t={t}에서 불일치")

    # C3.6 article-derived bits: keyword in title >= 1 time -> 1; same start & same last N
    headlines = [
        "Court Rules For Plaintiff In Landmark Case",      # court @0
        "Senate Court Battle Enters Final Week",           # court @1
        "New Budget Plan Revealed By Mayor",               # no court
        "Court Nomination Advances Despite Fight",         # court @3
        "Storm Damage Closes Coastal Highway",             # no court
        "Court Session Delayed After Outage",              # court @5
    ]
    kw = "court"
    bits = [kw in tokenize(h) for h in headlines]          # [T,T,F,T,F,T]
    if bits != [True, True, False, True, False, True]:
        bits = [kw in tokenize(h) for h in headlines]      # keep going; reported below
    N = 4
    dg3, ex3 = DGIM(N, 2), ExactWindowCount(N)
    bad_est, bad_exact = [], []
    for t, b_ in enumerate(bits):
        if b_:
            dg3.add(t)
            ex3.add(t)
        truth = sum(bits[max(0, t - N + 1): t + 1])        # direct count, last N articles
        if ex3.count(t) != truth:
            bad_exact.append((t, truth, ex3.count(t)))
        est = dg3.estimate(t)
        if truth == 0:
            if est != 0.0:
                bad_est.append((t, truth, est))
        elif not (truth - 1.0 <= est <= truth + 1.0):      # r=2 midpoint: |err| <= 1
            bad_est.append((t, truth, est))
    ok = bits == [True, True, False, True, False, True] and not bad_est and not bad_exact
    check("C3.6", "기사 제목 유무 비트(같은 추적 시작점·최근 N개)에서 DGIM≈직접 집계, 정확 집계==직접 집계", ok,
          f"bits={list(map(int, bits))}, N={N}, DGIM 오류 {bad_est or '없음'}, 정확 집계 오류 {bad_exact or '없음'}",
          repro=None if ok else f"bad_est={bad_est[:3]}, bad_exact={bad_exact[:3]}")

    # C3.7 randomized stream: invariants + error bound + zero case
    worst = {}
    viol, inv_bad = [], []
    for r in (2, 3, 5):
        for est_name in ("midpoint", "textbook"):
            rng = random.Random(1000 + r)
            dg4 = DGIM(500, r, est_name)
            stream = [rng.random() < 0.3 for _ in range(4000)]
            for t, b_ in enumerate(stream):
                if b_:
                    dg4.add(t)
                good, why = dgim_invariants(dg4)
                if not good:
                    inv_bad.append((r, est_name, t, why))
                    break
                truth = sum(stream[max(0, t - 499): t + 1])
                est = dg4.estimate(t)
                if truth == 0:
                    if est != 0.0:
                        viol.append((r, est_name, t, truth, est, "est!=0"))
                else:
                    bound = dg4.error_bound() if est_name == "midpoint" else max(dg4.error_bound(), 0.5)
                    if abs(est - truth) / truth > bound + 1e-9:
                        viol.append((r, est_name, t, truth, est, bound))
                    key = (r, est_name)
                    worst[key] = max(worst.get(key, 0.0), abs(est - truth) / truth)
    ok = not viol and not inv_bad
    check("C3.7", "무작위 스트림 4000step: 불변식(크기 2의 거듭제곱·r개 이하·시간 감소) 및 상대 오차 상한 준수", ok,
          "; ".join(f"r={r}/{e} 최대 상대오차 {worst.get((r, e), 0):.3f}" for r in (2, 3, 5)
                    for e in ("midpoint", "textbook")) + ("" if ok else f" | 위반: {viol[:2]} {inv_bad[:1]}"),
          repro=None if ok else f"viol={viol[:3]} inv={inv_bad[:1]}")
    REPORT.append("\n**V3 설정**: DGIM(N, r) 추정 구간 (t-N, t], 비트 = 키워드가 제목에 1회 이상 등장(문서 빈도). "
                  "상대 오차 상한 1/(2(r-1)) (textbook은 소량 구간 0.5 보정, 기존 tests/test_algorithms.py 관례).\n")


# ================================================================ V4: DGIM replay
def v4_dgim_replay(arts) -> None:
    print("\n== V4. DGIM 재생 실험 (합성 스트림) ==")
    tracked = [w for w, _ in _TRACK] + [_TRACK_BURST[0]]
    bits = {w: [(w in a["tokens"]) for a in arts] for w in tracked}
    counts = {w: sum(b) for w, b in bits.items()}
    print("  추적 키워드 총 언급 수: " + ", ".join(f"{w}={counts[w]:,}" for w in tracked))
    REPORT.append(f"\n**V4 추적 키워드** (제목에 1회 이상 → 1): " +
                  ", ".join(f"`{w}` {counts[w]:,}회" for w in tracked) + "\n")
    rows = []
    viol_repro = []
    for N in DGIM_NS:
        for r in DGIM_RS + ([None] if N == 2000 else []):
            estimator = "midpoint" if r is not None else "textbook"
            rr = 2 if r is None else r
            dgs = {w: DGIM(N, rr, estimator) for w in tracked}
            exs = {w: ExactWindowCount(N) for w in tracked}
            abs_errs, rel_errs, buckets, dg_bits, ex_bits = [], [], [], [], []
            dg_py, ex_py = [], []
            partial_max = 0.0
            exact_bad = []
            n_eval = n_partial = 0
            for t, a in enumerate(arts):
                for w in tracked:
                    if bits[w][t]:
                        dgs[w].add(t)
                        exs[w].add(t)
                if (t + 1) % DGIM_EVAL or t < N // 2:
                    continue
                obs = min(N, t + 1)
                for w in tracked:
                    truth = sum(bits[w][max(0, t - N + 1): t + 1])
                    if exs[w].count(t) != truth:
                        exact_bad.append((t, w, truth, exs[w].count(t)))
                    est = dgs[w].estimate(t)
                    if obs < N:
                        n_partial += 1
                        partial_max = max(partial_max, abs(est - truth))
                        continue
                    n_eval += 1
                    abs_errs.append(abs(est - truth))
                    if truth > 0:
                        rel_errs.append(abs(est - truth) / truth)
                        bound = dgs[w].error_bound() if estimator == "midpoint" else max(dgs[w].error_bound(), 0.5)
                        if abs(est - truth) / truth > bound + 1e-9 and len(viol_repro) < 3:
                            viol_repro.append({"N": N, "r": rr, "estimator": estimator, "t": t,
                                               "keyword": w, "truth": truth, "estimate": est,
                                               "bound": bound})
                    elif est == 0.0:
                        rel_errs.append(0.0)
                    else:
                        viol_repro.append({"N": N, "r": rr, "t": t, "keyword": w,
                                           "truth": 0, "estimate": est})
                    buckets.append(dgs[w].num_buckets)
                    dg_bits.append(dgs[w].memory_bits())
                    ex_bits.append(exs[w].memory_bits())
                if (t + 1) % (DGIM_EVAL * 8) == 0:
                    dg_py.append(deep_sizeof([dgs]) / len(tracked))
                    ex_py.append(deep_sizeof([exs]) / len(tracked))
            rows.append({
                "N": N, "r": rr, "estimator": estimator, "tracked": len(tracked),
                "eval_points": n_eval, "partial_points": n_partial,
                "abs_err_mean": round(statistics.mean(abs_errs), 3),
                "abs_err_max": round(max(abs_errs), 3),
                "rel_err_mean": round(statistics.mean(rel_errs), 4),
                "rel_err_p95": round(sorted(rel_errs)[min(len(rel_errs) - 1, int(round(0.95 * (len(rel_errs) - 1))))], 4),
                "rel_err_max": round(max(rel_errs), 4),
                "rel_err_bound": max(dgs[tracked[0]].error_bound(), 0.5) if estimator == "textbook" else dgs[tracked[0]].error_bound(),
                "partial_abs_err_max": round(partial_max, 3),
                "buckets_mean": round(statistics.mean(buckets), 1),
                "buckets_max": max(buckets),
                "theo_bits_dgim_mean": round(statistics.mean(dg_bits)),
                "theo_bits_exact_mean": round(statistics.mean(ex_bits)),
                "pybytes_dgim_mean": round(statistics.mean(dg_py)),
                "pybytes_exact_mean": round(statistics.mean(ex_py)),
                "exact_opponent_mismatch": len(exact_bad),
            })
            row = rows[-1]
            print(f"  N={N:,} r={rr} {estimator}: 절대 오차 평균 {row['abs_err_mean']} 최대 {row['abs_err_max']}, "
                  f"상대 {pct(row['rel_err_mean'])} (상한 {pct(row['rel_err_bound'], 0)}), "
                  f"버킷 {row['buckets_mean']}, 논리비트 DGIM {row['theo_bits_dgim_mean']:,} vs 정확 {row['theo_bits_exact_mean']:,}")
    table(["N", "r", "추정", "평가 수", "절대 오차 평균", "절대 오차 최대", "상대오차 평균", "p95", "최대 / 상한",
           "부분창 절대 오차 최대", "버킷 평균/최대", "논리 bit DGIM", "논리 bit 정확", "Python B DGIM", "Python B 정확"],
          [[f"{r_['N']:,}", r_["r"], r_["estimator"], f"{r_['eval_points']:,}",
            r_["abs_err_mean"], r_["abs_err_max"], pct(r_["rel_err_mean"], 2), pct(r_["rel_err_p95"], 2),
            f"{pct(r_['rel_err_max'], 1)} / {pct(r_['rel_err_bound'], 0)}",
            r_["partial_abs_err_max"], f"{r_['buckets_mean']} / {r_['buckets_max']}",
            f"{r_['theo_bits_dgim_mean']:,}", f"{r_['theo_bits_exact_mean']:,}",
            f"{r_['pybytes_dgim_mean']:,}", f"{r_['pybytes_exact_mean']:,}"] for r_ in rows],
          "V4. DGIM 오차·버킷·메모리 (추적 키워드당 평균, 합성 스트림)")

    bound_viol = [v for v in viol_repro if v.get("truth", 1) > 0]
    zero_viol = [v for v in viol_repro if v.get("truth", 1) == 0]
    ex_mis = sum(r_["exact_opponent_mismatch"] for r_ in rows)
    check("C4.1", "재생 실험 전 구간에서 DGIM 오차가 상한 내, 실제 횟수 0일 때 추정 0", not bound_viol and not zero_viol,
          f"상한 초과 {len(bound_viol)}건, 0 구간 오측 {len(zero_viol)}건" +
          ("" if not (bound_viol or zero_viol) else f" | 예시: {(bound_viol + zero_viol)[:1]}"),
          repro=json.dumps((bound_viol + zero_viol)[:1], ensure_ascii=False) if (bound_viol or zero_viol) else None)
    check("C4.2", "효율적 정확 집계(ExactWindowCount) == 직접 집계(같은 창·같은 시작점)", ex_mis == 0,
          f"불일치 {ex_mis}건")
    check("C4.3", "이론 비트(memory_bits 모델)와 실제 Python 객체 크기(deep_sizeof) 구분 기록", True,
          "표 참조: 논리 비트는 버킷당 log2N+log2log2N(DGIM) / min(등장수·log2N, N)(정확) 가정, "
          "Python 열은 실제 리스트·deque 객체 크기")
    REPORT.append("- 논리 비트 = 압축 저장 가정 모델(`memory_bits()`); Python B = `deep_sizeof` 실제 객체 크기(키워드당). "
                  "두 값은 단위·의미가 다르며 별개로 해석해야 한다.\n")
    return rows


# ================================================================ V5: data sources
def v5_data_sources(arts, seed, n) -> dict:
    print("\n== V5. 데이터 출처 ==")
    sources = {}
    # GDELT: any local article-level data? (repo has no loader/reference at all)
    gdelt_hits = list(ROOT.glob("data/**/*gdelt*")) + list(ROOT.glob("**/*gdelt*.csv"))
    gdelt_ok = bool(gdelt_hits)
    sources["gdelt"] = {"present": gdelt_ok, "paths": [str(p) for p in gdelt_hits]}
    check("C5.1", "GDELT 기사 데이터 재생 검증", None, "로컬에 재생 가능한 GDELT 기사 데이터가 없음(집계 CSV도 없음). "
          "저장소에 GDELT 로더·참조가 없고 verify/verify_gdelt.py는 빈 플레이스홀더. 합성 입력으로 정확성만 검증함.",
          status="SKIP")
    # HuffPost
    hp_ok = HUFFPOST_RAW.exists()
    sources["huffpost"] = {"present": hp_ok, "path": HUFFPOST_RAW.relative_to(ROOT).as_posix()}
    check("C5.2", "HuffPost 데이터 별도 재생 실험", None,
          f"data/raw/News_Category_Dataset_v3.json 없음({HUFFPOST_RAW.name} 미발견). "
          "데이터가 있으면 본 스크립트의 V2/V4를 실제 기사 스트림에 적용해야 함.",
          status="SKIP")
    dates = [a["date"] for a in arts]
    sources["synthetic"] = {
        "articles": n, "first_date": dates[0], "last_date": dates[-1],
        "days": len(set(dates)), "seed": seed,
        "generator": "Zipf 어휘 + 주입된 burst 이벤트 + 30/60일 조용한 갭; "
                     "키워드 추출은 저장소의 tokenize()+phrases() 사용",
    }
    check("C5.3", "합성 입력 생성·기록", True,
          f"{n:,}건, {dates[0]} ~ {dates[-1]} ({len(set(dates)):,}일, 긴 간격 30일·60일 포함), seed={seed}")
    return sources


# ================================================================ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--articles", type=int, default=48_000)
    ap.add_argument("--seed", type=int, default=20261116)
    args = ap.parse_args()
    t0 = time.time()
    meta = {"python": platform.python_version(), "platform": platform.platform(),
            "cpus": os.cpu_count(), "run_at": time.strftime("%Y-%m-%d %H:%M"),
            "command": "python verify/verify_algorithms.py"
                       + (f" --articles {args.articles}" if args.articles != 48_000 else "")
                       + (f" --seed {args.seed}" if args.seed != 20261116 else "")}
    print("감쇠 윈도우·DGIM 검증 (TrendPulse @ project-verification)")
    print(f"실행: {meta['command']}  |  Python {meta['python']} / {meta['platform']}")

    arts = synthetic_articles(args.articles, args.seed)
    n = len(arts)
    dates = [a["date"] for a in arts]
    day_ords = [date.fromisoformat(d).toordinal() for d in dates]
    steps_day = [0] + [day_ords[i] - day_ords[i - 1] for i in range(1, n)]

    REPORT.append("# 감쇠 윈도우 · DGIM 검증 결과\n")
    REPORT.append("> 기존 알고리즘의 합성 입력 검증이다. GDELT 실데이터 재생, 최근 7일 만료 및 72시간 급상승은 후속 검증 범위다. Top K 이벤트는 50기사 간격에서 관측하며, 같은 키워드의 첫 진입과 재진입을 구분하지 않고 허용 오차 내에서 매칭한다. 시점차 0은 같은 스냅샷에서 관측됐음을 뜻한다.\n")
    REPORT.append(f"- 실행 명령: `{meta['command']}` (저장소 루트에서)")
    REPORT.append(f"- 실행 환경: Python {meta['python']} / {meta['platform']} / CPU {meta['cpus']}개 · {meta['run_at']}")
    REPORT.append(f"- 감쇠 단위(현재 코드가 지원하는 단위): **기사 단위**(steps=1)와 **일 단위**"
                  f"(steps=직전 기사와의 날짜 차, 같은 날 0) — EngineConfig.decay_unit ∈ {{article, day}}.")
    REPORT.append(f"- 데이터: 합성 뉴스 스트림 {n:,}건 ({dates[0]} ~ {dates[-1]}, {len(set(dates)):,}일, "
                  f"30일·60일 조용한 갭 포함, seed={args.seed}). 키워드 추출은 저장소 자체 규칙"
                  f"(tokenize + phrases, v0.3 기본). GDELT/HuffPost 로컬 데이터 없음 → 미실행 항목 참고.\n")

    v1_decaying_small()

    # V2 article unit
    rows_a, start_a = v2_compare(arts, None, C_ARTICLE, THETAS_ARTICLE,
                                 f"기사(c={C_ARTICLE}, 반감기 ≈ {math.log(0.5)/math.log(1-C_ARTICLE):.0f}건)",
                                 WARMUP_STEPS, "C2")
    # V2 day unit
    rows_d, start_d = v2_compare(arts, steps_day, C_DAY, [THETA_DAY],
                                 f"일(반감기 {HALF_LIFE_DAYS}일, c={C_DAY:.4f}/일)", WARMUP_STEPS, "C2D")

    def mem_rows(rows, unit):
        out = []
        for th, r in rows.items():
            out.append([unit, th, f"{r['keywords_mean_approx']:,} / {r['keywords_mean_exact']:,}",
                        f"{r['keyword_reduction_pct']:.1f}%", f"{r['heap_entries_mean']:,.0f}",
                        f"{r['pybytes_mean_approx']/1e6:.2f} MB / {r['pybytes_mean_exact']/1e6:.2f} MB",
                        f"{r['pybytes_reduction_pct']:.1f}%", r["keywords_peak_approx"], f"{r['dropped_total']:,}"])
        return out

    table(["감쇠 단위", "θ", "저장 키워드 수 (근사/기준)", "감소율", "힙 항목 평균",
           "Python 집계 크기 (근사/기준)", "객체 감소율", "근사 최대 키워드", "누적 삭제"],
          mem_rows(rows_a, "기사") + mem_rows(rows_d, "일"),
          "V2-1. 메모리 (평가 구간 평균값; Python 크기 = deep_sizeof(window), 오차 추적 상태 포함, 2,000건마다 표본)")
    table(["감쇠 단위", "θ", "Top10 겹침", "Top20 겹침 평균", "최저", "순서까지 동일",
           "기준 이벤트", "대응(재현율)", "같은 스냅샷", "이르게/늦게", "|시점차| 중앙/p95/최대(기사)",
           "허용범위 밖 늦은 진입", "관측 종료까지 대기", "기준 Top K 이탈", "근사 단독 진입"],
          [[u, r["theta"], pct(r["topk_overlap10_mean"], 2), pct(r["topk_overlap20_mean"], 2),
            pct(r["topk_overlap20_min"], 1), f"{r['same_order_pct']:.1f}%",
            r["events_exact"], f"{r['matched']} ({pct(r['recall'])})", f"{r['same_snapshot_pct']:.1f}%",
            f"{r['early_pct']:.1f}% / {r['late_pct']:.1f}%",
            f"{r['abs_offset_median']} / {r['abs_offset_p95']} / {r['abs_offset_max']}",
            r["unmatched_late_entries"], r["unmatched_waiting_at_end"],
            r["unmatched_departed_baseline"], r["spurious_approx_events"]]
           for u, rows in (("기사", rows_a), ("일", rows_d)) for r in rows.values()],
          "V2-2. Top K 일치율·진입 이벤트·탐지 지연 (측정 간격 50기사 스냅샷; 지연 해상도 = 50기사)")
    table(["감쇠 단위", "θ", "TopK 합집합 최대 오차", "전체 키워드 최대 오차", "상한 θ/(1-θ)",
           "상한 초과", "key_error 초과", "음수(근사>기준)"],
          [[u, r["theta"], r["score_err_max_topk_union"], r["end_max_err_all_keywords"],
            r["score_err_bound"], r["end_bound_viol"], r["key_err_violations"], r["end_neg"]]
           for u, rows in (("기사", rows_a), ("일", rows_d)) for r in rows.values()],
          "V2-3. 점수 오차와 구현 제공 상한의 비교")
    REPORT.append("- Top K 일치율은 두 구현(전체 유지 vs 제거)의 결과가 얼마나 비슷한지를 나타내는 "
                  "상대 비교 지표이지, 실제 화제를 맞힌 정확도가 아니다.\n")

    v3_dgim_small()
    v4_rows = v4_dgim_replay(arts)
    sources = v5_data_sources(arts, args.seed, n)

    # ---- verdicts
    n_pass = sum(1 for c in CHECKS if c["status"] == "PASS")
    n_fail = sum(1 for c in CHECKS if c["status"] == "FAIL")
    n_skip = sum(1 for c in CHECKS if c["status"] == "SKIP")
    verdict_ko = {"PASS": "통과", "FAIL": "실패", "SKIP": "미실행"}
    REPORT.append("\n## 항목별 정리 (통과 / 실패 / 미실행)\n")
    REPORT.append("| ID | 항목 | 결과 | 비고 |")
    REPORT.append("|---|---|---|---|")
    for c in CHECKS:
        note = c["detail"] or ""
        if c["repro"]:
            note += f" **재현**: `{c['repro']}`"
        REPORT.append(f"| {c['id']} | {c['desc']} | {verdict_ko[c['status']]} | {note} |")
    REPORT.append("")
    REPORT.append(f"**총 {len(CHECKS)}항목: 통과 {n_pass} / 실패 {n_fail} / 미실행 {n_skip}** "
                  f"(소요 {time.time() - t0:.1f}초)\n")

    OUT_JSON.write_text(json.dumps(
        {"meta": meta, "checks": CHECKS, "sources": sources,
         "V2_article": {str(k): v for k, v in rows_a.items()},
         "V2_day": {str(k): v for k, v in rows_d.items()},
         "V4_rows": v4_rows},
        ensure_ascii=False, indent=1), encoding="utf-8")
    OUT_MD.write_text("\n".join(REPORT), encoding="utf-8")

    print(f"\n총 {len(CHECKS)}항목: 통과 {n_pass} / 실패 {n_fail} / 미실행 {n_skip} "
          f"(소요 {time.time() - t0:.1f}초)")
    print(f"결과: {OUT_MD}")
    print(f"      {OUT_JSON}")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
