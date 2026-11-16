# Trend Pulse - gdelt-gkg 브랜치 규칙

## 목표
키워드 목록 없이 GDELT GKG 2.1 영어 기사 흐름에서 7일 주요 이슈, 72시간 신규 이슈, 30일 반짝/꾸준을
감쇠 윈도우(교재 §4.7)로 찾고, 낮은 점수 삭제(θ)가 메모리·순위·신규 포착에 주는 영향을 측정한다.

## 데이터 규모 (2024-10-01 하루 측정값)
- 하루 약 163,000건, 기간 2024-09-15 ~ 2024-11-14 (61일) 전체 약 1,000만 건.
- 하루 고유 인물 약 156,000, 기관 약 100,000, 장소 약 26,000. 기사당 인물 3.3, 기관 2.4, 장소 3.1, 주제 24.
- 장소 언급의 37%가 나라(type 1). 제목 있는 기사 99.8%.
- 따라서 **기사 전체를 메모리에 올리지 않는다.** 날짜 파일을 순서대로 흘려 읽고, 필요한 요약만 메모리에 둔다.

## 지켜야 할 것
- 기존 HuffPost 코드(app.py, trendpulse/engine.py, data.py, text.py 등)와 기존 테스트 67개는 수정하지 않는다.
  GKG용 기능은 새 파일로 추가한다. 공용 모듈(decaying.py, dgim.py, sliding.py, memory.py)은 읽어서 재사용만 한다.
- 표준 라이브러리(sqlite3 포함) + requirements.txt(flask, matplotlib)만 쓴다. 다른 패키지가 필요하면 먼저 묻는다.
- data/ 아래 원본은 읽기만 한다(캐시·DB는 data/gdelt/processed/ 에 만든다). API 키는 환경변수로만 읽는다.
- 무작위성은 시드를 고정하고 결과 파일에 기록한다.
- 모든 수치는 experiments/results/gkg/ 아래 JSON과 summary.md에 남기고, 만든 명령과 입력 해시를 함께 적는다.
- experiments/gkg_events.json(사건 목록)은 실험 전에 확정된 파일이다. 결과를 보고 고치지 않는다.
- 확인하지 못한 것은 [미확인]으로 표시한다. 추측하지 말고 묻는다.
- 단계마다: 하루 치 데이터(2024-10-01)로 먼저 확인 → 테스트 추가 → `python -m unittest discover -s tests`
  전체 통과 → 바뀐 점 요약 → 커밋.

## 데이터 계약 (scripts/gdelt_download.py가 만든 파일)
data/gdelt/slim/YYYY-MM-DD/HHMM.jsonl.gz, 한 줄에 기사 하나:
{"id","ts":"YYYYMMDDHHMMSS"(UTC),"domain","url","title","persons":[...],"orgs":[...],
 "places":[[name,type,country],...],"themes":[...],"tone":float|null}
places type: 1 나라, 2 미국 주, 3 미국 도시, 4 세계 도시, 5 세계 지역.
같은 폴더의 HHMM.missing 은 서버에 없던 15분 파일. data/gdelt/download_log.csv 에 파일별 상태가 있다.
