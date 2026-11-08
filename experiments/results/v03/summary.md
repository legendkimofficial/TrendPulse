# 스프린트 3 (v0.3) 실험 결과

- 데이터: 209,480건 · 실행: Python 3.14.4 / Windows-11-10.0.26200-SP0 / CPU 6개 · 2026-11-08 22:15 · 소요 192.5초
- 공통: θ=0.5, K=20, 기사 수 감쇠 c=0.001(반감기 693건) / 날짜 감쇠 반감기 7.0일, 급상승은 반감기 1/7·최소 점수 3·Top 10, 구 흡수 비율 0.8

## E7. 두 단어 묶기 (구) — 기사 수 감쇠

| 설정 | 기사당 토큰 | 저장 키워드 (근사 / 정확) | 감소율 | Python 객체 (근사 / 정확) | 감소율 | Top20 겹침 / 최저 | 순서 동일 | 입력 갱신 (기사/s, 중앙값) |
|---|---|---|---|---|---|---|---|---|
| 단어만 (v0.2) | 5.47 | 2,854 / 40,252 | 92.9% | 0.66 / 4.24 MB | 84.4% | 99.91% / 95% | 89.0% | 210,919 (210,475–212,486) |
| 단어 + 구 (v0.3) | 7.96 | 4,507 / 198,463 | 97.7% | 1.03 / 19.46 MB | 94.7% | 99.89% / 95% | 90.1% | 129,191 (114,148–129,690) |

구를 넣으면 정확 기준이 기억해야 할 키워드가 크게 늘어나므로(마지막 시점 363,665개) 제거의 효과가 더 커진다. 대신 근사 쪽 저장량과 갱신 비용도 늘어난다.

### 같은 시점의 Top 20 비교

- **2013-04-17** 단어만: wedding, boston, home, divorce, spring, stress, study, marathon, love, baby, marriage, food, children, house, style, health, u.s., ideas, april, recipes
  - 단어 + 구: wedding, boston, home, divorce, spring, stress, study, love, baby, marriage, boston marathon, food, children, house, style, health, u.s., ideas, april, recipes
- **2016-02-16** 단어만: clinton, trump, sanders, bernie, obama, hillary, donald, super, court, supreme, scalia, hampshire, gop, bowl, debate, rubio, grammys, iowa, justice, cruz
  - 단어 + 구: clinton, trump, bernie sanders, obama, hillary clinton, donald trump, supreme court, scalia, new hampshire, gop, debate, super bowl, rubio, grammys, iowa, justice, cruz, love, black, marco rubio
- **2020-05-29** 단어만: trump, coronavirus, u.s., donald, dead, house, covid, home, china, face, pandemic, death, white, california, impeachment, gop, fox, twitter, tweets, police
  - 단어 + 구: trump, coronavirus, u.s., dead, donald trump, house, covid, home, china, face, pandemic, death, white, california, impeachment, gop, fox, twitter, white house, tweets

## E11. 사례 평가 (Top 20 진입, 사건일~7일 = 성공)

| 설정 | v0.2 사건 16개 | 사전 등록 사건 10개 | 합계 |
|---|---|---|---|
| 단어 · 기사 수 감쇠 (v0.2) | 13/16 | 3/10 | 16/26 |
| 단어+구 · 기사 수 감쇠 | 13/16 | 3/10 | 16/26 |
| 단어+구 · 날짜 감쇠 7일 (v0.3 기본) | 14/16 | 9/10 | 23/26 |

- v0.2 사건의 추가 표현(예: parkland → school shooting)은 **v0.2 결과를 본 뒤** 정한 것이라 사후 분석이다. 공정한 비교는 사전 등록 사건 10개 열이다.
- 사건 키워드는 사건별 표현 목록 중 **하나라도** Top 20에 들어오면 진입으로 본다.

