# GKG experiments summary

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
