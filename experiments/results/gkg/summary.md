# GKG experiments summary

<!-- input_manifest:begin -->
## Input manifest

- command: `python scripts/gkg_build.py --start 2024-10-01 --end 2024-10-01`
- manifest: `experiments/results/gkg/input_manifest.json`
- git: 5ed6b463aefae93e26a7c3327dd3df79f2c66d50 (dirty=True), gkg.py sha256 70b8c92918b2d3d1…
- config: {'include_countries': False, 'place_types': (2, 3, 4, 5), 'drop_self_orgs': True, 'dedup_urls': True}

```
range        : 2024-10-01 .. 2024-10-01 (1 days, UTC)
15-min slots : expected 96, read 96, missing on server 0, not downloaded 0
articles     : 163,360 kept of 163,360 lines (duplicate URL 0, bad line 0, empty URL 0)
with title   : 99.8%   no keys: 7.0%   domains: 8,721
self-name    : 1,641 organization keys dropped
distinct     : persons 156,004, orgs 99,433, places 25,988, countries 237
per article  : persons 3.25, orgs 2.37, places 1.94
time order   : 2024-10-01T00:00:00Z .. 2024-10-01T23:45:00Z, out of order 0
stream sha256: f0c31c8cd77002118e69811f6baa01c89c210adec917ba3b6ebb569dee2c7a34
raw sha256   : a94b0083515b83d1696e50f7270330db4e4a89db82c6a22d0ed029404b72fe1c
run          : 10.2 s, peak RSS 50.3 MB (URL hash table 2.1 MB)
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
- git: 5ed6b463aefae93e26a7c3327dd3df79f2c66d50 (dirty=True)

```
input   : 2024-10-01 .. 2024-10-01, 163,360 articles, 25 snapshots, stream sha256 f0c31c8cd7700211…
speed   : engine 27,482 articles/s, end-to-end 13,497 articles/s (read 6.16 s, engine 5.94 s)
memory  : peak RSS 128.6 MB; decay structures ≈ 61.2 MB (key strings 281,425 unique, 17.8 MB)
    72h/exact      keys 281,425 (peak 281,425, deleted 0), ≈ 14.4 MB
   168h/exact      keys 281,425 (peak 281,425, deleted 0), ≈ 14.4 MB
   720h/exact      keys 281,425 (peak 281,425, deleted 0), ≈ 14.4 MB
evidence: 382 tracked keys, 95,586 articles, 308,728 postings, file 24.4 MB (data/gdelt/processed/evidence.sqlite)
--- Top 10 72h/exact (score, bias-corrected rate per hour) at 2024-10-01T23:45:00Z
   1. O:united states                            11926.4  561.8/h
   2. L:new york, united states                  10327.7  486.5/h
   3. L:washington, washington, united states     7138.2  336.2/h
   4. P:joe biden                                 5374.7  253.2/h
   5. L:georgia, united states                    5218.0  245.8/h
   6. L:texas, united states                      5103.6  240.4/h
   7. L:florida, united states                    4914.6  231.5/h
   8. O:instagram                                 4821.6  227.1/h
   9. P:kamala harris                             4776.9  225.0/h
  10. P:donald trump                              4768.7  224.6/h
--- Top 10 168h/exact (score, bias-corrected rate per hour) at 2024-10-01T23:45:00Z
   1. O:united states                            12604.0  557.1/h
   2. L:new york, united states                  10863.3  480.2/h
   3. L:washington, washington, united states     7533.6  333.0/h
   4. P:joe biden                                 5681.4  251.1/h
   5. L:georgia, united states                    5513.5  243.7/h
   6. L:texas, united states                      5385.6  238.1/h
   7. L:florida, united states                    5173.6  228.7/h
   8. O:instagram                                 5088.7  224.9/h
   9. P:donald trump                              5040.1  222.8/h
  10. P:kamala harris                             5032.6  222.4/h
--- Top 10 720h/exact (score, bias-corrected rate per hour) at 2024-10-01T23:45:00Z
   1. O:united states                            13018.9  554.5/h
   2. L:new york, united states                  11191.2  476.6/h
   3. L:washington, washington, united states     7775.9  331.2/h
   4. P:joe biden                                 5869.8  250.0/h
   5. L:georgia, united states                    5695.0  242.5/h
   6. L:texas, united states                      5558.6  236.7/h
   7. L:florida, united states                    5332.9  227.1/h
   8. O:instagram                                 5251.9  223.7/h
   9. P:donald trump                              5206.7  221.7/h
  10. P:kamala harris                             5189.0  221.0/h
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
- Evidence store: postings start at the snapshot where a key first enters any Top 200 (no backfill).
  Earlier articles about that key have no postings; `keys.first_tracked_ts` records where coverage
  starts. On 2024-10-01 the store held 95,586 of 163,360 articles (59 %), 24.4 MB, because broad keys
  (e.g. "O:united states") are tracked. Over 61 days this projects to ≈ 1.5 GB [미확인: not measured].
- After one day all three windows give nearly the same Top 10, as expected: with 24 h elapsed the
  corrected rates of every half-life reduce to the average rate of that day.
- GKG artifacts visible in the Top 10: "O:united states" (country tagged as an organization) and
  "L:washington, washington, united states" (type 3 US city, likely Washington D.C. geocoded to
  Washington state [미확인]). These are not filtered.