| 사건 | 그룹 | 표현 | 단어·기사 | 구·기사 | 구·날짜 | 급상승(구·기사) | 최고 순위(구·기사) |
|---|---|---|---|---|---|---|---|
| 허리케인 샌디 상륙 (2012-10-29) | v0.2 | sandy, hurricane sandy | 사건 전부터 | 사건 전부터 | 사건 전부터 | 사건 전부터 | 1 |
| 샌디훅 초등학교 총격 (2012-12-14) | v0.2 | newtown, sandy hook | +5일 `newtown` | +5일 `newtown` | +4일 `newtown` | +1일 | 16 |
| 보스턴 마라톤 폭탄 테러 (2013-04-15) | v0.2 | boston, boston marathon | +1일 `boston` | +1일 `boston` | +1일 `boston` | +1일 | 1 |
| 짐머먼 무죄 평결 (2013-07-13) | v0.2 | zimmerman, george zimmerman | +1일 `zimmerman` | +1일 `zimmerman` | +1일 `zimmerman` | +1일 | 9 |
| 말레이시아항공 MH370 실종 (2014-03-08) | v0.2 | malaysia, malaysia airlines | - | - | - | - | - |
| 퍼거슨 사건 (2014-08-09) | v0.2 | ferguson, michael brown | +7일 `ferguson` | +7일 `ferguson` | +7일 `ferguson` | +6일 | 1 |
| 미국 첫 에볼라 확진 (2014-09-30) | v0.2 | ebola | 사건 전부터 | 사건 전부터 | 사건 전부터 | +1일 | 1 |
| 찰스턴 교회 총격 (2015-06-17) | v0.2 | charleston, charleston shooting | +1일 `charleston` | +1일 `charleston` | +1일 `charleston` | +1일 | 1 |
| 스캘리아 대법관 사망 (2016-02-13) | v0.2 | scalia, antonin scalia | +2일 `scalia` | +1일 `scalia` | +1일 `scalia` | +0일 | 7 |
| 올랜도 나이트클럽 총격 (2016-06-12) | v0.2 | orlando, orlando shooting | +0일 `orlando` | +0일 `orlando` | +0일 `orlando` | +0일 | 2 |
| 브렉시트 국민투표 (2016-06-23) | v0.2 | brexit | +1일 `brexit` | +1일 `brexit` | +1일 `brexit` | +0일 | 3 |
| 샬러츠빌 집회 폭력 (2017-08-12) | v0.2 | charlottesville | +1일 `charlottesville` | +1일 `charlottesville` | +1일 `charlottesville` | +0일 | 2 |
| 파크랜드 고교 총격 (2018-02-14) | v0.2 | parkland, school shooting, florida school | - | - | - | +1일 | - |
| WHO 팬데믹 선언 (2020-03-11) | v0.2 | coronavirus, coronavirus pandemic | 사건 전부터 | 사건 전부터 | 사건 전부터 | 사건 전부터 | 2 |
| 조지 플로이드 사망 (2020-05-25) | v0.2 | floyd, george floyd | - | - | +6일 `george floyd` | +8일 | - |
| 러시아의 우크라이나 침공 (2022-02-24) | v0.2 | ukraine, russia ukraine | +1일 `ukraine` | +1일 `ukraine` | 사건 전부터 | 사건 전부터 | 6 |
| 파리 테러 (2015-11-13) | 사전 등록 | paris, paris attacks | +1일 `paris` | +1일 `paris` | +1일 `paris` | +1일 | 1 |
| 허리케인 하비 (2017-08-25) | 사전 등록 | harvey, hurricane harvey | +2일 `harvey` | +2일 `harvey` | +1일 `harvey` | +0일 | 2 |
| 허리케인 마리아 (2017-09-20) | 사전 등록 | maria, puerto rico | - | +8일 `puerto rico` | +8일 `puerto rico` | +6일 | 4 |
| 와인스타인 보도 (2017-10-05) | 사전 등록 | weinstein, harvey weinstein | +3일 `weinstein` | +3일 `weinstein` | +2일 `weinstein` | +0일 | 2 |
| 캐버노 의혹 보도 (2018-09-16) | 사전 등록 | kavanaugh, brett kavanaugh | +10일 `kavanaugh` | +10일 `kavanaugh` | 사건 전부터 | +3일 | 4 |
| 노트르담 화재 (2019-04-15) | 사전 등록 | notre, notre dame | - | - | +0일 `notre dame` | +1일 | - |
| 트럼프 탄핵 조사 착수 (2019-09-24) | 사전 등록 | impeachment, impeachment inquiry | - | - | +2일 `impeachment` | +6일 | - |
| 코비 브라이언트 사망 (2020-01-26) | 사전 등록 | kobe, kobe bryant | - | - | +1일 `kobe` | +2일 | - |
| 미국 의사당 난입 (2021-01-06) | 사전 등록 | capitol, capitol riot | +10일 `capitol` | +10일 `capitol` | +0일 `capitol` | +2일 | 19 |
| 로 대 웨이드 판결 폐기 (2022-06-24) | 사전 등록 | roe, abortion rights | - | - | +0일 `roe` | +1일 | - |

