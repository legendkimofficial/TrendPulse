# GDELT GAL 제출 버전 — 재현 문서

이 문서는 `codex/gdelt-submission` 브랜치의 제출 버전을 처음 받은 환경에서
그대로 재현하는 절차와 판정 근거를 담는다. 모든 명령은 저장소 루트에서 실행한다.

## 0. 구성 요소와 실행 명령 요약

| 단계 | 명령 | 결과물 |
|---|---|---|
| 의존성 | `pip install -r requirements.txt` | flask, matplotlib |
| 원본 수집 | `python scripts/fetch_gdelt_gal.py collect --start 2026-11-09 --end 2026-11-15` | `data/raw/gdelt_gal/*.gal.json.gz` + `_manifest_raw.json` |
| 정규화(고정 입력) | `python scripts/fetch_gdelt_gal.py normalize` | `data/processed/gdelt/articles.jsonl`, `manifest.json` |
| 재생 계약 검증 | `python scripts/fetch_gdelt_gal.py verify` | 정규화·재생 결정성 통과 여부 |
| 전체 테스트 | `python -m unittest discover -s tests` | 기존 67 + GAL 25 = 92개 |
| 실험 | `python experiments/run_gal_experiments.py --thetas 0.05 0.1 0.3 --every 4` | `experiments/results/gdelt/{results.json,summary.md}` |
| 대시보드 | `python app_gdelt.py` → http://127.0.0.1:5001 | 7일 순위·72h 급상승·상세·Solar |
| 종단 시연 | `python experiments/demo_solar.py --t 2026-11-15T12:00:00Z` | `experiments/results/gdelt/solar_demo.{json,md}` |
| 주기 갱신(스트레치) | `python scripts/refresh_gal.py --days 7`, `--check` | `refresh_state.json`(마지막 성공 시각) |

`data/raw/`, `data/processed/`는 커밋에서 제외된다(`.gitignore`). 대용량 원본 없이
재현할 때는 위 수집 명령으로 동일한 배치를 다시 받으면 된다(각 파일 SHA-256이
`manifest.json`에 기록되어 있어 동일성을 확인할 수 있다).

## 1. 데이터: GDELT GAL 원본과 재생 계약

- **출처**: GDELT 3.0 Global Article List(GAL)
  `https://data.gdeltproject.org/gdeltv3/gal/YYYYMMDDHHMMSS.gal.json.gz`
  (gzip JSON-L; 레코드 필드 `date, url, domain, outletName, outletLogo,
  outletTwitter, title, image, desc, lang, author`를 그대로 사용 — 별도 보강 없음).
- **배치 관측 시각**: 파일명 타임스탬프(UTC). 15분 슬롯 직후 분(+01/+16/+31/+46,
  때때로 +02/+17/...)에 발행되며, 관측 시각이 재생 시계다. GAL의 `date` 필드는
  발행 시각 *추정치*(~30%만 정확)라 `adate`로 별도 보존하고 순위에 쓰지 않는다.
- **수집 기간**: 수집 시작일(2026-11-16 UTC) 직전 7개 완료일
  **2026-11-09T00:00Z ~ 2026-11-16T00:00Z**. 예상 배치 탐색 1,344개(672슬롯 ×
  클러스터 2분), **성공 1,276 / 부재(404) 68 / 실패 0**, 원본 합계 836,982,180바이트,
  레코드 2,930,537건. 빈 15분 슬롯은 0개 — 공백은 3개의 고립된 15분 슬롯
  (11-09 10:30, 11-09 11:00, 11-11 13:00 UTC)뿐이며 `manifest.json`의
  `collection_gaps`에 기록된다(공백 구간은 급상승 판정 보류 사유로 쓰인다).
