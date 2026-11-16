# 종단 시연 증거 (7일 순위 → 72h 급상승 → 관련 기사 → Solar)

- 입력 SHA-256 `0c5043b3874e3b07f2343687d0d70cc5c03bfdf40446d666859356221bc3c00f` · 기준 시각 2026-11-15T12:00:00Z · 재생 755,193건 · 키워드 `u.s.`
- Solar: 오프라인 모드(UPSTAGE_API_KEY 없음)

## Solar 답변
```
**u.s.** — 2026-11-15T12:00:00Z 관측 기준 순위 1, 7일 점수 9251.25, 72시간 급상승 보류(collection_gap).
- 7일 내 제목 언급 24790건, 보도 매체 3314곳 (발행국: AE, AT, AU, AZ, BB, BD, BN, BZ).
- 최근 관련 기사:
  - [#755134] 2026-11-15T11:46 · Indian products to get preferential access to US market over rival nations: Piyush Goyal - The Tribune (The Tribune, unknown)
  - [#755129] 2026-11-15T11:46 · Ringgit seen remaining soft against US dollar next week (The Star, MY)
  - [#755122] 2026-11-15T11:46 · At least two injured as passenger, cargo planes collide at U.S. airport (The Hindu, IN)
  - [#755025] 2026-11-15T11:46 · Iran strikes US base, Bahrain intercepts aerial attacks (newcastleherald.com.au, AU)
- DGIM: 최근 1,000건 중 약 22.5건 (정확 23건)

※ 오프라인 요약 모드(Solar 미사용): 도구 결과를 템플릿으로 정리했습니다.
```

## 도구 호출 추적
- `get_topic_stats({"keyword": "u.s."})` → 순위 1, 점수 9251.25
- `search_related_news({"keyword": "u.s.", "limit": 6})` → 기사 6건

## 검증
- 통과 — 도구 호출됨: ['get_topic_stats', 'search_related_news']
- 통과 — 동일 키워드 사용: u.s.
- 통과 — 동일 기준 시각(as_of): {'pos': 755193, 't_iso': '2026-11-15T12:00:00Z'}
- 통과 — 인용 기사가 도구 결과에 존재: [(755134, True), (755129, True), (755122, True), (755025, True)]
- 통과 — 답변 수치 == 엔진 점수: engine=9251.25
- 통과 — 오프라인 응답 표시: offline
