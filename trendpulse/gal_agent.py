"""Upstage Solar agent for the GDELT GAL dashboard.

Reuses the Solar chat plumbing of ``trendpulse.agent`` (_post, evidence
checks, citation parsing) but binds the tools to ``GalEngine`` so the model
must call statistics and related-article lookups over the *same* keyword and
reference time the dashboard shows (``as_of`` is the engine's current
observation time; article lookups never expose articles beyond it).

Env: UPSTAGE_API_KEY (required for online mode; otherwise the offline
template runs and the response is marked ``offline``), UPSTAGE_MODEL
(default solar-pro2), UPSTAGE_BASE_URL.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
from contextlib import nullcontext
from typing import Any

from .agent import DEFAULT_MODEL, _post, check_evidence, citations
from .gal_engine import GalEngine

GAL_SYSTEM_PROMPT = """너는 'Trend Pulse' GDELT 글로벌 뉴스 화제 키워드 분석 도우미다.
데이터: GDELT Article List(GAL) 영어 뉴스 제목을 관측 시각 순으로 재생하는 스트림.
순위 점수 = 최근 7일 기사의 시간 감쇠 가중 합(반감기 48시간). 급상승 = 최근 72시간 언급 비중에서
직전 72시간 비중을 뺀 값(퍼센트포인트). 발행국은 매체 도메인 기준이며 보도 대상 국가와 다르다.