- **정규화 규칙**:
  - 영어(`lang=="en"`)만 분석. 제외량: 비영어 2,049,597 / 제목 없음 1,220 /
    URL 없음·비HTTP 6 / JSON 파싱 실패 0 → **분석 기사 789,845건**.
  - 중복 제거: 캐노니컬 URL(스킴·호스트 소문자화, 기본 포트·프래그먼트 제거,
    추적 파라미터 `utm_*`, `itm_*`, `fbclid`, `gclid`, `ref`, `ref_src`, `ref_url`,
    `cmpid`, `cmp`, `smid`, `mc_cid`, `mc_eid`, `ns_campaign`, `ns_mchannel`, `_ga`,
    `_gl`, `usqp`, `sessionid` 제거; 경로와 기사 식별 쿼리는 보존) 기준
    **가장 이른 관측 레코드만 유지, 89,869건 중복 제거**. 원본 URL은 별도 보존.
  - 매체: 원래 `domain` 값과 표준화 도메인(www 제거)을 구분해 저장.
  - 발행국: **매핑 v1**(ccTLD 규칙 + `mappings/domain_countries_v1.tsv` 오버라이드,
    UK→GB 별칭 포함). 미매핑 도메인은 `unknown`으로 남기고 집계
    (unknown 619,281건, 매핑된 100+개국 — GB 85,511 / AU 33,461 / CA 25,166 /
    IN 23,985 / US 16,385 / …). 발행국은 **보도 대상 국가가 아니라 매체 등록 기준**임을
    웹에 표시한다.
  - 정렬: `(observed_at_utc, canonical_url)` 오름차순 → 기사 ID(`i`)는 이 순서.
- **결정성**: `verify`는 (1) 정규화를 두 번 실행해 `articles.jsonl` SHA-256이
  동일한지, (2) 같은 파일로 엔진을 두 번 재생해 기사 순서와 스냅샷 해시
  (`GalEngine.snapshot_digest()`)가 동일한지 확인한다.
- **고정 입력**: `articles.jsonl` SHA-256은 `manifest.json`의 `input_sha256`
  (실험·시연 결과에도 기록됨).

## 2. 키워드 추출 규칙(고정)과 순위 정의

- 추출: `trendpulse.text.tokenize`(소문자, 길이 ≥ 3, 불용어 제외, 숫자 포함 토큰
  제외, 화이트리스트 약어 US/UK/EU/UN/AI 유지) **+ GAL 확장 불용어**
  (`trendpulse.gal.GAL_STOPWORDS`: `com, www, http, https, daily, update, updates,
  latest, breaking, full, video, watch, photos, photo, gallery` — 제목에 섞인
  도메인 조각·일반 관형어 제거). 기사 하나에 같은 키워드가 여러 번 나와도
  **1회만 집계**(문서 빈도).
- 7일 순위(기준 시각 `t`): 기사 범위 `(t-7일, t]`(정확히 t-7일은 만료, t는 포함),
  기여 `2^(-(t-observed_at)/48시간)`, 키워드별 합산. 반감기 48h는 설정으로 변경 가능
  (`GalConfig.half_life_h`). 순위 동점은 점수 내림차순, 키워드 오름차순(고정).
  새 기사가 없어도 기준 시각이 전진하면 감쇠와 7일 만료가 반영된다
  (`advance_clock`).
- 72시간 급상승: 최근 `(t-72h, t]`와 직전 `(t-144h, t-72h]`의
  `키워드 포함 기사 수 / 구간 전체 분석 기사 수` 차이를 pp로 표시. 임계값(설정
  공개, 기본값): 구간별 최소 전체 기사 수 200, 최근 구간 최소 키워드 기사 수 5,
  최소 매체 수 3. 관측 범위(144시간) 미확보·수집 공백·분모 부족·키워드 지지 부족
  시 판정 보류하고 사유를 API·웹에 표시. 직전 구간 언급 0건도 분모·범위가 충분하면
  계산한다.
