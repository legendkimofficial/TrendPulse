# Initial Review

Reviewed revisions: [main](https://github.com/legendkimofficial/TrendPulse/tree/0b76b80), [project-plan](https://github.com/legendkimofficial/TrendPulse/tree/051cc83), and [project-verification](https://github.com/legendkimofficial/TrendPulse/tree/cbb1010).

The verification branch adds a separate GDELT GAL workflow alongside the HuffPost prototype. The plan and implementation agree on the publisher-country policy. Eligible English articles from unmapped publishers contribute to ranking and surge calculations. The dashboard counts mapped publisher countries, and the input manifest reports unmapped articles.

## Findings

| Area | Status | Evidence and remaining work |
|---|---|---|
| GAL collection and replay | Verified in the recorded run | The fixed input covers November 9–15, 2026 (UTC). The collector retrieved 1,276 files and retained 789,845 English articles after filtering and removing 89,869 duplicate URLs. Repeated normalization and replay produced matching hashes in the original run. The manifest records three missing 15-minute slots. Publish the full replay digest with the next verification run. |
| Publisher counts | Partially meets the plan | The manifest records daily totals, country totals, and the top 50 publisher domains. Full publisher-level counts remain a completion criterion. Of the retained articles, 619,281 (78.4%) have an unmapped publisher country. |
| Seven-day ranking | Passed | Tests cover expiration, equal observation timestamps, per-title deduplication, deterministic ties, direct score summation, and entry and reentry behavior. The ranking uses a 48-hour half-life. |
| 72-hour surge | Calculation and deferral passed. Numeric demo pending | Tests cover the percentage-point calculation and the minimum-coverage rules. The recorded dashboard demo deferred the calculation because of a collection gap. The review independently confirmed that the fixed input contains no gap-free 144-hour comparison window. |
| Decay comparison | Measured on GAL | All three pruning thresholds preserved the reference Top 20 at evaluated snapshots. Aggregation memory fell by up to about 5%. The evaluated interval contained zero ranking entry events, so entry precision, recall, and delay are unavailable. |
| DGIM comparison | Measured on GAL | All four tested configurations stayed within their respective error bounds. The exact queue matched direct counts. At `(N, r) = (5000, 2)`, DGIM used 1,021 Python object bytes per tracked keyword versus 3,087 bytes for the exact count. |
| Dashboard | Required views present | The GDELT dashboard shows ranking and surge tables, entry labels, publisher and country counts, related articles, and the Solar response mode. The timeline, ranking flow, and exact-versus-approximate charts remain features of the HuffPost dashboard and fall outside the current plan. |
| Upstage Solar | Offline passed. Live call pending | The recorded offline demo passed six checks covering tool use, replay context, citations, and score agreement. Live API calls and live answer grounding remain unverified. |
| Reproducibility | Tests passed. Full rerun pending | The initial review ran the test suite from an archive of `cbb1010` in a temporary directory. All 92 tests passed. A fresh checkout still needs an end-to-end run covering data collection, replay verification, experiments, and the dashboard demo. |
| Periodic refresh | Partial stretch-goal coverage | The same-window refresh kept the input hash unchanged in the recorded check. Scheduling, restart checkpoints, and a visible last-success timestamp remain stretch work. The 30-day view is planned for a later phase. |

## Submission priorities

1. Obtain a fixed seven-day input containing a verified gap-free 144-hour comparison window. Show a numeric surge and its supporting articles in the dashboard. The last gap in the current input ends on November 11 at 13:15 UTC. November 17 at 13:15 UTC is the earliest theoretical comparison time after that gap, and availability also depends on complete coverage after the gap.
2. Run the live Upstage Solar demo with the key supplied through the environment. Record the actual tool calls and check the answer's figures and article citations against tool results.
3. Add full publisher counts to the data manifest and rerun the documented workflow from a fresh checkout. Record the input hash, full replay digest, and demo evidence.

The initial review checked the branch revisions, code, committed evidence, and available comparison windows. It reran the test suite. Collection, replay verification, refresh, and long-running experiments were assessed from their existing records.

- [GDELT verification report](../docs/GDELT_SUBMISSION.md)
- [Input manifest](../experiments/results/gdelt/input_manifest.json)
- [GAL comparison results](../experiments/results/gdelt/summary.md)
- [Offline Solar demo](../experiments/results/gdelt/solar_demo.md)
- [Synthetic algorithm verification](../verify/results_algorithms.md)
