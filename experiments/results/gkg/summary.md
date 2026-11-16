# GKG experiments summary

<!-- input_manifest:begin -->
## Input manifest

- command: `python scripts/gkg_build.py --start 2024-10-01 --end 2024-10-01`
- manifest: `experiments/results/gkg/input_manifest.json`
- git: b1b8b9cac12b6cce6d3c6ee036dfe9ee6e44ba5e (dirty=False), gkg.py sha256 fbd7f06b4d6dc14a…
- config: {'include_countries': False, 'place_types': (2, 3, 4, 5), 'drop_self_orgs': True, 'self_prefix_min_len': 4, 'dedup_urls': True}

```
range        : 2024-10-01 .. 2024-10-01 (1 days, UTC)
15-min slots : expected 96, read 96, missing on server 0, not downloaded 0
articles     : 163,360 kept of 163,360 lines (duplicate URL 0, bad line 0, empty URL 0)
with title   : 99.8%   no keys: 7.0%   domains: 8,721
self-name    : 1,687 organization keys dropped
distinct     : persons 156,004, orgs 99,420, places 25,988, countries 237
per article  : persons 3.25, orgs 2.37, places 1.94
time order   : 2024-10-01T00:00:00Z .. 2024-10-01T23:45:00Z, out of order 0
stream sha256: d12df612a886247222abde99e7366707bf852c63b7047cc3d984463682ff7d2a
raw sha256   : a94b0083515b83d1696e50f7270330db4e4a89db82c6a22d0ed029404b72fe1c
run          : 10.3 s, peak RSS 50.2 MB (URL hash table 2.1 MB)
```
<!-- input_manifest:end -->