- 진입 표시: 직전 스냅샷 Top K에 없던 키워드가 현재 Top K에 들어오면 신규 진입.
  이전에 Top K 경험이 없으면 `first`, 있으면 `re-entry`. 재생의 첫 스냅샷은
  `initial`로 구분.

## 3. 감쇠 윈도우와 DGIM (실험)

- 비교 조건: 동일 입력(`articles.jsonl`), 반감기 48h, 기간 7일, 추출 규칙, Top K=20,
  스냅샷 시각(모든 관측 배치 시각, ~15분 간격). 기준 방식은 7일 창의 모든 기여분
  보존(창 밖 만료 제외), 실험 방식은 스냅샷마다 점수 < θ인 키워드의 상태 전체를
  제거(θ ∈ {0.05, 0.1, 0.3}). 제거된 키워드의 과거 기여는 폐기되며 재등장 시 1부터
  재시작 — 재등장 이후 과거 기여를 다시 차감하지 않는다(근사 ≤ 기준 보장,
  `tests/test_gal.py::PruningTest`).
- 측정: Top K 일치율(구성원·순서), 절대/상대 점수 오차(두 변형 Top-K 합집합 기준,
  제거 테이블에 없으면 0점; 기준 0 & 근사 > 0은 위반으로 별도 집계), 진입 이벤트
  유형별 재현율·정밀도·시점차(±60분 일대일 대응, 스냅샷 해상도 명시), 집계 상태
  메모리(`deep_sizeof`; 기사 저장·관련 기사 인덱스는 별도 항목).
- DGIM: 추적 키워드(최종 기준 Top-10 + 언급 수 최대/중위/최소 중 100회 이상)를
  재생 시작(i=0)부터 같은 기사 인덱스로 DGIM과 정확 슬라이딩 큐
  (`sliding.ExactWindowCount`)에 반영, N ∈ {200, 1000, 5000} × r ∈ {2, 3}의
  절대·상대 오차, 0구간 오탐, 버킷 수, 논리 비트(`memory_bits()` 모델)와
  실제 파이썬 객체 바이트(`deep_sizeof`)를 구분해 기록.
- 결과: `experiments/results/gdelt/summary.md`(사람용)·`results.json`(기계용).
  기존 HuffPost 실험(`experiments/results/{summary.md, v03/summary.md}`)은 과거
  기록으로, 이번 실행과 무관하며 재실행하지 않았다(로컬에 원본 데이터 없음).

## 4. 웹·Solar

- `app_gdelt.py`는 기존 `app.py`와 같은 라우트/API 구조(`/api/state`, `/api/step`,
  `/api/keyword/<w>`, `/api/track`, `/api/ask`, `/api/info`)를 GAL 엔진으로 재사용한다.
  순위·상세·Solar가 항상 같은 기사 위치(`as_of`)를 기준으로 조회하며, 재생 위치
  이후 기사는 노출되지 않는다(테스트 `GalAppTest`).
- Solar(`trendpulse/gal_agent.py`): 통계·관련 기사 도구 3종을 반드시 호출하게
  강제하고(`tool_choice=required`), 답변의 인용 `[#id]`를 도구 결과와 대조 검증한다.
  `UPSTAGE_API_KEY`가 있으면 실호출, 없으면 같은 도구를 쓰는 오프라인 템플릿으로
  응답하고 웹·결과 파일에 모드를 구분해 표시한다. 키는 명령 출력·결과 파일·커밋에
  포함되지 않는다(환경변수만 사용).

## 5. 판정 (증거 경로)

최종 실행(2026-11-16, 로컬 브랜치 커밋 시점) 기준 판정.

