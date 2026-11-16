# GKG experiments summary

<!-- input_manifest:begin -->
## Input manifest

- command: `python scripts/gkg_build.py --start 2024-09-15 --end 2024-11-14 --out experiments/results/gkg/manifest_61d/input_manifest.json`
- manifest: `experiments/results/gkg/manifest_61d/input_manifest.json`
- git: f19ff63a9179f2ac8c4f705a8618b691f8b7a538 (dirty=False), gkg.py sha256 a4afedc209f113a4…
- config: {'include_countries': False, 'place_types': (2, 3, 4, 5), 'drop_self_orgs': True, 'drop_country_orgs': True, 'dedup_urls': True}

```
range        : 2024-09-15 .. 2024-11-14 (61 days, UTC)
15-min slots : expected 5856, read 5856, missing on server 0, not downloaded 0
articles     : 8,313,313 kept of 8,313,432 lines (duplicate URL 0, bad line 119, empty URL 0)
with title   : 99.8%   no keys: 7.2%   domains: 17,168
self-name    : 82,887 organization keys dropped
country org  : 570,753 organization keys equal to a country name dropped
distinct     : persons 3,243,869, orgs 1,939,430, places 240,996, countries 252
per article  : persons 3.28, orgs 2.27, places 1.90
time order   : 2024-09-15T00:00:00Z .. 2024-11-14T23:45:00Z, out of order 0
stream sha256: fb77326c554e5eae595c2e38545f8eed5db0d2ae483826db7fb850ebd359fa5b
raw sha256   : 0172f9374476a14cd73ca040947d41ae2afc058552fbfd1390575c522dfec230
run          : 588.8 s, peak RSS 440.2 MB (URL hash table 134.2 MB)
```
<!-- input_manifest:end -->