## E8. 날짜 기준 감쇠 vs 기사 수 기준 감쇠 (단어 + 구)

| 연도 | 기사 수 감쇠: 최근 7일 기사 수 중앙값 | 근거 얇음(≤2건) 비율 | 날짜 감쇠: 최근 7일 기사 수 중앙값 | 근거 얇음 비율 |
|---|---|---|---|---|
| 2012 | 10.1 | 2.1% | 10.1 | 1.9% |
| 2013 | 9.6 | 2.2% | 9.6 | 2.1% |
| 2014 | 7.6 | 4.9% | 7.7 | 4.1% |
| 2015 | 8.8 | 3.0% | 8.9 | 2.3% |
| 2016 | 12.7 | 2.1% | 12.8 | 1.6% |
| 2017 | 12.3 | 2.8% | 12.5 | 2.1% |
| 2018 | 3.9 | 55.0% | 5.1 | 45.5% |
| 2019 | 0.6 | 88.3% | 2.0 | 74.0% |
| 2020 | 0.7 | 81.4% | 2.0 | 73.5% |
| 2021 | 0.8 | 85.5% | 2.0 | 74.5% |
| 2022 | 0.7 | 88.3% | 2.0 | 78.2% |

하루 끝마다 Top 20 키워드 각각이 최근 7일 동안 몇 건의 제목에 나왔는지 센 값. 기사 수 감쇠는 희소 구간에서 몇 달 전 기사가 순위를 지탱하고(최근 근거가 얇음), 날짜 감쇠는 최근 기사만 반영하지만 기사가 적을 때 1~2건짜리 키워드가 순위에 오른다.

## E9. 급상승 탐지 (단어 + 구, 기사 수 감쇠)

- 급상승 Top 10에 사건일~7일 안에 진입: **24/26** (Top 20 진입은 16/26)
- 급상승이 Top 20보다 이른 날 잡은 사건 17개, 같은 날 7개, 급상승에서만 잡힌 사건 8개
- 급상승 목록 중 같은 시점 Top 20에 없는 키워드 비율(주 1회 측정 평균): 48.2% → Top 20과 다른 정보를 보여 준다
- 날짜 감쇠(v0.3 기본)에서는 급상승 Top 10 진입 22/26. 반감기 1일짜리 짧은 창이 희소 구간에서 최소 점수(3)를 넘기 어려워 목록이 비는 날이 있다

## E10. 순위 불확실성 표시 (단어만, 기사 수 감쇠 — 원점수 기준 보장)

| 항목 | 값 |
|---|---|
| 평가 스냅샷 / Top 20 인접 쌍 | 1,995 / 37,905 |
| 실제로 순서가 바뀐 쌍 (정확 기준과 비교) | 197 (0.52%) |
| **표시하지 않았는데 바뀐 쌍 (보장 위반)** | **0** |
| 표시 비율: 전체 상한 θ/(1-θ) / 키워드별 상한 | 66.6% / 2.38% |
| 표시한 쌍 중 실제로 바뀐 비율 (키워드별) | 21.8% |
| 마지막 순위가 Top 20 밖으로 밀린 시점 / 그중 표시 안 한 것 | 37 / **0** |
| 마지막 순위 표시 비율: 전체 상한 / 키워드별 | 96.1% / 9.4% |
| 키워드별 상한을 위한 추가 메모리 (Python 객체) | 0.66 → 0.86 MB (+29.6%) |

키워드별 상한: 키워드가 (다시) 들어온 뒤로 잃은 점수의 상한이 θ/(1-θ)·(1-c)^경과 로 줄어든다. 오래 머문 키워드는 사실상 정확하다. 보장은 원점수 순위에 대한 것이며, 구 흡수로 숨겨지는 단어가 정확 계산과 달라지는 경우는 포함하지 않는다.

