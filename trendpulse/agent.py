"""Upstage Solar agent that explains *why* a keyword is trending.

The model never sees the ranking directly; it must call tools that read the
engine state (statistics, related articles, co-occurring words) and answer from
what those tools returned, citing article ids as evidence.

Solar's chat API is OpenAI-compatible:
  POST {UPSTAGE_BASE_URL}/chat/completions   (default https://api.upstage.ai/v1)
  env: UPSTAGE_API_KEY (required for online mode), UPSTAGE_MODEL (default solar-pro2)

Evidence checks done by the application (not left to the prompt):
  * the first request uses tool_choice="required", so at least one tool is called
  * a question about a keyword must have fetched related articles
    (search_related_news) and the answer must cite at least one of them
  * every cited id [#n] must be an article returned by a tool in this session
  * on failure the model gets one corrective retry; if it still fails, the
    offline summary is returned with a warning (the unverified text is not shown)

Concurrency: tool calls take the engine lock one at a time and read articles
only up to ``as_of`` (the stream position when the question was asked), so the
HTTP round-trips to Solar do not block the dashboard.

Without an API key the agent runs in offline mode: it calls the same tools in a
fixed order and fills a template, so the demo works without network access.
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
from contextlib import nullcontext
from typing import Any, Callable

from .engine import TrendEngine
from .text import tokenize

DEFAULT_BASE_URL = "https://api.upstage.ai/v1"
DEFAULT_MODEL = "solar-pro2"
MAX_TOOL_ROUNDS = 5
MAX_REPAIRS = 1
CITE_RE = re.compile(r"\[#(\d+)\]")

SYSTEM_PROMPT = """너는 'Trend Pulse' 뉴스 화제 키워드 분석 도우미다.
데이터: HuffPost 뉴스 제목(영어)을 날짜순으로 재생하는 스트림. 순위 점수는 감쇠 윈도우 점수다
(최근 기사일수록 가중치가 크고, 약 693개 기사마다 영향이 절반으로 줄어든다).

