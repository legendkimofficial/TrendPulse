"""Question answering over the precomputed GKG dashboard cache.

Same contract as the HuffPost agent (trendpulse/agent.py, reused only for its HTTP
helper): the model must call tools, and every cited article id [#idx] must be one the
tools returned. With UPSTAGE_API_KEY unset (or on any API/verification failure) an
offline template answer is built from the same tools.

Tools read one cached snapshot (the time selected on the slider):
  get_top(tab, kind, k)   rows of a tab ("major" 7-day, "new" 72-hour, "trend" 30-day)
  get_key_detail(key)     the key's row(s) at that time + stored evidence articles
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
from datetime import datetime, timezone

from trendpulse.agent import CITE_RE, DEFAULT_BASE_URL, DEFAULT_MODEL, _post
from trendpulse.gkg_cache import KIND_KO, CacheReader

MAX_ROUNDS = 5
TAB_KO = {"major": "7일 주요 이슈", "new": "72시간 신규 이슈", "trend": "30일 반짝·꾸준"}

SYSTEM_PROMPT = """너는 'Trend Pulse GKG' 뉴스 이슈 분석 도우미다.
데이터: GDELT GKG 2.1 영어 기사 흐름(인물 P:, 기관 O:, 장소 L: 키). 점수는 시간 감쇠 점수
(반감기 72시간/168시간/720시간), 보도율은 시작 시점 보정을 한 시간당 기사 수다.
규칙:
1. 반드시 도구를 호출해 확인한 뒤 답한다. 도구 결과에 없는 사실은 지어내지 않는다.
2. 특정 키에 대한 질문이면 get_key_detail로 근거 기사를 조회하고 [#idx] 형식으로 인용한다.
   조회 결과에 없는 idx는 인용하지 않는다.
3. 답변: (1) 현재 순위·보도율·분류 한 줄 (2) 근거 기사 제목으로 본 요점 2~4개 (3) 한계 한 줄
   (근거 기사는 하루 30건 상한의 표본이며, 언론사 수는 최대 8곳까지 근사한다).
4. 한국어로 간결하게 답한다. 이름과 기사 제목은 영어 원문을 유지해도 된다."""

TOOLS = [
    {"type": "function", "function": {
        "name": "get_top", "description": "선택한 시점의 탭 목록 (major=7일 주요, new=72시간 신규, trend=30일 반짝·꾸준)",
        "parameters": {"type": "object", "properties": {
            "tab": {"type": "string", "enum": ["major", "new", "trend"]},
            "kind": {"type": "string", "enum": ["all", "P", "O", "L"]},
            "k": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["tab"]}}},
    {"type": "function", "function": {
        "name": "get_key_detail", "description": "키 하나의 점수·보도율·분류와 근거 기사(idx, 제목, 언론사, 시각)",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "예: 'P:joe biden' 또는 이름만 'joe biden'"}},
            "required": ["key"]}}},
]


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class GkgTools:
    def __init__(self, reader: CacheReader, t: int | None) -> None:
        self.reader = reader
        self.snap = reader.snapshot(t)
        self.seen_idx: set[int] = set()

    def resolve(self, key: str) -> str | None:
        key = " ".join(str(key).split()).lower()
        if len(key) > 2 and key[1] == ":" and key[0] in "pol":
            return key[0].upper() + key[1:]
        for tab in self.snap["tabs"].values():
            for r in tab["rows"]["all"] + tab["rows"]["P"] + tab["rows"]["O"] + tab["rows"]["L"]:
                if r["name"] == key:
                    return r["key"]
        return None

    def get_top(self, tab: str = "major", kind: str = "all", k: int = 10) -> dict:
        if tab not in self.snap["tabs"]:
            return {"error": f"unknown tab {tab}"}
        kind = kind if kind in ("all", "P", "O", "L") else "all"
        k = max(1, min(int(k or 10), 20))
        t = self.snap["tabs"][tab]
        src = t["rows"][kind]
        if tab == "trend":      # three groups: first k of each
            src = [r for g in ("rising", "cooling", "steady") for r in [x for x in src if x.get("group") == g][:k]]
        else:
            src = src[:k]
        rows = [{"rank": i, "key": r["key"], "rate_per_hour": r["rate"], "label": r["label_ko"],
                 "ratio": r.get("ratio"), "evidence": r["evidence"], "outlets": r["outlets"]}
                for i, r in enumerate(src, 1)]
        return {"tab": TAB_KO[tab], "status": t["status"], "as_of": iso(self.snap["t"]), "rows": rows}

    def get_key_detail(self, key: str) -> dict:
        k = self.resolve(key)
        if k is None:
            return {"error": f"'{key}' is not in any list at {iso(self.snap['t'])}"}
        row = self.reader.find_row(self.snap, k)
        ev = self.reader.evidence(k, self.snap["articles"], limit=8)
        for a in ev["articles"]:
            self.seen_idx.add(a["idx"])
        return {"key": k, "as_of": iso(self.snap["t"]), "row": row,
                "evidence_total": ev["total"],
                "articles": [{"idx": a["idx"], "time": iso(a["ts"]), "domain": a["domain"], "title": a["title"]}
                             for a in ev["articles"]]}

    def call(self, name: str, args: dict) -> dict:
        try:
            if name == "get_top":
                return self.get_top(args.get("tab", "major"), args.get("kind", "all"), args.get("k", 10))
            if name == "get_key_detail":
                return self.get_key_detail(args.get("key", ""))
        except (TypeError, ValueError) as exc:
            return {"error": str(exc)}
        return {"error": f"unknown tool {name}"}


def guess_key(tools: GkgTools, question: str) -> str | None:
    """Longest row name that occurs in the question (case-insensitive)."""
    q = " ".join(question.lower().split())
    best = None
    for tab in tools.snap["tabs"].values():
        for kind in ("all", "P", "O", "L"):
            for r in tab["rows"][kind]:
                if len(r["name"]) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(r["name"]) + r"(?![a-z0-9])", q):
                    if best is None or len(r["name"]) > len(best[1]):
                        best = (r["key"], r["name"])
    return best[0] if best else None


def check_citations(answer: str, tools: GkgTools, need_cite: bool) -> list[str]:
    cited = {int(x) for x in CITE_RE.findall(answer)}
    problems = []
    bad = sorted(cited - tools.seen_idx)
    if bad:
        problems.append(f"조회하지 않은 기사 인용 {bad}")
    if need_cite and not cited:
        problems.append("근거 기사 인용 없음")
    return problems


def ask(reader: CacheReader, question: str, t: int | None = None, key: str | None = None) -> dict:
    tools = GkgTools(reader, t)
    key = guess_key(tools, question) or (tools.resolve(key) if key else None)
    api_key = os.environ.get("UPSTAGE_API_KEY", "").strip()
    if api_key:
        try:
            res = _solar(tools, question, key, api_key)
            if res is not None:
                return res
            out = _offline(tools, key)
            out.update(mode="offline-fallback", warning="Solar 답변이 근거 검증을 통과하지 못해 오프라인 요약으로 대체했습니다.")
            return out
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, IndexError) as exc:
            out = _offline(GkgTools(reader, t), key)
            out.update(mode="offline-fallback", warning=f"Solar API 호출 실패로 오프라인 요약을 표시합니다: {exc}")
            return out
    return _offline(tools, key)


def _solar(tools: GkgTools, question: str, key: str | None, api_key: str) -> dict | None:
    base = os.environ.get("UPSTAGE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("UPSTAGE_MODEL", DEFAULT_MODEL)
    hint = f"\n(선택 시점: {iso(tools.snap['t'])}" + (f", 질문 대상 키: '{key}')" if key else ")")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question + hint}]
    trace, n_calls, repaired = [], 0, False
    for _ in range(MAX_ROUNDS + 2):
        payload = {"model": model, "messages": messages, "tools": TOOLS, "temperature": 0.2,
                   "tool_choice": "required" if n_calls == 0 else "auto"}
        msg = _post(f"{base}/chat/completions", api_key, payload)["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if calls:
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls:
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = tools.call(c["function"]["name"], args if isinstance(args, dict) else {})
                n_calls += 1
                trace.append({"tool": c["function"]["name"], "args": args})
                messages.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
            continue
        answer = msg.get("content") or ""
        problems = check_citations(answer, tools, need_cite=key is not None)
        if not problems:
            return {"answer": answer, "mode": f"solar ({model})", "key": key, "as_of": iso(tools.snap["t"]),
                    "trace": trace, "verified": True}
        if repaired:
            return None
        repaired = True
        messages.append({"role": "assistant", "content": answer})
        messages.append({"role": "user", "content": "근거 검증 실패: " + "; ".join(problems)
                         + ". get_key_detail 결과에 있는 idx만 [#idx]로 인용해 다시 써줘."})
    return None


def _offline(tools: GkgTools, key: str | None) -> dict:
    as_of = iso(tools.snap["t"])
    base = {"mode": "offline", "key": key, "as_of": as_of, "verified": True}
    if not key:
        parts, trace = [], []
        for tab in ("major", "new", "trend"):
            top = tools.get_top(tab, "all", 5)
            trace.append({"tool": "get_top", "args": {"tab": tab, "k": 5}})
            if top["rows"]:
                names = ", ".join(f"{r['rank']}. {r['key']} ({r['label']})" for r in top["rows"])
            else:
                names = f"({tools.snap['tabs'][tab].get('label_ko') or top['status']})"
            parts.append(f"**{top['tab']}**: {names}")
        return {**base, "trace": trace, "answer": f"{as_of} 기준\n\n" + "\n\n".join(parts)
                + "\n\n특정 이름을 넣어 물어보면 근거 기사로 설명합니다 (예: \"joe biden 왜 많이 나와?\")."}
    d = tools.get_key_detail(key)
    trace = [{"tool": "get_key_detail", "args": {"key": key}}]
    if "error" in d:
        return {**base, "trace": trace, "answer": d["error"]}
    r = d["row"] or {}
    kind = KIND_KO.get(r.get("kind", ""), "")
    lines = [f"**{key}** ({kind}) — {as_of} 기준 {TAB_KO.get(r.get('tab'), '')}, "
             f"보정 보도율 {r.get('rate')}/시간, 분류 {r.get('label_ko')}."]
    if r.get("lift") is not None:
        lines.append(f"- lift {r['lift']} (72h {r.get('rate')}/h vs 168h {r.get('rate_168h')}/h), "
                     f"최근 72시간 기사 {r.get('articles_72h')}건")
    if r.get("outlets") is not None:
        lines.append(f"- 최근 72시간 언론사 {r['outlets']}곳 (최대 8곳까지 근사)")
    dg = r.get("dgim")
    if dg:
        part = f", 추적 후 {dg['observed']:,}건만 관측" if dg["partial"] else ""
        lines.append(f"- DGIM: 최근 {dg['window']:,}건 중 약 {dg['estimate']}건 언급{part}")
    lines.append(f"- 저장된 근거 기사 {d['evidence_total']}건 중 최근:")
    for a in d["articles"][:5]:
        lines.append(f"  - [#{a['idx']}] {a['time']} · {a['domain']} · {a['title']}")
    if not d["articles"]:
        lines.append("  - (저장된 근거 기사 없음: Top 200에 든 뒤부터만 저장)")
    lines += ["", "※ 오프라인 요약 모드: 도구 결과를 템플릿으로 정리했습니다. 근거 기사는 키마다 하루 30건 상한의 표본입니다."]
    return {**base, "trace": trace, "answer": "\n".join(lines)}