| 항목 | 판정 | 증거 |
|---|---|---|
| GAL 수집·품질 | **통과** | `manifest.json`: 1,276/1,344 파일 성공(404 68, 실패 0), 영어 789,845건, 중복 89,869 제거, 비영어 2,049,597·제목없음 1,220 제외 집계, 공백 3슬롯 기록, 발행국 100+개국 확인(GB 85,511/AU 33,461/CA 25,166/IN 23,985/US 16,385…) |
| 결정적 재생 | **통과** | `fetch_gdelt_gal.py verify`: 정규화 두 번 SHA-256 동일, 재생 두 번 스냅샷 해시 동일(최종 코드 기준 `f9e644b6…`) |
| 7일 순위 | **통과** | `tests/test_gal.py` 25개(경계 t-7d 제외/t 포함·동시각·제목 내 중복 1회·동점 순서·시계 전진 감쇠·직접 합산 일치). 실측 Top: u.s./trump/ai… 매체 수백~수천 |
| 72시간 급상승 | **통과** | `SurgeTest`: pp 계산·직전 0건 계산·보류 4사유(관측범위/공백/분모/키워드 지지). 종단 시연 시점은 수집 공백 직전 구간 겹침으로 보류 사유 표시(의도된 동작) |
| 감쇠 비교 | **통과** | `experiments/results/gdelt/summary.md` E1: θ∈{0.05,0.1,0.3} 모두 Top20 겹침 1.0000·순서 동일 100%·오차 0·근사>기준 0건. 집계 상태 54.5MB(θ=0.3 51.8MB, -5.0%) — 이 척도에선 제거 이득 미미(실측 결론) |
| DGIM 비교 | **통과** | 같은 파일 E2: N∈{200,1000,5000}×r∈{2,3} 전 구간 상대오차 상한 이내(최대 44.1% < 50%), 0구간 오탐 0, 정확 큐 불일치 0, 논리 bit 133 vs 962(N=5000, 14%), Python 객체 1,021 vs 3,087 B/키워드 |
| 웹 연동 | **통과** | `GalAppTest`(같은 기준 시각·미래 기사 미노출·오프라인 ask 도구 호출) + `python app_gdelt.py` 기동 확인 |
| 실제 Solar | **미실행** | `UPSTAGE_API_KEY` 없음. `solar_demo.md`: 오프라인 경로 6/6 검증 통과(도구 호출·동일 키워드/기준시각·인용 4건 유효·수치 일치). 실호출 조건: 키 설정 후 `python experiments/demo_solar.py` |
| 새 환경 재현 | **통과** | 이 문서 §0 절차(수집→normalize→verify→테스트→실험→앱→시연)를 같은 방식으로 로컬에서 전부 수행. 원본은 재수집으로 동일화 가능(파일별 SHA-256 기록) |
| 주기 갱신 | **통과** | `scripts/refresh_gal.py --check`: 같은 기간 재수집·재정규화 후 입력 SHA-256 불변(멱등, URL 중복 집계 없음) — 실행 출력 참조 |

실험 소요: E1+E2 3,551초(4개 엔진 동시 재생 포함). 커밋에서 제외: `data/raw/`(802MB),
`data/processed/`(1.3GB pickle 포함), 키 없음(환경변수만 사용, 출력·커밋에 미포함 확인).

## 6. 한계와 결정 사항

- 수집 기간 7일은 과제 기준(직전 7개 완료일). 11-14(토)·11-15(일)은 기사 수가
  평일 대비 적다(주말 효과, `by_day` 참조).
- 발행국 미매핑 `unknown`이 78%다. ccTLD 규칙 + 큐레이션 오버라이드(v1)로
  100+개국을 확인했지만 .com 매체 다수가 unknown으로 남는다. 매핑 확장은
  버전을 올려 반영할 수 있다.
- GAL `date`(발행 추정)은 순위에 쓰지 않는다. 관측 시각 기반이므로 관측 지연이
  짧은 실제 화제 반영에 유리하지만, 발행 시각과는 최대 수 시간 차이날 수 있다.
- 실험 스냅샷은 실제 관측 배치 간격(대부분 15분, 일부 슬롯 2파일)을 그대로 쓴다.
  지연 해상도는 이 간격보다 작은 차이를 구분하지 못한다.