규칙:
1. 반드시 도구를 호출해 통계와 관련 기사를 확인한 뒤 답한다. 도구 결과에 없는 사실은 지어내지 않는다.
2. 특정 키워드에 대한 질문이면 search_related_news로 기사를 조회하고, 근거로 쓴 기사를 [#기사id] 형식으로 인용한다.
   조회 결과에 없는 id는 절대 인용하지 않는다.
3. 답변 구성: (1) 현재 순위·점수와 최근 추이 한 줄 (2) 어떤 내용으로 언급되는지 2~4개 요점 (3) 한계 한 줄.
4. 한계에는 '이 순위는 HuffPost 데이터 안에서 관찰한 화제이며 사회 전체의 관심도가 아니다'는 점,
   DGIM 값은 근사값이라는 점을 필요할 때 밝힌다.
5. 한국어로 간결하게 답한다. 키워드와 기사 제목은 영어 원문을 유지해도 된다."""

TOOLS = [
    {"type": "function", "function": {
        "name": "get_top_keywords",
        "description": "현재 시점의 화제 키워드 순위(감쇠 윈도우 점수)와 최근 Top K 진입 키워드를 반환한다.",
        "parameters": {"type": "object", "properties": {
            "k": {"type": "integer", "description": "가져올 순위 수 (1~30)", "default": 10}}}}},
    {"type": "function", "function": {
        "name": "get_keyword_stats",
        "description": "키워드의 현재 순위, 감쇠 점수, 정확 기준 점수, 점수 추이, 최근/이전 구간 등장 수, DGIM 최근 N개 기사 등장 수(근사/정확), 함께 등장한 단어, 카테고리 분포를 반환한다.",
        "parameters": {"type": "object", "required": ["keyword"], "properties": {
            "keyword": {"type": "string", "description": "영어 소문자 키워드, 예: 'trump'"}}}}},
    {"type": "function", "function": {
        "name": "search_related_news",
        "description": "질문 시점까지 스트림에 들어온 기사 중 키워드를 제목에 포함한 최신 기사를 반환한다 (id, 날짜, 제목, 짧은 설명, 카테고리).",
        "parameters": {"type": "object", "required": ["keyword"], "properties": {
            "keyword": {"type": "string"},
            "limit": {"type": "integer", "default": 8, "description": "1~15"},
            "offset": {"type": "integer", "default": 0, "description": "더 과거 기사를 보려면 이미 본 개수만큼 건너뛴다"}}}}},
]


class TrendTools:
    """Tool implementations over a live engine (read-only).

    Each call holds ``lock`` only for its own duration. Article lookups are
    limited to ``as_of``; ``evidence`` collects every article a tool returned.
    """

    def __init__(self, engine: TrendEngine, lock: threading.Lock | None = None, as_of: int | None = None):
        self.e = engine
        self.lock = lock or nullcontext()
        self.as_of = engine.now if as_of is None else as_of
        self.evidence: dict[int, dict] = {}
        self.searched: set[str] = set()
        self.drifted = False      # stream moved between the question and a tool call

    def _check_drift(self) -> None:
        if self.e.now != self.as_of:
            self.drifted = True

    def get_top_keywords(self, k: int = 10) -> dict:
        k = max(1, min(int(k), 30))
        with self.lock:
            self._check_drift()
            st = self.e.state()
        return {
            "as_of_date": st["clock"]["date"], "article_t": st["clock"]["t"],
            "top": [{"rank": r["rank"], "keyword": r["keyword"], "score": r["score"],
                     "rank_change": r["delta"]} for r in st["ranking"][:k]],
            "recent_topk_entries": [{"keyword": ev["keyword"], "date": ev["date"], "rank": ev["rank"],
                                     "kind": "첫 진입" if ev["kind"] == "first" else "재진입"}
                                    for ev in st["events"][:8]],
        }

    def get_keyword_stats(self, keyword: str) -> dict:
        with self.lock:
            self._check_drift()
            d = self.e.keyword_detail(keyword)
            date = self.e.clock()["date"]
            win = self.e.clock()["effective_window"]
        hist = d["history"][-8:]
        return {
            "keyword": d["keyword"], "as_of_date": date,
            "current_rank": d["rank"], "in_top_k": d["in_top_k"], "top_k_size": self.e.config.k,
            "pruned_from_memory": not d["in_pruned"],
            "decayed_score": d["score"], "exact_baseline_score": d["baseline_score"],
            "mentions_so_far": d["total_mentions_so_far"],
            "window_articles": win,
            "mentions_in_last_window": d["mentions_in_effective_window"],
            "mentions_in_previous_window": d["mentions_in_previous_window"],
            "first_topk_entry": d["first_entry"],
            "score_trend": [{"date": h["date"], "score": h["score"], "rank": h["rank"]} for h in hist],
            "dgim_last_N_articles": d["dgim"],
            "cooccurring_words": d["cooccurring"],
            "categories": d["categories"],
        }

    def search_related_news(self, keyword: str, limit: int = 8, offset: int = 0) -> dict:
        limit = max(1, min(int(limit), 15))
        with self.lock:
            self._check_drift()
            items = self.e.related(keyword, limit=limit, offset=max(0, int(offset)), as_of=self.as_of)
        self.searched.add(keyword.lower())
        for it in items:
            self.evidence[it["id"]] = {"id": it["id"], "date": it["date"], "headline": it["headline"],
                                       "link": it["link"]}
            it["description"] = it["description"][:220]
            it.pop("link", None)
        return {"keyword": keyword.lower(), "count": len(items), "articles": items}

    def call(self, name: str, args: dict) -> dict:
        fn: Callable[..., dict] | None = {
            "get_top_keywords": self.get_top_keywords,
            "get_keyword_stats": self.get_keyword_stats,
            "search_related_news": self.search_related_news,
        }.get(name)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**args)
        except (TypeError, ValueError) as exc:
            return {"error": f"bad arguments: {exc}"}


def guess_keyword(engine: TrendEngine, question: str) -> str | None:
    """Pick the keyword a question is about (the highest-scoring known token)."""
    cands = [w for w in tokenize(question) if w in engine.postings]
    if not cands:
        return None
    return max(cands, key=engine.pruned.score)


def check_evidence(answer: str, tools: TrendTools, keyword: str | None, n_tool_calls: int) -> list[str]:
    """Return a list of problems; empty means the answer passes the evidence checks."""
    problems = []
    if n_tool_calls == 0:
        problems.append("도구를 호출하지 않고 답변함")
    cited = {int(x) for x in CITE_RE.findall(answer)}
    invalid = sorted(cited - set(tools.evidence))
    if invalid:
        problems.append("조회 결과에 없는 기사 인용: " + ", ".join(f"[#{i}]" for i in invalid))
    if keyword:
        if keyword not in tools.searched:
            problems.append(f"'{keyword}' 관련 기사를 조회하지 않음")
        elif tools.evidence and not cited:
            problems.append("조회한 기사를 하나도 인용하지 않음")
    return problems


def citations(answer: str, tools: TrendTools) -> list[dict]:
    out = []
    for i in dict.fromkeys(int(x) for x in CITE_RE.findall(answer)):
        ev = tools.evidence.get(i)
        out.append({"id": i, "valid": ev is not None, **(ev or {})})
    return out


# ---------------------------------------------------------------------- Solar API
def _post(url: str, key: str, payload: dict, timeout: float = 60) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ask(engine: TrendEngine, question: str, keyword: str | None = None,
        lock: threading.Lock | None = None) -> dict[str, Any]:
    """Answer ``question``.

    Returns {answer, mode, keyword, as_of, trace, citations, verified, warning?}.
    """
    lk = lock or nullcontext()
    with lk:
        # A keyword named in the question wins over the one selected on screen.
        keyword = guess_keyword(engine, question) or (keyword or "").strip().lower() or None
        as_of = engine.now
        as_of_info = {"t": as_of, "date": engine.articles[max(as_of, 0)].date}
    key = os.environ.get("UPSTAGE_API_KEY", "").strip()
    if not key:
        return _offline_answer(engine, keyword, lock, as_of, as_of_info)
    try:
        return _solar_answer(engine, question, keyword, key, lock, as_of, as_of_info)
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, IndexError) as exc:
        res = _offline_answer(engine, keyword, lock, as_of, as_of_info)
        res["mode"] = "offline-fallback"
        res["warning"] = f"Solar API 호출 실패로 오프라인 요약을 표시합니다: {exc}"
        return res


def _solar_answer(engine, question, keyword, key, lock, as_of, as_of_info) -> dict:
    tools = TrendTools(engine, lock, as_of)
    base = os.environ.get("UPSTAGE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("UPSTAGE_MODEL", DEFAULT_MODEL)
    hint = f"\n(질문 대상 키워드: '{keyword}')" if keyword else ""
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question + hint},
    ]
    trace: list[dict] = []
    n_calls = 0               # real tool calls (evidence-check entries excluded)
    repairs = 0
    rounds = 0
    answer = ""
    while rounds < MAX_TOOL_ROUNDS + MAX_REPAIRS + 1:
        rounds += 1
        payload = {"model": model, "messages": messages, "tools": TOOLS, "temperature": 0.2,
                   # force a tool call until at least one has happened
                   "tool_choice": "required" if n_calls == 0 else "auto"}
        msg = _post(f"{base}/chat/completions", key, payload)["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if calls:
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
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
        problems = check_evidence(answer, tools, keyword, n_calls)
        if not problems:
            res = {"answer": answer, "mode": f"solar ({model})", "keyword": keyword, "as_of": as_of_info,
                   "trace": trace, "citations": citations(answer, tools), "verified": True}
            if tools.drifted:
                res["warning"] = "질문 처리 중 스트림이 진행되어 일부 통계는 더 최신 시점 기준입니다."
            return res
        if repairs >= MAX_REPAIRS:
            break
        repairs += 1
        trace.append({"tool": "evidence-check", "args": {}, "summary": "; ".join(problems)})
        messages.append({"role": "assistant", "content": answer})
        messages.append({"role": "user", "content": (
            "답변이 근거 검증을 통과하지 못했다: " + "; ".join(problems) +
            ". 필요하면 search_related_news를 호출하고, 도구 결과에 있는 기사 id만 [#id]로 인용해 다시 작성해줘.")})

    res = _offline_answer(engine, keyword, lock, as_of, as_of_info)
    res["mode"] = "offline-fallback"
    res["trace"] = trace + res["trace"]
    res["warning"] = ("Solar 답변이 근거 검증을 통과하지 못해 오프라인 요약으로 대체했습니다 ("
                      + "; ".join(check_evidence(answer, tools, keyword, n_calls)) + ").")
    return res


def _summarize(result: dict) -> str:
    if "error" in result:
        return "오류: " + result["error"]
    if "articles" in result:
        return f"기사 {result['count']}건"
    if "top" in result:
        return f"상위 {len(result['top'])}개"
    if "current_rank" in result:
        return f"순위 {result['current_rank']}, 점수 {result['decayed_score']}"
    return "ok"


# ---------------------------------------------------------------------- offline mode
def _offline_answer(engine: TrendEngine, keyword: str | None, lock, as_of: int, as_of_info: dict) -> dict:
    tools = TrendTools(engine, lock, as_of)
    trace = []
    base = {"mode": "offline", "as_of": as_of_info, "verified": True}
    if not keyword:
        top = tools.get_top_keywords(10)
        trace.append({"tool": "get_top_keywords", "args": {"k": 10}, "summary": _summarize(top)})
        names = ", ".join(f"{r['rank']}. {r['keyword']}" for r in top["top"])
        news = ", ".join(f"{e['keyword']}({e['date']}, {e['kind']})" for e in top["recent_topk_entries"][:5]) or "없음"
        example = top["top"][0]["keyword"] if top["top"] else "trump"
        return {**base, "keyword": None, "trace": trace, "citations": [], "answer": (
            f"**{top['as_of_date']} 기준 화제 키워드 Top 10**\n{names}\n\n"
            f"최근 Top K 진입: {news}\n\n"
            f"특정 키워드를 물어보면 관련 기사를 근거로 설명합니다 "
            f"(예: \"{example} 왜 순위에 올랐어?\"). 키워드는 영어로 입력하세요.")}

    stats = tools.get_keyword_stats(keyword)
    trace.append({"tool": "get_keyword_stats", "args": {"keyword": keyword}, "summary": _summarize(stats)})
    news = tools.search_related_news(keyword, limit=5)
    trace.append({"tool": "search_related_news", "args": {"keyword": keyword, "limit": 5},
                  "summary": _summarize(news)})

    if not news["articles"]:
        return {**base, "keyword": keyword, "trace": trace, "citations": [],
                "answer": f"'{keyword}'를 제목에 포함한 기사가 아직 스트림에 없습니다 ({as_of_info['date']} 기준)."}

    rank = stats["current_rank"]
    if rank is None:
        rank_txt = "점수가 θ 아래로 내려가 집계에서 삭제된 상태"
    elif stats["in_top_k"]:
        rank_txt = f"현재 {rank}위 (Top {stats['top_k_size']} 안)"
    else:
        rank_txt = f"현재 {rank}위 (Top {stats['top_k_size']} 밖)"
    co = ", ".join(w for w, _ in stats["cooccurring_words"][:6]) or "-"
    cats = ", ".join(f"{c}({n})" for c, n in stats["categories"][:3]) or "-"
    fe = stats["first_topk_entry"]
    lines = [
        f"**{keyword}** — {stats['as_of_date']} 기준 {rank_txt}, 감쇠 점수 {stats['decayed_score']} "
        f"(정확 기준 {stats['exact_baseline_score']}).",
        "",
        f"- 최근 {stats['window_articles']:,}건 중 {stats['mentions_in_last_window']}건의 제목에 등장 "
        f"(그 이전 {stats['window_articles']:,}건: {stats['mentions_in_previous_window']}건)",
    ]
    if fe:
        lines.append(f"- Top {stats['top_k_size']} {'첫 진입' if fe['kind'] == 'first' else '재진입'}: {fe['date']} ({fe['rank']}위)")
    lines += [f"- 함께 많이 등장한 단어: {co}", f"- 주요 카테고리: {cats}", "- 최근 관련 기사:"]
    for a in news["articles"][:4]:
        lines.append(f"  - [#{a['id']}] {a['date']} · {a['headline']}")
    dg = stats["dgim_last_N_articles"]
    if dg:
        part = f", 추적 후 {dg['observed']:,}/{dg['window']:,}건만 관측" if dg["partial"] else ""
        lines.append(f"- DGIM: 최근 {dg['window']:,}건 중 약 {dg['estimate']}건 (정확 {dg['exact']}건{part})")
    lines += ["", "※ 오프라인 요약 모드(Solar 미사용): 도구 결과를 템플릿으로 정리했습니다. "
                  "순위는 HuffPost 데이터 안에서 관찰한 화제입니다."]
    answer = "\n".join(lines)
    return {**base, "keyword": keyword, "trace": trace, "citations": citations(answer, tools), "answer": answer}
