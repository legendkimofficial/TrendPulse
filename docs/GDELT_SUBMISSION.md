# Trend Pulse GDELT Verification

## Scope

This branch includes the GDELT GAL collector, deterministic replay, the seven-day and 72-hour ranking engines, the dashboard, the Solar integration, and the comparison experiments. The verified input covers November 9–15, 2026 (UTC).

## Reproduce the run

Run these commands from the repository root. Data files and credentials are excluded from Git.

| Step | Command |
|---|---|
| Install dependencies | `python -m pip install -r requirements.txt` |
| Collect source batches | `python scripts/fetch_gdelt_gal.py collect --start 2026-11-09 --end 2026-11-15` |
| Create the replay input | `python scripts/fetch_gdelt_gal.py normalize` |
| Check deterministic normalization and replay | `python scripts/fetch_gdelt_gal.py verify` |
| Run the test suite | `python -m unittest discover -s tests` |
| Run the GAL comparisons | `python experiments/run_gal_experiments.py --thetas 0.05 0.1 0.3 --every 4` |
| Launch the dashboard | `python app_gdelt.py` (local port 5001) |
| Run the end-to-end demo | `python experiments/demo_solar.py --t 2026-11-15T12:00:00Z` |
| Check same-window refresh idempotence | `python scripts/refresh_gal.py --check` |

The collector reads gzip JSON Lines from GDELT 3.0 GAL and uses the batch filename timestamp as the UTC observation time. It preserves GAL's estimated publication time separately. The normalized input is ordered by observation time and canonical URL. URL deduplication keeps the earliest observation and preserves the original URL for display.

## Data and results

- **Source** — 1,276 successfully downloaded files, 68 probed files absent, no download failures, and 2,930,537 raw records. The manifest records three missing 15-minute observation slots. A missing filename alone does not establish a missing slot because the collector probes two candidate filenames per slot.
- **Replay** — 789,845 English articles after filtering invalid records and removing 89,869 duplicate URLs. Input SHA-256 is `0c5043b3874e3b07f2343687d0d70cc5c03bfdf40446d666859356221bc3c00f`. The original verification observed matching normalized input hashes and matching replay digests on two runs.
- **Publisher country** — The analysis includes English articles from unmapped publishers. The country count uses mapped publishers only. Mapping v1 uses domain overrides and country-specific domain suffixes. It leaves 619,281 articles (78.4%) unmapped. The manifest retains the top 50 publisher domains. Full publisher-level counts remain pending.
- **Seven-day ranking** — Scores sum exponentially decayed article contributions in `(t-7 days, t]`, with a 48-hour half-life and one mention per keyword per title. Tests cover the window boundary, equal timestamps, duplicate terms, clock advancement, and direct summation.
- **72-hour surge** — The engine compares article shares in consecutive 72-hour windows. Defaults require at least 200 articles per window, five recent articles mentioning the keyword, and three recent publishers. Tests cover the calculation and deferral conditions. The live-data demo deferred correctly because of a collection gap. Every possible 144-hour comparison in this fixed input overlaps the November 11 gap, so displaying a numeric surge requires filling the gap or selecting a different verified input.
- **Decay experiment** — Thresholds 0.05, 0.1, and 0.3 all preserved the reference Top 20 at evaluated snapshots. Score error was zero across the union of the compared Top 20 lists. At 0.3, measured aggregation memory fell from 54.5 MB to 51.8 MB, about 5%. Article storage and the related-article index were measured separately. The evaluated interval contained zero entry events, so entry precision, recall, and delay are unavailable.
- **DGIM experiment** — The four tested `(N, r)` pairs were `(200, 2)`, `(1000, 2)`, `(1000, 3)`, and `(5000, 2)`. Errors stayed within each configuration's bound. The exact queue agreed with direct counts and DGIM returned zero for every evaluated zero-count window. At `(5000, 2)`, storage averaged 133 versus 962 modeled bits and 1,021 versus 3,087 Python object bytes per tracked keyword.
- **Runtime** — The two comparison experiments took 3,551 seconds. The prior HuffPost results and synthetic verification are separate historical runs.
- **Dashboard and Solar** — The dashboard showed the ranking, related articles, and the surge deferral. The offline demo passed six checks, including tool use, cited article IDs, and score agreement. **Live Solar remains unrun.** Set `UPSTAGE_API_KEY` in the environment and rerun the demo to verify live tool calls and answer grounding.
- **Fresh checkout and periodic refresh** — The original run exercised the workflow locally. A fresh-checkout data-to-demo rerun remains unverified. The refresh command passed the identical-window hash check. Scheduling, restart checkpoints, and a dashboard last-success indicator remain stretch work. The 30-day view remains a follow-up.

## Evidence

- [Input manifest](../experiments/results/gdelt/input_manifest.json) with batch hashes and collection gaps

- [GAL experiment summary](../experiments/results/gdelt/summary.md) and [machine-readable results](../experiments/results/gdelt/results.json)
- [Dashboard API capture](../experiments/results/gdelt/app_api_evidence.json)
- [Offline demo report](../experiments/results/gdelt/solar_demo.md) and [structured trace](../experiments/results/gdelt/solar_demo.json)