규칙:
1. 반드시 도구를 호출해 통계와 관련 기사를 확인한 뒤 답한다. 도구 결과에 없는 사실은 지어내지 않는다.
2. 특정 키워드에 대한 질문이면 search_related_news로 기사를 조회하고, 근거로 쓴 기사를 [#기사id] 형식으로 인용한다.
   조회 결과에 없는 id는 절대 인용하지 않는다.
3. 답변 구성: (1) 현재 순위·점수(및 급상승 수치 또는 보류 사유) 한 줄 (2) 어떤 내용으로 언급되는지 2~4개 요점 (3) 한계 한 줄.
4. 한계에는 '이 순위는 GDELT가 관측한 영어 매체 제목 기준의 화제이며 전 세계 여론이 아니다',
   발행국은 매체 등록 기준, DGIM 값은 근사값이라는 점을 필요할 때 밝힌다.
5. 한국어로 간결하게 답한다. 키워드와 기사 제목은 영어 원문을 유지해도 된다."""

GAL_TOOLS = [
    {"type": "function", "function": {
        "name": "get_top_topics",
        "description": "현재 관측 시각의 7일 화제 키워드 순위(감쇠 점수)와 72시간 급상승 표를 반환한다.",
        "parameters": {"type": "object", "properties": {
            "k": {"type": "integer", "description": "가져올 순위 수 (1~20)", "default": 10}}}}},
    {"type": "function", "function": {
        "name": "get_topic_stats",
        "description": "키워드의 현재 순위, 7일 감쇠 점수, 72시간 급상승(최근/직전 구간 분자·분모, 보류 사유 포함), "
                       "보도 매체 수, 발행국 목록, DGIM 최근 N개 기사 등장 수(근사/정확)를 반환한다.",
        "parameters": {"type": "object", "required": ["keyword"],
                       "properties": {"keyword": {"type": "string", "description": "영어 소문자 키워드"}}}}},
    {"type": "function", "function": {
        "name": "search_related_news",
        "description": "현재 관측 시각까지 스트림에 들어온 기사 중 키워드를 제목에 포함한 최신 기사를 반환한다 "
                       "(id, 관측시각, 제목, 매체, 도메인, 발행국, URL).",
        "parameters": {"type": "object", "required": ["keyword"],
                       "properties": {"keyword": {"type": "string"},
                                      "limit": {"type": "integer", "default": 8, "description": "1~15"}}}}},
]


class GalTools:
    """Tool implementations over a GalEngine (read-only, as_of-bounded)."""

    def __init__(self, engine: GalEngine, lock=None, as_of: int | None = None):
        self.e = engine
        self.lock = lock or nullcontext()
        self.as_of = engine.pos - 1 if as_of is None else as_of
        self.evidence: dict[int, dict] = {}
        self.searched: set[str] = set()

    def get_top_topics(self, k: int = 10) -> dict:
        k = max(1, min(int(k), 20))
        with self.lock:
            st = self.e.state()
        return {"as_of": st["clock"]["t_iso"], "article_pos": st["clock"]["pos"],
                "top": [{"rank": r["rank"], "keyword": r["keyword"], "score": r["score"],
                         "entry": r["entry"], "outlets": r["outlets"],
                         "countries": r["countries"]} for r in st["ranking"][:k]],
                "surges": st["burst"]["rows"][:10],
                "surge_status": st["burst"]["status"], "surge_reason": st["burst"]["reason"]}

    def get_topic_stats(self, keyword: str) -> dict:
        with self.lock:
            d = self.e.keyword_detail(keyword.lower())
        b = d["burst"] or {}
        return {"keyword": d["keyword"], "as_of": d["as_of"], "current_rank": d["rank"],
                "score_7d": round(d["score"], 2),
                "mentions_7d": d["mentions_7d"], "outlets_7d": d["outlets_7d"],
                "publisher_countries_7d": d["countries_7d"],
                "surge": {"status": b.get("status"), "reason": b.get("reason"),
                          "diff_pp": b.get("diff_pp"),
                          "recent": f"{b.get('num_recent')}/{b.get('den_recent')}",
                          "previous": f"{b.get('num_prev')}/{b.get('den_prev')}"},
                "dgim_last_N_articles": d["dgim"]}

    def search_related_news(self, keyword: str, limit: int = 8) -> dict:
        limit = max(1, min(int(limit), 15))
        with self.lock:
            items = self.e.related(keyword.lower(), limit, as_of=self.as_of)
        self.searched.add(keyword.lower())
        for it in items:
            self.evidence[it["id"]] = {"id": it["id"], "obs": it["obs"], "title": it["title"],
                                       "outlet": it["outlet"], "domain": it["domain"],
                                       "country": it["country"], "url": it["url"]}
        return {"keyword": keyword.lower(), "count": len(items), "articles": items}

    def call(self, name: str, args: dict) -> dict:
        fn = {"get_top_topics": self.get_top_topics,
              "get_topic_stats": self.get_topic_stats,
              "search_related_news": self.search_related_news}.get(name)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**args) if isinstance(args, dict) else fn()
        except (TypeError, ValueError) as exc:
            return {"error": f"bad arguments: {exc}"}


def _summarize(result: dict) -> str:
    if "error" in result:
        return "오류: " + result["error"]
    if "articles" in result:
        return f"기사 {result['count']}건"
    if "top" in result:
        return f"상위 {len(result['top'])}개"
    if "current_rank" in result:
        return f"순위 {result['current_rank']}, 점수 {result['score_7d']}"
    return "ok"


def ask_gal(engine: GalEngine, question: str, keyword: str | None = None,
            lock=None) -> dict[str, Any]:
    """Answer a question about the GAL replay. Same contract as agent.ask."""
    lk = lock or nullcontext()
    with lk:
        if not keyword:
            # pick the question keyword: highest-scoring token that the engine knows
            from .text import tokenize
            cands = [w for w in tokenize(question) if w in engine.postings]
            keyword = max(cands, key=lambda w: engine.kw[w].S if w in engine.kw else 0,
                          default=None)
        keyword = (keyword or "").strip().lower() or None
        as_of_info = {"pos": engine.pos,
                      "t_iso": engine.snapshots[-1]["t_iso"] if engine.snapshots else None}
    key = os.environ.get("UPSTAGE_API_KEY", "").strip()
    if not key:
        return _offline(engine, keyword, lock, as_of_info)
    try:
        return _solar(engine, question, keyword, key, lock, as_of_info)
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, IndexError) as exc:
        res = _offline(engine, keyword, lock, as_of_info)
        res["mode"] = "offline-fallback"
        res["warning"] = f"Solar API 호출 실패로 오프라인 요약을 표시합니다: {exc}"
        return res


def _solar(engine, question, keyword, key, lock, as_of_info) -> dict:
    base = os.environ.get("UPSTAGE_BASE_URL", "https://api.upstage.ai/v1").rstrip("/")
    model = os.environ.get("UPSTAGE_MODEL", DEFAULT_MODEL)
    tools = GalTools(engine, lock, as_of_info["pos"] - 1)
    hint = f"\n(질문 대상 키워드: '{keyword}')" if keyword else ""
    messages = [{"role": "system", "content": GAL_SYSTEM_PROMPT},
                {"role": "user", "content": question + hint}]
    trace, n_calls, repairs, answer = [], 0, 0, ""
    for _ in range(7):
        payload = {"model": model, "messages": messages, "tools": GAL_TOOLS,
                   "temperature": 0.2, "tool_choice": "required" if n_calls == 0 else "auto"}
        msg = _post(f"{base}/chat/completions", key, payload)["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if calls:
            messages.append({"role": "assistant", "content": msg.get("content") or "",
                             "tool_calls": calls})
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = tools.call(name, args if isinstance(args, dict) else {})
                n_calls += 1
                trace.append({"tool": name, "args": args, "summary": _summarize(result)})
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
            continue
        answer = msg.get("content") or ""
        problems = check_evidence(answer, _tools_view(tools), keyword, n_calls)
        if not problems:
            return {"answer": answer, "mode": f"solar ({model})", "keyword": keyword,
                    "as_of": as_of_info, "trace": trace,
                    "citations": citations(answer, _tools_view(tools)), "verified": True}
        if repairs >= 1:
            break
        repairs += 1
        trace.append({"tool": "evidence-check", "args": {}, "summary": "; ".join(problems)})
        messages.append({"role": "assistant", "content": answer})
        messages.append({"role": "user", "content":
                         "답변이 근거 검증을 통과하지 못했다: " + "; ".join(problems) +
                         ". 필요하면 search_related_news를 호출하고, 도구 결과에 있는 기사 id만 [#id]로 인용해 다시 작성해줘."})
    res = _offline(engine, keyword, lock, as_of_info)
    res["mode"] = "offline-fallback"
    res["trace"] = trace + res["trace"]
    res["warning"] = "Solar 답변이 근거 검증을 통과하지 못해 오프라인 요약으로 대체했습니다."
    return res


class _tools_view:
    """Adapter so agent.check_evidence/citations can read GalTools state."""

    def __init__(self, tools: GalTools):
        self.evidence = tools.evidence
        self.searched = tools.searched


def _offline(engine, keyword, lock, as_of_info) -> dict:
    tools = GalTools(engine, lock, as_of_info["pos"] - 1)
    trace = []
    base = {"mode": "offline", "as_of": as_of_info, "verified": True}
    if not keyword:
        top = tools.get_top_topics(10)
        trace.append({"tool": "get_top_topics", "args": {"k": 10}, "summary": _summarize(top)})
        names = ", ".join(f"{r['rank']}. {r['keyword']}" for r in top["top"])
        surges = ", ".join(f"{s['kw']}({s['diff_pp']:+.1f}pp)" for s in top["surges"][:5]) or "보류/없음"
        return {**base, "keyword": None, "trace": trace, "citations": [], "answer": (
            f"**{top['as_of']} 관측 시각 기준 7일 화제 키워드 Top 10**\n{names}\n\n"
            f"72시간 급상승(상위): {surges}\n\n특정 키워드를 물어보면 관련 기사를 근거로 설명합니다 "
            f"(예: \"trump 왜 화제야?\").")}
    stats = tools.get_topic_stats(keyword)
    trace.append({"tool": "get_topic_stats", "args": {"keyword": keyword},
                  "summary": _summarize(stats)})
    news = tools.search_related_news(keyword, limit=6)
    trace.append({"tool": "search_related_news", "args": {"keyword": keyword, "limit": 6},
                  "summary": _summarize(news)})
    if not news["articles"]:
        return {**base, "keyword": keyword, "trace": trace, "citations": [], "answer":
                f"'{keyword}'를 제목에 포함한 기사가 아직 스트림에 없습니다 "
                f"({as_of_info['t_iso']} 기준)."}
    b = stats["surge"]
    surge_txt = (f"{b['diff_pp']:+.2f}pp (최근 {b['recent']}, 직전 {b['previous']})"
                 if b.get("status") == "ok" else f"보류({b.get('reason')})")
    countries = ", ".join(stats["publisher_countries_7d"][:8]) or "-"
    lines = [f"**{keyword}** — {stats['as_of']} 관측 기준 순위 {stats['current_rank']}, "
             f"7일 점수 {stats['score_7d']}, 72시간 급상승 {surge_txt}.",
             f"- 7일 내 제목 언급 {stats['mentions_7d']}건, 보도 매체 {stats['outlets_7d']}곳 "
             f"(발행국: {countries}).",
             "- 최근 관련 기사:"]
    for a in news["articles"][:4]:
        lines.append(f"  - [#{a['id']}] {a['obs'][:16]} · {a['title']} ({a['outlet']}, {a['country']})")
    dg = stats["dgim_last_N_articles"]
    if dg:
        part = f", 추적 후 {dg['observed']:,}/{dg['window']:,}건만 관측" if dg["partial"] else ""
        lines.append(f"- DGIM: 최근 {dg['window']:,}건 중 약 {dg['estimate']}건 (정확 {dg['exact']}건{part})")
    lines += ["", "※ 오프라인 요약 모드(Solar 미사용): 도구 결과를 템플릿으로 정리했습니다."]
    answer = "\n".join(lines)
    return {**base, "keyword": keyword, "trace": trace,
            "citations": citations(answer, _tools_view(tools)), "answer": answer}
