"""Real-world events used by the case-study evaluation (E6/E11) and, since v0.4,
shown as markers on the dashboard timeline.

(date, expressions, label, group). The first expression is the v0.2 keyword.
  group "v0.2"    : the 16 events of v0.2; extra expressions were added AFTER seeing
                    the v0.2 results (post-hoc, e.g. "school shooting" for Parkland)
  group "holdout" : 10 events registered on 2026-11-08 before run_sprint3.py was
                    first run (pre-registered, see PROGRESS.md E11)

This list is *data shared by the experiments and the UI*; changing it changes the
E11 numbers, so edit it only together with a re-run of experiments/run_sprint3.py.
"""
from __future__ import annotations

EVENTS: list[tuple[str, list[str], str, str]] = [
    ("2012-10-29", ["sandy", "hurricane sandy"], "허리케인 샌디 상륙", "v0.2"),
    ("2012-12-14", ["newtown", "sandy hook"], "샌디훅 초등학교 총격", "v0.2"),
    ("2013-04-15", ["boston", "boston marathon"], "보스턴 마라톤 폭탄 테러", "v0.2"),
    ("2013-07-13", ["zimmerman", "george zimmerman"], "짐머먼 무죄 평결", "v0.2"),
    ("2014-03-08", ["malaysia", "malaysia airlines"], "말레이시아항공 MH370 실종", "v0.2"),
    ("2014-08-09", ["ferguson", "michael brown"], "퍼거슨 사건", "v0.2"),
    ("2014-09-30", ["ebola"], "미국 첫 에볼라 확진", "v0.2"),
    ("2015-06-17", ["charleston", "charleston shooting"], "찰스턴 교회 총격", "v0.2"),
    ("2016-02-13", ["scalia", "antonin scalia"], "스캘리아 대법관 사망", "v0.2"),
    ("2016-06-12", ["orlando", "orlando shooting"], "올랜도 나이트클럽 총격", "v0.2"),
    ("2016-06-23", ["brexit"], "브렉시트 국민투표", "v0.2"),
    ("2017-08-12", ["charlottesville"], "샬러츠빌 집회 폭력", "v0.2"),
    ("2018-02-14", ["parkland", "school shooting", "florida school"], "파크랜드 고교 총격", "v0.2"),
    ("2020-03-11", ["coronavirus", "coronavirus pandemic"], "WHO 팬데믹 선언", "v0.2"),
    ("2020-05-25", ["floyd", "george floyd"], "조지 플로이드 사망", "v0.2"),
    ("2022-02-24", ["ukraine", "russia ukraine"], "러시아의 우크라이나 침공", "v0.2"),
    ("2015-11-13", ["paris", "paris attacks"], "파리 테러", "holdout"),
    ("2017-08-25", ["harvey", "hurricane harvey"], "허리케인 하비", "holdout"),
    ("2017-09-20", ["maria", "puerto rico"], "허리케인 마리아", "holdout"),
    ("2017-10-05", ["weinstein", "harvey weinstein"], "와인스타인 보도", "holdout"),
    ("2018-09-16", ["kavanaugh", "brett kavanaugh"], "캐버노 의혹 보도", "holdout"),
    ("2019-04-15", ["notre", "notre dame"], "노트르담 화재", "holdout"),
    ("2019-09-24", ["impeachment", "impeachment inquiry"], "트럼프 탄핵 조사 착수", "holdout"),
    ("2020-01-26", ["kobe", "kobe bryant"], "코비 브라이언트 사망", "holdout"),
    ("2021-01-06", ["capitol", "capitol riot"], "미국 의사당 난입", "holdout"),
    ("2022-06-24", ["roe", "abortion rights"], "로 대 웨이드 판결 폐기", "holdout"),
]


def events_as_dicts() -> list[dict]:
    """JSON-friendly view sorted by date (the dashboard timeline uses this)."""
    rows = [{"date": d, "exprs": list(e), "label": l, "group": g} for d, e, l, g in EVENTS]
    rows.sort(key=lambda r: r["date"])
    return rows
