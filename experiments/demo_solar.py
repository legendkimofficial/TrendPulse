"""End-to-end submission demo: one flow over the fixed input and reference time.

    python experiments/demo_solar.py [--t 2026-11-15T12:00Z] [--keyword trump]

Steps, all at the SAME reference time:
  1. replay the fixed GAL input up to the reference batch,
  2. show the 7-day ranking Top-10,
  3. show the 72-hour surge table (or the deferral reason),
  4. show supporting articles for the demo keyword (never beyond as_of),
  5. ask the Solar agent (real call when UPSTAGE_API_KEY is set, offline template
     otherwise) and verify the answer against the tool results: every number and
     cited article id must come from the engine at that reference time.

Writes experiments/results/gdelt/solar_demo.{json,md}.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trendpulse.gal import load_articles, parse_iso          # noqa: E402
from trendpulse.gal_agent import ask_gal                     # noqa: E402
from trendpulse.gal_engine import GalConfig, GalEngine       # noqa: E402

OUT = ROOT / "experiments" / "results" / "gdelt"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--t", default="2026-11-15T12:00:00Z", help="reference observation time")
    ap.add_argument("--keyword", default=None, help="demo keyword (default: top surge row)")
    args = ap.parse_args()
    t_ref = parse_iso(args.t)

    arts = load_articles()
    manifest = json.loads((ROOT / "data" / "processed" / "gdelt" / "manifest.json")
                          .read_text(encoding="utf-8"))
    gaps = [{"start": g["start"], "end": g["end"],
             "start_ts": parse_iso(g["start"].replace("+00:00", "")),
             "end_ts": parse_iso(g["end"].replace("+00:00", ""))}
            for g in manifest.get("collection_gaps", [])]
    eng = GalEngine(arts, GalConfig(gaps=gaps))
    while eng.pos < len(arts) and arts[eng.pos].obs <= t_ref:
        eng.step(24)
    if not eng.snapshots or eng.snapshots[-1]["t"] != t_ref:
        # advance the clock to exactly the requested reference time
        try:
            eng.advance_clock(args.t)
        except ValueError as exc:
            print(f"reference time not reachable: {exc}")
            sys.exit(2)
    state = eng.state()
    pos = eng.pos
    print(f"reference time {state['clock']['t_iso']} · articles replayed {pos:,}/{len(arts):,}")

    ranking = state["ranking"][:10]
    print("\n[7일 순위 Top10]")
    for r in ranking:
        print(f"  {r['rank']:2d}. {r['keyword']:18s} score={r['score']:9.2f} "
              f"outlets={r['outlets']:3d} countries={r['countries']}")

    print(f"\n[72시간 급상승] status={state['burst']['status']} "
          f"reason={state['burst'].get('reason')}")
    for b in state["burst"]["rows"][:8]:
        print(f"  {b['kw']:18s} {b['diff_pp']:+6.2f}pp  recent {b['num_recent']}/{b['den_recent']}"
              f"  prev {b['num_prev']}/{b['den_prev']}  outlets={b['outlets']}")

    keyword = args.keyword or (state["burst"]["rows"][0]["kw"] if state["burst"]["rows"]
                               else ranking[0]["keyword"])
    detail = eng.keyword_detail(keyword)
    related = eng.related(keyword, 5)
    print(f"\n[관련 기사] {keyword} · as_of 이후 기사 없음 확인")
    for a in related:
        print(f"  [#{a['id']}] {a['obs'][:16]} {a['title'][:70]} ({a['outlet']}, {a['country']})")
    assert all(a["id"] < pos for a in related)

    solar_mode = "online" if os.environ.get("UPSTAGE_API_KEY") else "offline"
    print(f"\n[Solar] mode={solar_mode} model={os.environ.get('UPSTAGE_MODEL', 'solar-pro2')}")
    res = ask_gal(eng, f"{keyword} 가 왜 화제인지 설명해줘", keyword=keyword)

    # ---- verification of the answer against engine/tool state
    checks = []
    trace_tools = [t["tool"] for t in res.get("trace", [])]
    checks.append({"check": "도구 호출됨", "ok": len(trace_tools) > 0, "evidence": trace_tools})
    checks.append({"check": "동일 키워드 사용", "ok": res.get("keyword") == keyword,
                   "evidence": res.get("keyword")})
    as_of_ok = res.get("as_of", {}).get("pos") == pos
    checks.append({"check": "동일 기준 시각(as_of)", "ok": as_of_ok,
                   "evidence": res.get("as_of")})
    cited = [c for c in res.get("citations", [])]
    valid_cites = [c for c in cited if c.get("valid")]
    checks.append({"check": "인용 기사가 도구 결과에 존재",
                   "ok": all(c.get("valid") for c in cited) and len(valid_cites) > 0,
                   "evidence": [(c["id"], c.get("valid")) for c in cited]})
    # numbers: the offline template and Solar must both quote the engine's score
    if solar_mode == "offline":
        score_str = f"{detail['score']:.2f}"
        checks.append({"check": "답변 수치 == 엔진 점수", "ok": score_str in res["answer"],
                       "evidence": f"engine={score_str}"})
        checks.append({"check": "오프라인 응답 표시", "ok": res["mode"] == "offline",
                       "evidence": res["mode"]})
    else:
        checks.append({"check": "실제 Solar 응답", "ok": res["mode"].startswith("solar"),
                       "evidence": res["mode"]})
        checks.append({"check": "근거 검증 통과", "ok": res.get("verified") is True,
                       "evidence": res.get("verified")})
    n_ok = sum(1 for c in checks if c["ok"])
    print(f"\n[답변 검증] {n_ok}/{len(checks)} 통과")
    for c in checks:
        print(f"  {'OK ' if c['ok'] else 'NG '} {c['check']}: {c['evidence']}")

    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_at": time.strftime("%Y-%m-%d %H:%M"),
        "input_sha256": manifest.get("input_sha256"),
        "reference_time": state["clock"]["t_iso"],
        "articles_replayed": pos,
        "keyword": keyword,
        "ranking_top10": ranking,
        "burst": state["burst"],
        "related": related,
        "solar_mode_requested": solar_mode,
        "solar_response": res,
        "verification": checks,
        "verification_passed": n_ok == len(checks),
        "real_solar_call": solar_mode == "online" and res.get("mode", "").startswith("solar"),
    }
    (OUT / "solar_demo.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    lines = ["# 종단 시연 증거 (7일 순위 → 72h 급상승 → 관련 기사 → Solar)\n",
             f"- 입력 SHA-256 `{payload['input_sha256']}` · 기준 시각 {payload['reference_time']}"
             f" · 재생 {pos:,}건 · 키워드 `{keyword}`",
             f"- Solar: {'**실호출** (' + res['mode'] + ')' if payload['real_solar_call'] else '오프라인 모드(UPSTAGE_API_KEY 없음)'}",
             "",
             "## Solar 답변", "```", res["answer"], "```", "",
             "## 도구 호출 추적"]
    for t in res.get("trace", []):
        lines.append(f"- `{t['tool']}({json.dumps(t['args'], ensure_ascii=False)})` → {t['summary']}")
    lines += ["", "## 검증"]
    for c in checks:
        lines.append(f"- {'통과' if c['ok'] else '실패'} — {c['check']}: {c['evidence']}")
    (OUT / "solar_demo.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n-> {OUT / 'solar_demo.md'}")
    if n_ok != len(checks):
        sys.exit(3)


if __name__ == "__main__":
    main()
