"""Ranking helpers shared by the engine and the experiments (v0.3).

absorb()       : phrases take the place of the words they contain
uncertainty()  : which adjacent ranks the pruning error could swap
burst_scores() : keywords mentioned more than their long-term rate predicts
"""
from __future__ import annotations

from .decaying import DecayingWindow

ABSORB_RATIO = 0.8


def absorb(cands: list[tuple[str, float]], k: int, ratio: float = ABSORB_RATIO) -> list[tuple[str, float]]:
    """Top ``k`` of ``cands`` (sorted by score, descending) after phrase absorption.

    A single word is hidden when a phrase containing it scores at least
    ``ratio`` x the word's own score: then most of the word's mentions come from
    that phrase, and showing both would repeat one topic ("donald" vs
    "donald trump"). A word used in many contexts keeps its place ("trump").
    Only candidates are compared, so pass enough of them (e.g. 3k).
    """
    score = dict(cands)
    hidden = set()
    for w, s in cands:
        if " " not in w:
            continue
        for part in w.split(" "):
            su = score.get(part)
            if su is not None and s >= ratio * su:
                hidden.add(part)
    out = [(w, s) for w, s in cands if w not in hidden]
    return out[:k]


def uncertainty(ranked: list[tuple[str, float]], errors: list[float],
                outside: list[tuple[float, float]], dropped_bound: float,
                outside_complete: bool = True) -> list[dict]:
    """Per-row flags for an approximate ranking whose scores are under-estimates.

    The exact score of row i lies in [s_i, s_i + e_i) where e_i is that keyword's
    error bound (DecayingWindow.key_error). Rows i and i+1 can only swap in the
    exact ranking if s_i < s_{i+1} + e_{i+1}; otherwise their order is
    *guaranteed*.
    The last row may fall out of the list if any keyword outside it could
    exceed it: ``outside`` holds (score, error) of the kept keywords below the
    list (at least all with score >= s_last - dropped_bound, since no error
    exceeds dropped_bound), ``dropped_bound`` is the most a pruned-away keyword
    can have. If the caller could not list all such keywords it passes
    ``outside_complete=False`` and the last row is flagged conservatively.
    The guarantee is about raw scores; with phrase absorption, which words are
    hidden can also differ (not covered).
    """
    n = len(ranked)
    below = []
    for i in range(n):
        s = ranked[i][1]
        if i + 1 < n:
            below.append(s < ranked[i + 1][1] + errors[i + 1])
        else:
            reach = max([o + e for o, e in outside] + [dropped_bound])
            below.append(not outside_complete or s < reach)
    return [{"swap_above": i > 0 and below[i - 1], "swap_below": below[i]} for i in range(n)]


def outside_candidates(window: DecayingWindow, ranked: list[tuple[str, float]], extra: int = 40):
    """(outside, complete) for uncertainty(): kept keywords below ``ranked`` that could
    catch up with its last row. Fetches ``extra`` more; complete if the fetched list
    already goes below s_last - error_bound or the window is exhausted."""
    if not ranked:
        return [], True
    shown = {w for w, _ in ranked}
    s_last, bound = ranked[-1][1], window.error_bound()
    cands = window.top_k(len(ranked) + extra)
    outside = [(s, window.key_error(w)) for w, s in cands if w not in shown and s >= s_last - bound]
    complete = len(cands) < len(ranked) + extra or cands[-1][1] < s_last - bound
    return outside, complete


def burst_scores(short: DecayingWindow, long: DecayingWindow, min_short: float, k: int) -> list[dict]:
    """Keywords whose recent rate exceeds their long-term rate.

    Rates are normalised by the decay constants: a keyword appearing at a
    steady rate p has short score ~ p / c_s and long score ~ p / c_l, so
    ``expected = long * c_l / c_s`` is what the short score would be without a
    change. ``excess = short - expected`` (in short-window mention units) ranks
    the list; ``lift = short / expected`` is shown. ``min_short`` filters words
    seen only once or twice. The long score is floored at 1 (a keyword the long
    window has pruned is treated as seen once).
    """
    cs, cl = short.c, long.c
    out = []
    for w, s in short.items():
        if s < min_short:
            continue
        expected = max(long.score(w), 1.0) * cl / cs
        excess = s - expected
        if excess <= 0:
            continue
        out.append((w, excess, s, s / expected))
    out.sort(key=lambda x: (-x[1], x[0]))
    return [{"keyword": w, "excess": round(e, 2), "short": round(s, 2), "lift": round(l, 1)}
            for w, e, s, l in out[:k]]
