"""Evaluate the agent on a fixed question set.

    set UPSTAGE_API_KEY=up_xxx           (PowerShell: $env:UPSTAGE_API_KEY="up_xxx")
    python experiments/eval_agent.py     # all questions (~36 Solar calls x up to 7 requests)
    python experiments/eval_agent.py --limit 5

Without a key every answer comes from the offline summary; the report then only
describes the baseline and says so. Results: experiments/results/agent_eval/

Questions
  * one per case-study event (26), asked 2 days after the event date, using the
    phrase expression when there is one ("kobe bryant가 왜 화제야?")
  * 4 overview questions at different dates
  * 3 hard cases: a word that never appears, a keyword asked before it exists
    (covid in 2016), and a question with no keyword at all

Measured per question: answer mode (solar / offline / offline-fallback),
evidence verification result, number of tool calls, number of corrective
retries, citations (all / valid), latency, whether the resolved keyword is the
intended one, and the answer text (for human review).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import date as Date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))

from run_sprint3 import EVENTS  # noqa: E402
from trendpulse import agent  # noqa: E402
from trendpulse.data import first_index_on_or_after, load_stream  # noqa: E402
from trendpulse.engine import EngineConfig, TrendEngine  # noqa: E402

OUT = ROOT / "experiments" / "results" / "agent_eval"


def questions():
    qs = []
    for d, exprs, label, group in EVENTS:
        ask_day = (Date.fromisoformat(d) + timedelta(days=2)).isoformat()
        kw = exprs[-1] if len(exprs) > 1 else exprs[0]
        qs.append({"id": f"event-{exprs[0]}", "kind": "event", "date": ask_day, "question": f"{kw}가 왜 화제야?",
                   "expected": exprs, "label": label})
    for d in ("2013-04-17", "2016-11-10", "2020-03-15", "2022-03-01"):
        qs.append({"id": f"overview-{d}", "kind": "overview", "date": d, "question": "요즘 뭐가 화제야?",
                   "expected": [], "label": "개요 질문"})
    qs.append({"id": "hard-unknown", "kind": "hard", "date": "2016-02-16", "question": "xyzzyq가 왜 화제야?",
               "expected": [], "label": "데이터에 없는 단어"})
    # correct behaviour: do not resolve a keyword that is not in the stream yet
    qs.append({"id": "hard-future", "kind": "hard", "date": "2016-02-16", "question": "covid가 왜 화제야?",
               "expected": [], "label": "아직 등장하지 않은 키워드"})
    qs.append({"id": "hard-nokeyword", "kind": "hard", "date": "2016-02-16", "question": "그래서 이유가 뭐야?",
               "expected": [], "label": "키워드 없는 질문"})
    return qs


def run(limit=None):
    arts = load_stream()
    engine = TrendEngine(arts, EngineConfig())
    solar = bool(os.environ.get("UPSTAGE_API_KEY", "").strip())
    rows = []
    for q in questions()[:limit]:
        start = (Date.fromisoformat(q["date"]) - timedelta(days=30)).isoformat()
        engine.reset(EngineConfig(), start_date=start)
        target = first_index_on_or_after(arts, (Date.fromisoformat(q["date"]) + timedelta(days=1)).isoformat())
        engine.step(max(0, target - engine.pos))
        t0 = time.perf_counter()
        try:
            res = agent.ask(engine, q["question"])
            err = None
        except Exception as exc:          # keep evaluating the other questions
            res, err = {"answer": "", "mode": "error", "trace": [], "citations": [], "verified": False}, repr(exc)
        latency = time.perf_counter() - t0
        trace = res.get("trace", [])
        cites = res.get("citations", [])
        rows.append({
            **q, "as_of": res.get("as_of"), "mode": res["mode"], "verified": res.get("verified"),
            "warning": res.get("warning"), "error": err, "latency_s": round(latency, 2),
            "tool_calls": sum(1 for t in trace if t["tool"] != "evidence-check"),
            "repairs": sum(1 for t in trace if t["tool"] == "evidence-check"),
            "citations": len(cites), "valid_citations": sum(1 for c in cites if c.get("valid")),
            "resolved_keyword": res.get("keyword"), "keyword_note": res.get("keyword_note"),
            "keyword_ok": (res.get("keyword") in q["expected"]) if q["expected"] else res.get("keyword") is None,
            "answer": res.get("answer", ""),
        })
        r = rows[-1]
        print(f"{r['id']:28s} {r['mode']:18s} verified={r['verified']} cites={r['valid_citations']}/{r['citations']} "
              f"repairs={r['repairs']} {r['latency_s']}s")
    return {"solar_enabled": solar, "model": os.environ.get("UPSTAGE_MODEL", agent.DEFAULT_MODEL),
            "run_at": time.strftime("%Y-%m-%d %H:%M"), "rows": rows}


def report(res):
    rows = res["rows"]
    n = len(rows)
    by = lambda m: sum(1 for r in rows if r["mode"] == m)  # noqa: E731
    head = ("Solar 실제 호출 결과" if res["solar_enabled"]
            else "**Solar 미실행 — UPSTAGE_API_KEY가 없어 오프라인 요약 기준선만 측정했다.** 키를 설정하고 다시 실행해야 Solar 평가가 된다.")
    lat = [r["latency_s"] for r in rows]
    L = ["# 에이전트 평가\n", f"- {head}",
         f"- 모델: {res['model']} · 실행 {res['run_at']} · 질문 {n}개",
         f"- 답변 모드: solar {by('solar (' + res['model'] + ')')} · offline {by('offline')} · "
         f"offline-fallback(검증 실패·호출 실패로 대체) {by('offline-fallback')} · error {by('error')}",
         f"- 근거 검증 통과 {sum(1 for r in rows if r['verified'])}/{n} · 재작성 요청이 있었던 질문 "
         f"{sum(1 for r in rows if r['repairs'])}개 · 인용 {sum(r['citations'] for r in rows)}개 중 유효 "
         f"{sum(r['valid_citations'] for r in rows)}개",
         f"- 의도한 키워드로 해석한 질문 {sum(1 for r in rows if r['keyword_ok'])}/{n} "
         f"(나머지 중 '질문의 표현이 아직 기사에 없음'을 답변에 안내한 질문 "
         f"{sum(1 for r in rows if not r['keyword_ok'] and r.get('keyword_note'))}개)",
         f"- 응답 시간 중앙값 {statistics.median(lat):.2f}초 (최대 {max(lat):.2f}초)\n",
         "| 질문 | 시점 | 모드 | 검증 | 도구 호출 | 재작성 | 인용(유효/전체) | 해석 키워드 | 시간 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['label']}: {r['question']} | {r['date']} | {r['mode']} | {'✓' if r['verified'] else '✗'} | "
                 f"{r['tool_calls']} | {r['repairs']} | {r['valid_citations']}/{r['citations']} | "
                 f"{r['resolved_keyword'] or '-'}{'' if r['keyword_ok'] else (' (안내함)' if r.get('keyword_note') else ' ⚠')} | {r['latency_s']}s |")
    L += ["\n## 사람 검토용 답변 (근거 적절성: 1~3점을 팀원이 기입)\n"]
    for r in rows:
        L += [f"### {r['label']} — {r['question']} ({r['date']})", f"- 모드: {r['mode']}"
              + (f" · 경고: {r['warning']}" if r.get("warning") else ""), "- 팀 평가(근거 적절성 1~3): ", "",
              "```", r["answer"].strip() or "(빈 답변)", "```", ""]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    res = run(args.limit)
    (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "report.md").write_text(report(res), encoding="utf-8")
    print(f"done -> {OUT}")


if __name__ == "__main__":
    main()
