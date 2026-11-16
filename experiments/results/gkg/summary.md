# GKG experiments summary

<!-- input_manifest:begin -->
## Input manifest

- command: `python scripts/gkg_build.py --start 2024-10-01 --end 2024-10-01`
- manifest: `experiments/results/gkg/input_manifest.json`
- git: 2e438d7bd9fab35cbaaca15429418c2fc097f582 (dirty=False), gkg.py sha256 a2a6ee04429b83f2…
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
run          : 11.5 s, peak RSS 50.2 MB (URL hash table 2.1 MB)
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
