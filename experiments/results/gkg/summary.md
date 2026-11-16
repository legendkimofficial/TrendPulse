# GKG experiments summary

<!-- gkg_experiments:begin -->
## GKG experiments (E1–E5)

> **경고:** gkg_events.json 의 flash_vs_steady_reference/us_election 는 verified=false (출처 미확인).

- 실행 명령: `python experiments/run_gkg_experiments.py`
- 기간: 2024-09-15 .. 2024-11-14 (UTC), 평가 시작 2024-10-01, 6시간 간격
- 입력 해시: stream SHA-256 `fb77326c554e5eae595c2e38545f8eed5db0d2ae483826db7fb850ebd359fa5b` (8,313,313 articles; 정의는 input_manifest.json 과 같음)
- git 0340e4805929bfb653333ca018c19280b45e8e1f (dirty=True), 시드 20241001, 결과 `results.json`, 그림 `figures/`
- 실행 시간: main 107.9 min, E5 2% 10.4 min, E5 5% 10.3 min, peak RSS 4.42 GB

### E1 메모리와 순위 (정확 기준 대비)

| 창 | θ | 키 수(평균) | 메모리 MB(평균) | Top-20 겹침 평균 / 최소 | 순서 동일 | 점수 상대오차 평균 / 최대 |
|---|---|---:|---:|---:|---:|---:|
| 72h | 정확 | 3,803,189 | 202.01 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 72h | 0.1 | 1,539,145 | 98.46 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 72h | 0.3 | 1,023,940 | 55.33 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 72h | 0.5 | 751,256 | 44.69 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 72h | 1.0 | 245,003 | 13.57 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 168h | 정확 | 3,803,189 | 202.01 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 168h | 0.1 | 2,769,263 | 168.65 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 168h | 0.3 | 1,917,303 | 107.53 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 168h | 0.5 | 1,430,905 | 91.76 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 168h | 1.0 | 447,266 | 26.11 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 720h | 정확 | 3,803,189 | 202.01 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 720h | 0.1 | 3,803,189 | 202.01 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 720h | 0.3 | 3,759,025 | 200.95 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 720h | 0.5 | 3,236,340 | 188.4 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |
| 720h | 1.0 | 788,991 | 44.82 | 1.000 / 1.00 | 100.0% | 0.0% / 0.0% |

[해석] 점수 상대오차는 각 평가 시점의 정확 기준 Top-20 키에서 |정확−근사|/정확 을 모두 모아 평균·최대를 낸 값. 메모리는 dict + float 추정(키 문자열 제외).

### E2 신규 이슈 포착 (72시간 신규 Top 20, 첫 진입)

