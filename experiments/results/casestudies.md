# 사례 평가: 실제 사건을 화제 키워드로 발견했는가

- 사전에 정한 사건 16개, 사건마다 키워드 1개. 관찰 기간: 사건일 -7일 ~ +14일.
- 자동 판정 기준: 사건일부터 7일 안에 근사(c=0.001, θ=0.5) Top 20에 진입하면 성공.
- 진입 시점은 **기사 한 건 단위**로 비교했다(관찰 기간의 매 기사마다 두 방식의 Top 20 확인).
- `팀 평가` 열은 사람이 채운다: 키워드가 사건을 이해할 수 있는 표현인가(표현), 대표 기사가 사건을 뒷받침하는가(근거). 1~3점.

| 사건 | 키워드 | 첫 등장 | 정확 진입 | 근사 진입 | 근사−정확 (기사) | 하루 기사 수 | 자동 판정 | 팀 평가: 표현 | 팀 평가: 근거 |
|---|---|---|---|---|---|---|---|---|---|
| 허리케인 샌디 상륙 (2012-10-29) | sandy | +0일 | +0일 | +0일 | 0 | 91.4 | 성공 (사건 전부터 Top 20) | | |
| 샌디훅 초등학교 총격 (2012-12-14) | newtown | +0일 | +5일 | +5일 | 0 | 92.1 | 성공 | | |
| 보스턴 마라톤 폭탄 테러 (2013-04-15) | boston | +0일 | +1일 | +1일 | 0 | 97.1 | 성공 | | |
| 짐머먼 무죄 평결 (2013-07-13) | zimmerman | +0일 | +1일 | +1일 | 0 | 93.9 | 성공 | | |
| 말레이시아항공 MH370 실종 (2014-03-08) | malaysia | +5일 | - | - | - | 91.4 | 실패 | | |
| 퍼거슨 사건 (2014-08-09) | ferguson | +5일 | +7일 | +7일 | 0 | 89.3 | 성공 | | |
| 미국 첫 에볼라 확진 (2014-09-30) | ebola | +0일 | +0일 | +0일 | 0 | 86.2 | 성공 (사건 전부터 Top 20) | | |
| 찰스턴 교회 총격 (2015-06-17) | charleston | +1일 | +1일 | +1일 | 0 | 89.1 | 성공 | | |
| 스캘리아 대법관 사망 (2016-02-13) | scalia | +0일 | +2일 | +2일 | 0 | 88.1 | 성공 | | |
| 올랜도 나이트클럽 총격 (2016-06-12) | orlando | +0일 | +0일 | +0일 | 0 | 89.0 | 성공 | | |
| 브렉시트 국민투표 (2016-06-23) | brexit | +0일 | +1일 | +1일 | 0 | 90.0 | 성공 | | |
| 샬러츠빌 집회 폭력 (2017-08-12) | charlottesville | +0일 | +1일 | +1일 | 0 | 80.7 | 성공 | | |
| 파크랜드 고교 총격 (2018-02-14) | parkland | +3일 | - | - | - | 61.0 | 실패 | | |
| WHO 팬데믹 선언 (2020-03-11) | coronavirus | +0일 | +0일 | +0일 | 0 | 5.2 | 성공 (사건 전부터 Top 20) | | |
| 조지 플로이드 사망 (2020-05-25) | floyd | +4일 | - | - | - | 5.9 | 실패 | | |
| 러시아의 우크라이나 침공 (2022-02-24) | ukraine | +0일 | +1일 | +1일 | 2 | 5.8 | 성공 | | |

## 근사 Top 20 진입 시점의 대표 기사와 함께 등장한 단어

