# End-to-End Demo Evidence

- Input SHA-256: `0c5043b3874e3b07f2343687d0d70cc5c03bfdf40446d666859356221bc3c00f`
- Replay position: 755,193 articles as of November 15, 2026, at 12:00 UTC
- Keyword: `u.s.`
- Response mode: offline template. The Upstage API key was unavailable.
- The data gap deferred the 72-hour surge calculation.

## Captured offline response

The response below preserves the original Korean output for comparison with the structured trace.

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

## Tool trace and checks

The demo recorded `get_topic_stats` returning rank 1 and a score of 9251.25. It also recorded `search_related_news` returning six articles for the same keyword. All six checks passed: the expected tools ran, the keyword and replay position agreed, all four cited IDs appeared in the tool results, the score matched the engine, and the response identified its mode as offline.

See [the original structured trace](solar_demo.json) for exact inputs and outputs.