| 사건 | 첫 보도 (UTC) | 정확: 진입 시각 / 지연 h / 키 | θ=0.5: 진입 시각 / 지연 h / 키 |
|---|---|---|---|
| milton 허리케인 밀턴 플로리다 상륙 | 2024-10-10 00:30 | 2024-09-24 09:00 / -375.5 / L:bletchley, milton keynes, united kingdom (#19) | 2024-09-24 09:00 / -375.5 / L:bletchley, milton keynes, united kingdom (#19) |
| han_kang 한강 노벨문학상 발표 | 2024-10-10 11:00 | 놓침 | 놓침 |
| liam_payne 리암 페인 사망 | 2024-10-16 22:30 | 놓침 | 놓침 |
| sinwar 야히야 신와르 사망 발표 | 2024-10-17 16:55 | 2024-10-17 23:00 / +6.1 / P:yahya sinwar (#20) | 2024-10-17 23:00 / +6.1 / P:yahya sinwar (#20) |
| valencia 스페인 발렌시아 홍수 | 2024-10-29 19:11 | 2024-10-30 14:00 / +18.8 / L:utiel, valenciana, spain (#14) | 2024-10-30 14:00 / +18.8 / L:utiel, valenciana, spain (#14) |
| quincy_jones 퀸시 존스 사망 보도 | 2024-11-04 09:00 | 2024-11-04 11:00 / +2.0 / P:quincy jones (#15) | 2024-11-04 11:00 / +2.0 / P:quincy jones (#15) |

참고(준비 기간 사건, 신규 판별은 7일 준비 뒤라 포착 대상 아님):

- helene 허리케인 헬렌 상륙: 정확 놓침; θ=0.5 놓침
- nasrallah 하산 나스랄라 사망 발표 (이스라엘군): 정확 2024-09-29 10:00 / +26.0 / P:sayyed hassan nasrallah (#19); θ=0.5 2024-09-29 10:00 / +26.0 / P:sayyed hassan nasrallah (#18)

### E3 반짝/꾸준 (00:00 UTC, 168h Top 2,000 후보)

**2024-10-15** (상태 ok; 후보 2000: cooling 15, rising 29, steady 1956)
- 최근 급증: binyamina, h_efa, israel (1.794), baba siddique (1.739), alex salmond (1.697), vem miller (1.695), coachella, california, united states (1.676), abu hamza (1.657), alba party (1.622), kenny macaskill (1.617), simon johnson (1.612), joshua simmons (1.607)
- 식는 중: ryan wesley routh (0.447), trump international golf club (0.503), david lochridge (0.523), jean smart (0.57), west palm beach, florida, united states (0.572), firass abiad (0.6), australian community media (0.611), ryan routh (0.612), a red ventures company (0.623), ranil wickremesinghe (0.629)
- 꾸준: new york, united states (0.925), florida, united states (1.1), washington, washington, united states (0.962), donald trump (0.96), california, united states (0.992), london, london, city of, united kingdom (0.985), kamala harris (0.98), instagram (0.978), texas, united states (0.981), joe biden (0.97)

**2024-11-01** (상태 ok; 후보 2000: cooling 31, rising 155, steady 1814)
- 최근 급증: tony hinchcliffe (2.256), utiel, valenciana, spain (2.053), castilla la mancha, castilla-la manacha, spain (2.042), teri garr (2.042), young frankenstein (1.983), madison square, florida, united states (1.972), greg kimsey (1.957), allentown, pennsylvania, united states (1.953), voto latino (1.942), ricky martin (1.926)
- 식는 중: valdosta, georgia, united states (0.464), international longshoremen association (0.464), national hurricane center, florida, united states (0.48), national hurricane center (0.524), brian kemp (0.525), ryan routh (0.531), west palm beach, florida, united states (0.532), jerome powell (0.546), gulf of mexico, mexico (general), mexico (0.571), deanne criswell (0.577)
- 꾸준: new york, united states (1.036), donald trump (1.144), washington, washington, united states (1.088), kamala harris (1.172), california, united states (1.036), instagram (1.064), london, london, city of, united kingdom (1.005), texas, united states (1.034), florida, united states (0.858), associated press (1.028)

**2024-11-08** (상태 ok; 후보 2000: cooling 27, rising 202, steady 1771)
- 최근 급증: grover cleveland (2.293), howard university (2.217), palm beach convention center (2.198), bernie moreno (2.135), cedric richmond (2.13), quincy jones (2.128), sarah mcbride (2.086), sherrod brown (2.073), quincy jone (2.073), angela alsobrooks (2.017)
- 식는 중: brian kemp (0.474), siesta key, florida, united states (0.48), tampa bay, florida, united states (0.486), tampa, florida, united states (0.488), pinellas county, florida, united states (0.502), fort myers, florida, united states (0.542), hassan nasrallah (0.551), ismail haniyeh (0.558), u s secret service (0.559), beirut, beyrouth, lebanon (0.571)
- 꾸준: donald trump (1.42), new york, united states (1.046), kamala harris (1.411), washington, washington, united states (1.188), white house, district of columbia, united states (1.454), white house (1.45), pennsylvania, united states (1.415), joe biden (1.228), florida, united states (1.039), california, united states (1.067)

**2024-11-14** (상태 ok; 후보 2000: cooling 28, rising 195, steady 1777)
- 최근 급증: pete hegseth (2.55), tom homan (2.472), department of government efficiency (2.465), john thune (2.299), john smyth (2.286), vivek ramaswamy (2.282), john cornyn (2.281), mike huckabee (2.231), john ratcliffe (2.174), stephen miller (2.155)
- 식는 중: brian kemp (0.411), p solutions (0.433), tampa, florida, united states (0.446), daniel hagari (0.449), hassan nasrallah (0.461), ismail haniyeh (0.471), tampa bay, florida, united states (0.48), asheville, north carolina, united states (0.485), fort myers, florida, united states (0.497), national hurricane center, florida, united states (0.498)
- 꾸준: donald trump (1.231), new york, united states (1.022), washington, washington, united states (1.144), kamala harris (1.104), white house, district of columbia, united states (1.363), white house (1.356), florida, united states (1.016), california, united states (1.02), texas, united states (1.068), joe biden (1.105)

참고 키 분류 변화 (매일 00:00 UTC, ratio / 라벨):

| 날짜 | P:donald trump | P:kamala harris |
|---|---|---|
| 2024-10-15 | 0.96 steady | 0.98 steady |
| 2024-10-16 | 0.976 steady | 0.992 steady |
| 2024-10-17 | 1.002 steady | 1.029 steady |
| 2024-10-18 | 1.017 steady | 1.042 steady |
| 2024-10-19 | 1.022 steady | 1.05 steady |
| 2024-10-20 | 1.003 steady | 1.027 steady |
| 2024-10-21 | 0.983 steady | 1.005 steady |
| 2024-10-22 | 0.998 steady | 1.023 steady |
| 2024-10-23 | 1.022 steady | 1.05 steady |
| 2024-10-24 | 1.057 steady | 1.075 steady |
| 2024-10-25 | 1.076 steady | 1.097 steady |
| 2024-10-26 | 1.102 steady | 1.117 steady |
| 2024-10-27 | 1.093 steady | 1.103 steady |
| 2024-10-28 | 1.077 steady | 1.092 steady |
| 2024-10-29 | 1.099 steady | 1.117 steady |
| 2024-10-30 | 1.113 steady | 1.141 steady |
| 2024-10-31 | 1.134 steady | 1.161 steady |
| 2024-11-01 | 1.144 steady | 1.172 steady |
| 2024-11-02 | 1.163 steady | 1.19 steady |
| 2024-11-03 | 1.143 steady | 1.174 steady |
| 2024-11-04 | 1.128 steady | 1.166 steady |
| 2024-11-05 | 1.155 steady | 1.198 steady |
| 2024-11-06 | 1.227 steady | 1.28 steady |
| 2024-11-07 | 1.392 steady | 1.416 steady |
| 2024-11-08 | 1.42 steady | 1.411 steady |
| 2024-11-09 | 1.405 steady | 1.369 steady |
| 2024-11-10 | 1.353 steady | 1.313 steady |
| 2024-11-11 | 1.298 steady | 1.253 steady |
| 2024-11-12 | 1.272 steady | 1.199 steady |
| 2024-11-13 | 1.247 steady | 1.147 steady |
| 2024-11-14 | 1.231 steady | 1.104 steady |

### E4 DGIM (근사) vs 정확 슬라이딩 개수

키 13개, 2024-10-01 00:00에 선택: L:new york, united states [top10], L:washington, washington, united states [top10], P:donald trump [top10], L:florida, united states [top10], L:california, united states [top10], L:london, london, city of, united kingdom [top10], L:texas, united states [top10], O:instagram [top10], P:joe biden [top10], P:kamala harris [top10], L:ottawa, ontario, canada [high (rank 100)], P:jonathan brady [mid (rank 1000)], O:kent police [low (rank 10000)]

| N | r | 표본 | 상대오차 평균 / 최대 | 이론 상한 | 키당 비트 DGIM / 정확 |
|---:|---:|---:|---:|---:|---:|
| 10,000 | 2 | 2,327 | 10.4% / 37.2% | 50.0% | 184 / 4,296 |
| 50,000 | 2 | 2,314 | 10.7% / 37.5% | 50.0% | 283 / 24,436 |
| 50,000 | 4 | 2,314 | 4.5% / 14.2% | 16.7% | 567 / 24,436 |

빈도 그룹별(평균 정확 개수, 상대오차, 비트 DGIM / 정확):

- N=10,000 r=2 · high (rank 100): 51, 10.7%, 141 / 719
- N=10,000 r=2 · low (rank 10000): 2, 3.1%, 13 / 22
- N=10,000 r=2 · mid (rank 1000): 6, 8.6%, 25 / 90
- N=10,000 r=2 · top10: 408, 10.7%, 221 / 5,502
- N=50,000 r=2 · high (rank 100): 255, 11.1%, 238 / 4,084
- N=50,000 r=2 · low (rank 10000): 8, 8.7%, 46 / 129
- N=50,000 r=2 · mid (rank 1000): 33, 8.7%, 73 / 525
- N=50,000 r=2 · top10: 2,058, 11.0%, 332 / 31,293
- N=50,000 r=4 · high (rank 100): 255, 4.6%, 454 / 4,084
- N=50,000 r=4 · low (rank 10000): 8, 2.1%, 64 / 129
- N=50,000 r=4 · mid (rank 1000): 33, 3.9%, 127 / 525
- N=50,000 r=4 · top10: 2,058, 4.7%, 673 / 31,293

### E5 빈 구간 견고성 (168h 창, 정확)

| 제거 비율 | 제거 슬롯 | 평가 시점 | Top-20 겹침 평균 / 최소 | 순서 동일 | 168h 빈 구간 평균 | 근거 부족 보류 비율 |
|---:|---:|---:|---:|---:|---:|---:|
| 2% | 117/5,856 | 180 | 0.998 / 0.95 | 75.6% | 2.0% | 0.0% |
| 5% | 293/5,856 | 180 | 0.999 / 0.95 | 64.4% | 5.1% | 57.8% |

제거 슬롯 목록은 시드로 재현되며 SHA-256 은 results.json 의 dropped_sha256.
<!-- gkg_experiments:end -->

## 전체 실험 실행 기록 메모 (2026-11-17)

- `python experiments/run_gkg_experiments.py` 는 커밋 0340e48 에서 시작했다(실행한 코드 = 0340e48).
- results.json 의 `meta.git`(dirty=True)과 `meta.code["gkg_cache.py"]`(1c7eb5a2…)는 실행이 끝날 때 기록되어,
  실행 중에 고치던 대시보드 작업 트리 상태를 담았다. 실험이 실제로 불러온 `gkg_cache.py` 는 0340e48 판(SHA-256 fe2f8732…)이고,
  실험이 쓰는 함수 `trend_groups` 는 그 뒤에도 바뀌지 않았다.
- 처리 기사 8,313,313건은 내려받기 때 센 8,313,370건보다 57건 적다. 차이의 원인은 **[미확인]**(중복 URL 제거는 0건).

<!-- input_manifest:begin -->
## Input manifest

- command: `python scripts/gkg_build.py --start 2024-10-01 --end 2024-10-01`
- manifest: `experiments/results/gkg/input_manifest.json`
- git: 07b6ffb29d7ceb4ac9227e0461bdc04ccba30f35 (dirty=False), gkg.py sha256 a4afedc209f113a4…
- config: {'include_countries': False, 'place_types': (2, 3, 4, 5), 'drop_self_orgs': True, 'drop_country_orgs': True, 'dedup_urls': True}

```
range        : 2024-10-01 .. 2024-10-01 (1 days, UTC)
15-min slots : expected 96, read 96, missing on server 0, not downloaded 0
articles     : 163,360 kept of 163,360 lines (duplicate URL 0, bad line 0, empty URL 0)
with title   : 99.8%   no keys: 7.1%   domains: 8,721
self-name    : 1,641 organization keys dropped
country org  : 13,149 organization keys equal to a country name dropped
distinct     : persons 156,004, orgs 99,432, places 25,988, countries 237
per article  : persons 3.25, orgs 2.29, places 1.94
time order   : 2024-10-01T00:00:00Z .. 2024-10-01T23:45:00Z, out of order 0
stream sha256: c7d692a4709022ec6208b33145210e4dd3ecbfa2868d7c6f672dd94dd1afc005
raw sha256   : a94b0083515b83d1696e50f7270330db4e4a89db82c6a22d0ed029404b72fe1c
run          : 11.1 s, peak RSS 50.2 MB (URL hash table 2.1 MB)
```
<!-- input_manifest:end -->

## Known limitations: outlet self-name removal

Rule (`trendpulse/gkg.py`, `is_self_org`): an organization key is dropped when its first
word(s), with non-alphanumerics removed and joined, equal the outlet name taken from the
domain. The match must always end at a word boundary.

Measured on 2024-10-01 (`python scripts/gkg_build.py --start 2024-10-01 --end 2024-10-01`):
- Old rule ("starts with", word boundary only for outlet names < 4 chars): 1,687 dropped.
  New rule (word boundary always): 1,641 dropped, 573 distinct (domain, org) pairs.
- The 46 matches that are no longer dropped are mostly mid-word false positives,
  e.g. nation.africa → "national assembly" (7), "national treasury" (2); india.com → "indian army" (2);
  trib.com → "tribune content agency" (2).
- Side effect: a few real self-names written with different word splits are now kept,
  e.g. aginfo.net → "ag information network" (5).
- **Kept on purpose:** outlets named after a place or institution still drop that place's
  organizations, e.g. syracuse.com → "syracuse university" (3), miltonkeynes.co.uk →
  "milton keynes city council" (2), yankton.net → "yankton high school" (1),
  china.org.cn → "china telecom" (1). These are a small share (thousandths) of ~387k org mentions
  per day. [미확인] effect on rankings has not been measured.

<!-- engine_2024-10-01_2024-10-01_72h-168h-720h_exact:begin -->
## Engine run 2024-10-01_2024-10-01_72h-168h-720h_exact

- command: `python scripts/gkg_run.py --start 2024-10-01 --end 2024-10-01`
- result: `experiments/results/gkg/engine_2024-10-01_2024-10-01_72h-168h-720h_exact.json`
- git: 43b1966dca5906da7ece872b2668b4917b20a989 (dirty=False)

```
input   : 2024-10-01 .. 2024-10-01, 163,360 articles, 25 snapshots, stream sha256 c7d692a4709022ec…
speed   : engine 20,394 articles/s, end-to-end 11,193 articles/s (read 6.59 s, engine 8.01 s)
memory  : peak RSS 162.0 MB; decay structures ≈ 61.2 MB (key strings 281,424 unique, 17.8 MB)
    72h/exact      keys 281,424 (peak 281,424, deleted 0), ≈ 14.4 MB
   168h/exact      keys 281,424 (peak 281,424, deleted 0), ≈ 14.4 MB
   720h/exact      keys 281,424 (peak 281,424, deleted 0), ≈ 14.4 MB
evidence: 381 tracked keys, 6,121 articles, 11,211 postings, file 1.5 MB (data/gdelt/processed/evidence.sqlite)
          cap 30 postings per key per UTC day, 285,053 postings truncated
--- Top 10 72h/exact (score, bias-corrected rate per hour) at 2024-10-01T23:45:00Z
   1. L:new york, united states                  10327.7  486.5/h
   2. L:washington, washington, united states     7138.2  336.2/h
   3. P:joe biden                                 5374.7  253.2/h
   4. L:georgia, united states                    5218.0  245.8/h
   5. L:texas, united states                      5103.6  240.4/h
   6. L:florida, united states                    4914.6  231.5/h
   7. O:instagram                                 4821.6  227.1/h
   8. P:kamala harris                             4776.9  225.0/h
   9. P:donald trump                              4768.7  224.6/h
  10. L:london, london, city of, united kingdom    4569.9  215.3/h
--- Top 10 168h/exact (score, bias-corrected rate per hour) at 2024-10-01T23:45:00Z
   1. L:new york, united states                  10863.3  480.2/h
   2. L:washington, washington, united states     7533.6  333.0/h
   3. P:joe biden                                 5681.4  251.1/h
   4. L:georgia, united states                    5513.5  243.7/h
   5. L:texas, united states                      5385.6  238.1/h
   6. L:florida, united states                    5173.6  228.7/h
   7. O:instagram                                 5088.7  224.9/h
   8. P:donald trump                              5040.1  222.8/h
   9. P:kamala harris                             5032.6  222.4/h
  10. L:london, london, city of, united kingdom    4836.5  213.8/h
--- Top 10 720h/exact (score, bias-corrected rate per hour) at 2024-10-01T23:45:00Z
   1. L:new york, united states                  11191.2  476.6/h
   2. L:washington, washington, united states     7775.9  331.2/h
   3. P:joe biden                                 5869.8  250.0/h
   4. L:georgia, united states                    5695.0  242.5/h
   5. L:texas, united states                      5558.6  236.7/h
   6. L:florida, united states                    5332.9  227.1/h
   7. O:instagram                                 5251.9  223.7/h
   8. P:donald trump                              5206.7  221.7/h
   9. P:kamala harris                             5189.0  221.0/h
  10. L:london, london, city of, united kingdom    4999.1  212.9/h
--- gaps (missing 15-min slots in each window's recent span)
  72h 0.0% of 96, 168h 0.0% of 96, 720h 0.0% of 96
--- new issues 72h (lift = (r72+1)/(r168+1)), status ok, 1916 keys passed >=20 articles & >=5 outlets; entry events in run: 153
   1. P:erica wolff                            lift 1.05  r72 9.5/h  r168 9.0/h  articles 201  outlets 8
   2. P:kunta kinte                            lift 1.05  r72 26.8/h  r168 25.5/h  articles 579  outlets 8
   3. P:john amos                              lift 1.05  r72 29.4/h  r168 28.0/h  articles 637  outlets 8
   4. P:mary tyler moore                       lift 1.05  r72 25.3/h  r168 24.1/h  articles 547  outlets 8
   5. P:von miller                             lift 1.05  r72 5.7/h  r168 5.4/h  articles 120  outlets 8
   6. P:gordy howard                           lift 1.05  r72 18.6/h  r168 17.7/h  articles 402  outlets 8
   7. P:alex haley                             lift 1.05  r72 20.0/h  r168 19.0/h  articles 433  outlets 8
   8. P:norman lear                            lift 1.05  r72 20.9/h  r168 19.9/h  articles 452  outlets 8
   9. P:hank stram                             lift 1.05  r72 16.6/h  r168 15.8/h  articles 360  outlets 8
  10. P:belinda foster                         lift 1.05  r72 19.6/h  r168 18.7/h  articles 425  outlets 8
--- 168h Top 10 burst/steady (ratio = (r168+1)/(r720+1)), status warming_up, elapsed 0.99 days
   1. L:new york, united states                ratio 1.01  warming_up (판별 준비 중)
   2. L:washington, washington, united states  ratio 1.01  warming_up (판별 준비 중)
   3. P:joe biden                              ratio 1.00  warming_up (판별 준비 중)
   4. L:georgia, united states                 ratio 1.00  warming_up (판별 준비 중)
   5. L:texas, united states                   ratio 1.01  warming_up (판별 준비 중)
   6. L:florida, united states                 ratio 1.01  warming_up (판별 준비 중)
   7. O:instagram                              ratio 1.01  warming_up (판별 준비 중)
   8. P:donald trump                           ratio 1.00  warming_up (판별 준비 중)
   9. P:kamala harris                          ratio 1.01  warming_up (판별 준비 중)
  10. L:london, london, city of, united kingdom ratio 1.00  warming_up (판별 준비 중)
```
<!-- engine_2024-10-01_2024-10-01_72h-168h-720h_exact:end -->

## Known limitations: decay engine and evidence store

- Incremental Top K (`TimeDecayWindow.snapshot`) is exact for K ≤ top_m (2,000): it ranks the
  previous Top M plus keys updated since the last snapshot. Tested against a full sort with and
  without pruning (`tests/test_gkg_engine.py`).
- With a threshold θ, a deleted key restarts at 0, so scores are always under-estimates. θ=None is exact.
- `bias_corrected_rate` is None while elapsed = 0 (first batch). GKG stamps a whole 15-min file
  with one ts, so the formula (which assumes a continuous flow) over-states a steady rate by about
  2^(7.5 min / h) − 1: +0.12 % for h = 72 h, less for 168 h / 720 h (test: constant 16/h within 1 %).
  In the first hours the correction divides by a small number, so rates there are noisy.
- Evidence store: postings start at the snapshot where a key first enters any Top 200 (no backfill,
  decided 2026-11-16: no second pass). Earlier articles about that key have no postings;
  `keys.first_tracked_ts` records where coverage starts.
- Evidence daily cap: at most **30 postings per tracked key per UTC day** (the first to arrive that day).
  On 2024-10-01: 381 tracked keys, 6,121 articles, 11,211 postings kept, **285,053 postings truncated**,
  file 1.5 MB (before the cap: 95,586 articles, 308,728 postings, 24.4 MB). Evidence for broad keys is
  therefore a sample of each day's earliest articles, not of the most relevant ones.
- After one day all three windows give nearly the same Top 10, as expected: with 24 h elapsed the
  corrected rates of every half-life reduce to the average rate of that day.
- GKG artifacts: organization keys equal to a country name ("O:united states") are now dropped
  (13,149 on 2024-10-01, `country_orgs_dropped` in input_manifest.json). "L:washington, washington,
  united states" (type 3 US city, likely Washington D.C. geocoded to Washington state [미확인]) is
  left as is.

## Known limitations: detection (P3)

Spec (as first built; F and warm-ups changed in "설계 변경" below): lift = (r72+α)/(r168+α), α=1,
candidates = 72h Top 2,000, ≥20 articles and ≥5 outlets in 72 h, Top 10 by lift; trend ratio =
(r168+α)/(r720+α), F=2, 14-day warm-up; hold only when a used window has
> 5 % missing 15-min slots. Code: `trendpulse/gkg_engine.py`; tests: `tests/test_gkg_detect.py`.

- **Candidate restriction (approximation):** only keys in the 72 h window's Top 2,000 are scored. A key
  with a high lift but a 72 h score below the 2,000th is never a new issue.
- **Outlet / article counts (approximation):** a key is tracked from the article at which its 72 h score
  reaches 3. Every mention in the last 72 h weighs ≥ 0.5 in that score, so fewer than 6 mentions precede
  tracking: articles and outlets are under-counted by at most 5. Up to 8 outlets are kept per key
  (most recent last-seen time wins), so the outlet count is exact up to 8 for the tracked period and
  capped at 8, enough for the ≥ 5 test. Articles are counted in whole UTC hours (71–72 h of history).
  Keys with no article in the last 72 h are swept at every snapshot and restart from 0.
  On 2024-10-01: 35,461 keys tracked, ≈ 31.5 MB (key/domain strings not counted).
- **Trend labels cannot reach "rising" early (mathematical limit of the original spec F=2; changed,
  see "설계 변경" below):** with bias-corrected rates
  the largest possible ratio (instantaneous burst, α → 0) at elapsed e is
  720·(1−2^(−e/720)) / (168·(1−2^(−e/168))): 1.58 at day 14, 1.84 at day 20, **2.0 only from day 23.7**,
  2.26 at day 30, 3.25 at day 61. So between day 14 and ≈ day 24 no key can be labelled 최근 급증.
  At day 30 a key that appeared from nothing at a constant rate is "rising" only if it started within
  the last ≈ 3 days (2.18 after 1 day, 2.02 after 3, 1.89 after 5, 1.61 after 10).
- **New issues had no warm-up in the original spec (now 7 days, see "설계 변경"):** on day 1 all lifts are ≈ 1.05 and the list ranks what was
  reported late in the day (2024-10-01: the John Amos obituary and its co-mentioned names), with 153
  entry events (151 first, 2 re-entry) in one day. Co-mentioned names of one story fill several slots.
- **Gaps:** a slot counts as present if it had at least one article; windows are clipped at the stream
  start. A held new-issue snapshot records no events and keeps the previous Top K.

## 설계 변경 (결과 확인 전, 수학적 상한 때문)

Decided 2026-11-16, before any multi-day result was looked at; 2024-10-01 was not re-run (both
judgements are still warming up on day 1). The reason is a property of the formulas, not of the data.

With bias-corrected rates (rate = score·ln2/h ÷ (1 − 2^(−e/h)), e = elapsed time since the stream
start), the largest possible ratio of a short-window rate to a long-window rate (all mentions at the last
instant, α → 0) is

    max(r_short / r_long) = h_long·(1 − 2^(−e/h_long)) / (h_short·(1 − 2^(−e/h_short)))

so neither lift (72 h / 168 h) nor the trend ratio (168 h / 720 h) can be large early in the stream.

| elapsed (days) | max lift r72/r168 | max ratio r168/r720 |
|---:|---:|---:|
| 1 | 1.07 | 1.04 |
| 3 | 1.20 | 1.12 |
| 5 | 1.33 | 1.20 |
| 7 | 1.46 | 1.28 |
| 10 | 1.63 | 1.41 |
| 14 | 1.82 | 1.58 |
| 21 | 2.06 | 1.88 |
| 24 | 2.12 | 2.01 |
| 30 | 2.22 | 2.26 |
| 45 | 2.31 | 2.80 |
| 61 | 2.33 | 3.25 |

Command for this table:
`python -c "m=lambda e,s,l:l*(1-2**(-e/l))/(s*(1-2**(-e/s)));[print(d,round(m(24*d,72,168),2),round(m(24*d,168,720),2)) for d in (1,3,5,7,10,14,21,24,30,45,61)]"`

Changes:
1. **Burst / steady: F = 2 → 1.5, warm-up 14 → 21 days.** rising ≥ 1.5, steady 1/1.5 ≤ ratio < 1.5,
   cooling < 1/1.5. With F = 2 nothing could be "rising" before day 23.7. At day 21 the bound is 1.88 > 1.5,
   so every label is reachable once judgement starts.
2. **New issues: 7-day warm-up.** Before day 7 the list is held as "판별 준비 중" and no entry events are
   recorded; the "previous Top K" for entry events starts empty at day 7. The bound at day 7 is 1.46.
   On day 1 every lift was ≈ 1.05 and the list only reflected what was reported late in the day.

Code: `trendpulse/gkg_engine.py` (`trend_factor=1.5`, `trend_warmup_days=21`, `new_warmup_days=7`);
tests: `tests/test_gkg_detect.py`.

Remaining limitation (not changed): names co-mentioned in one story fill several new-issue slots
(2024-10-01: John Amos, Kunta Kinte, Alex Haley, Norman Lear, Mary Tyler Moore from one obituary).

<!-- app_cache_2024-09-15_2024-10-14:begin -->
## Dashboard cache 2024-09-15_2024-10-14

- command: `python scripts/gkg_app_cache.py --start 2024-09-15 --end 2024-10-14`
- result: `experiments/results/gkg/app_cache_2024-09-15_2024-10-14.json`
- git: 1ee22f7174d56cc74c95a88fa9e2ecd4bd4737e6 (dirty=False), fingerprint 4ebcee2e9f7b8be0…

```
range      : 2024-09-15 .. 2024-10-14, 4,024,133 articles, 121 cached 6-hour snapshots, 422 new-issue entry events
build      : 655.2 s (6,141 articles/s), peak RSS 1367.5 MB
decay      : ≈ 800.7 MB (72h 3,182,782 keys, 168h 3,182,782 keys, 720h 3,182,782 keys)
tracker    : 125,391 keys, ≈ 140.2 MB
evidence   : 2,092 keys, 327,414 articles, 526,063 postings (cap 30/day, 7,309,010 truncated), 78.8 MB
dgim       : 2,086 keys, 11,998 buckets
```
<!-- app_cache_2024-09-15_2024-10-14:end -->

## 설계 변경: 30일 탭을 라벨별 3칸으로 (2026-11-17)

Decided after seeing the dashboard's 30-day tab (ordered by 720 h score): its top rows repeated the 7-day tab
and almost every row was "steady" (5 rising and 9 cooling of ≈ 2,960 labelled top-20 rows over all cached
snapshots). This changes only how the dashboard presents labels; the label rule (F = 1.5, 21-day warm-up) is
unchanged.

- Candidates: 168 h window Top 2,000, labelled by ratio = (r168+1)/(r720+1).
- Per key kind (filter applied first): **최근 급증** ratio descending Top 10, **식는 중** ratio ascending Top 10,
  **꾸준** 168 h score order Top 10. Ties by key. Empty while warming up (< 21 days) or held for gaps.
- Code: `trendpulse/gkg_cache.py` (`trend_groups`), `web/static/gkg.js`; tests: `tests/test_gkg_app.py`
  (`TrendGroupsTest`, `TrendTabTest` on a 22-day synthetic stream).

Result (`python scripts/gkg_app_cache.py --start 2024-09-15 --end 2024-10-14`, last snapshot 2024-10-14 23:45 UTC,
kind = all):
- 최근 급증: binyamina (1.79), baba siddique (1.74), alex salmond (1.70), vem miller (1.70), coachella (1.68),
  abu hamza, alba party, kenny macaskill, simon johnson, joshua simmons (1.61).
- 식는 중: ryan wesley routh (0.45), trump international golf club (0.50), david lochridge, jean smart,
  west palm beach (0.57), firass abiad, australian community media, ryan routh, a red ventures company,
  ranil wickremesinghe (0.63).
- 꾸준: new york, florida, washington (state), donald trump, california, london, kamala harris, instagram, texas,
  joe biden.
- Over the 37 cached snapshots with an active judgement (day ≥ 21): 337 rising, 370 cooling, 370 steady rows
  (kind = all), so the rising group is sometimes shorter than 10.
- Visible GKG artifacts (not filtered): place names tagged as persons ("P:los angeles", "P:las vegas") and
  publisher boilerplate tagged as organizations ("O:a red ventures company", "O:australian community media").
  The same event can appear under two name forms ("P:ryan wesley routh", "P:ryan routh").