### 허리케인 샌디 상륙 — `sandy`
- 함께 등장: hurricane, storm, u.s., path, power
- [#25486] 2012-10-28 · Escaping Sandy: Recommended Beach Vacations For Storm Avoiders (PHOTOS)
- [#25474] 2012-10-28 · Hurricane Sandy Meets "Grease" (PICTURE)
- [#25459] 2012-10-28 · Hurricane Sandy Pet Emergency Plan

### 샌디훅 초등학교 총격 — `newtown`
- 함께 등장: shooting, children, talking, shootings, gun
- [#30232] 2012-12-19 · The Names Of Newtown
- [#30228] 2012-12-19 · Natalie Barden, 10-Year-Old Sister Of Newtown Shooting Victim, Writes Letter To President Obama About Gun Control (VIDEO)
- [#30107] 2012-12-18 · Field Notes From Newtown: Reflections on What Constitutes Good Care in a Time of Crisis

### 보스턴 마라톤 폭탄 테러 — `boston`
- 함께 등장: marathon, explosions, parents, runners, road
- [#41392] 2013-04-16 · Martin Richard Photo: 8-Year-Old Boston Victim Pictured With 'No More Hurting People' Sign
- [#41391] 2013-04-16 · Kelli Johnston, Robert Watling Marry After Boston Marathon Despite Explosions (PHOTOS)
- [#41390] 2013-04-16 · How To Talk To Kids About The Boston Marathon Bombing, Age By Age

### 짐머먼 무죄 평결 — `zimmerman`
- 함께 등장: george, trayvon, martin, verdict, charged
- [#49822] 2013-07-14 · Attorney General Holder Has a Compelling Reason to Consider a Zimmerman Prosecution
- [#49821] 2013-07-14 · George Zimmerman Acquittal: How Are You Coping With The Outcome?
- [#49814] 2013-07-14 · George Zimmerman Trial Reactions Roll In

### 말레이시아항공 MH370 실종 — `malaysia`
- 관찰 기간 안에 근사 Top 20 진입 없음
- 원인 메모(사람 작성): 데이터 범위 문제: 사건 후 14일간 제목에 'malaysia' 1건, 'flight' 5건뿐. HuffPost 이 데이터셋에는 MH370 보도가 거의 없다(편명 'MH370'은 숫자 포함 토큰이라 제거되기도 함).

### 퍼거슨 사건 — `ferguson`
- 함께 등장: sarah, princess, beatrice, kate, police
- [#86220] 2014-08-16 · Ferguson Protesters Guard Stores From Looters
- [#86213] 2014-08-16 · Streets Of Ferguson Erupt Again
- [#86193] 2014-08-15 · FLASHBACK: Ferguson Cops Beat Man, Charged Him For Bleeding On Their Uniforms

### 미국 첫 에볼라 확진 — `ebola`
- 함께 등장: u.s., doctor, missionary, treatment, outbreak
- [#90142] 2014-09-29 · Ebola In Sierra Leone: 'It Reminded Me Of A Conflict Zone'
- [#90088] 2014-09-29 · Ebola and Life-Threatening Diseases: Should You Take Untested Medicines?
- [#90080] 2014-09-28 · Experts: Tradition Of Kissing Corpses May Contribute To Ebola Spread

### 찰스턴 교회 총격 — `charleston`
- 함께 등장: shooting, church, chef, south, carolina
- [#112887] 2015-06-18 · Shooting in Charleston: Let's Stand With Mother Emanuel
- [#112880] 2015-06-18 · Charleston Church Shooting: The Danger of 'All Are Welcome'
- [#112858] 2015-06-18 · Charleston Resident On Church Shooting: ‘The Real Issue Is Race'

### 스캘리아 대법관 사망 — `scalia`
- 함께 등장: justice, antonin, court, supreme, death
- [#134257] 2016-02-15 · Vulnerable Senate Republicans Say No Scalia Replacement Under Obama
- [#134217] 2016-02-14 · Justice Scalia Left Undecided High-Stakes Cases That Could Change The Nation
- [#134214] 2016-02-14 · Replacing Antonin Scalia Could Mean The Biggest Supreme Court Shakeup In 25 Years

### 올랜도 나이트클럽 총격 — `orlando`
- 함께 등장: bloom, shooting, katy, perry, nightclub
- [#144507] 2016-06-12 · The Orlando Massacre: A Reminder of the Dangers LGBT People Live With Every Day
- [#144503] 2016-06-12 · Witnesses Recount Horror At Orlando Nightclub During Mass Shooting
- [#144502] 2016-06-12 · Connecticut's Senators, Who Know Something About Gun Violence, Blame Congress For Orlando Slaughter

### 브렉시트 국민투표 — `brexit`
- 함께 등장: trump, referendum, vote, london, donald
- [#145553] 2016-06-24 · Anti-EU Leader Ignores Jo Cox’s Murder In ‘Crass’ Brexit Victory Speech
- [#145552] 2016-06-24 · Markets Tumble Amid Brexit Chaos
- [#145551] 2016-06-24 · Brexit Chaos Could Shock Trump Voters Back To Common Sense

### 샬러츠빌 집회 폭력 — `charlottesville`
- 함께 등장: white, supremacist, police, rally, violence
- [#181727] 2017-08-13 · Nationwide Solidarity Rallies Assemble In Charlottesville Aftermath
- [#181725] 2017-08-13 · Fox News Host Compares Charlottesville White Supremacists To Black Lives Matter
- [#181724] 2017-08-13 · How What Happened Here In Charlottesville Was Inevitable

### 파크랜드 고교 총격 — `parkland`
- 관찰 기간 안에 근사 Top 20 진입 없음
- 원인 메모(사람 작성): 키워드 표현 문제: 기사들은 사건을 주로 'florida'(56건)·'shooting'(57건)으로 불렀고, 두 단어는 사건 다음 날(2018-02-15) Top 20에 진입했다. 'parkland'는 17건. → 바이그램·개체명 처리 필요.

### WHO 팬데믹 선언 — `coronavirus`
- 함께 등장: u.s., cases, california, cruise, ship
- [#204291] 2020-03-11 · Nearly 1,000 People Diagnosed With Coronavirus In U.S.
- [#204290] 2020-03-10 · Only 6,563 Americans Have Been Tested For The Coronavirus So Far
- [#204289] 2020-03-10 · Thanks To The Coronavirus, Now Is A Great Time To Refinance Your Debt

### 조지 플로이드 사망 — `floyd`
- 관찰 기간 안에 근사 Top 20 진입 없음
- 원인 메모(사람 작성): 희소 구간 문제: 2020년은 하루 약 6건이라 감쇠 대표 규모 1,000건이 약 6개월에 해당한다. 14일간 'floyd' 4건. 'police'는 +4일에 Top 20 진입. → 날짜 기준 감쇠 비교 필요.

### 러시아의 우크라이나 침공 — `ukraine`
- 함께 등장: trump, russian, russia, military, giuliani
- [#208366] 2022-02-25 · Republicans At CPAC Would Rather Talk About The Southern Border Than Ukraine
- [#208364] 2022-02-25 · UN To Scale Up Humanitarian Operations In Ukraine Following Russia's Invasion
- [#208363] 2022-02-25 · Europe Braces For Refugees As Russia Attacks Ukraine's Capital
