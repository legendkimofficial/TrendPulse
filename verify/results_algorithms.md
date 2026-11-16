# Decaying Window and DGIM Verification Results

## Scope and setup

The verification covers the existing algorithms on a synthetic news stream. Replay on real GDELT data, seven-day expiration, and 72-hour surge detection remain pending. Top-K entries were evaluated every 50 articles. Event matching treated first entries and reentries alike and used a fixed tolerance for each decay mode. An offset of zero indicates a match at the same snapshot.

- **Command** — `python verify/verify_algorithms.py` from the repository root.
- **Environment** — Python 3.12.2 on Windows 11 (10.0.26200, SP0), 12 CPUs. The run was recorded at 16:12 on November 16, 2026.
- **Data** — 48,000 synthetic news articles spanning January 1, 2018 through December 17, 2024 (2,455 days), with 30- and 60-day gaps. The random seed was `20261116`. Keyword extraction used the repository's default v0.3 token and phrase rules.
- **Decay modes** — `EngineConfig.decay_unit` supports `article` (one step per article) and `day` (one step per elapsed calendar day, with zero steps between articles dated the same day).
- **Overall result** — 28 passed, 0 failed, 2 not run, out of 30 checks. Runtime was 33.8 seconds.

## V1. Direct-summation reference

The direct-summation check used a decay rate of `c = 0.1` and a pruning threshold of `θ = 0.5`. For each keyword, the reference summed `(1 - c)^(T_t - T_i)` over all articles up to the current step that contained the keyword. The reference used floating-point arithmetic and evaluated both article-based and day-based decay.

## V2. Decaying window

### V2-1. Aggregation memory

The table reports averages across samples taken every 2,000 articles, except where the column specifies a peak or cumulative total. Python aggregation size was measured with `deep_sizeof` and includes keyword lifetime and error-tracking state.

| Decay unit | θ | Stored keywords (approx. / reference) | Keyword reduction | Mean heap entries | Python aggregation memory (approx. / reference) | Memory reduction | Peak approx. keywords | Cumulative removals |
|---|---|---|---|---|---|---|---|---|
| Article | 0.1 | 5,911 / 27,865 | 78.8% | 6,932 | 1.82 MB / 2.99 MB | 39.2% | 6133 | 76,610 |
| Article | 0.5 | 2,735 / 27,865 | 90.2% | 3,366 | 0.84 MB / 2.99 MB | 71.9% | 2940 | 106,378 |
| Article | 0.9 | 1,016 / 27,865 | 96.4% | 1,672 | 0.35 MB / 2.99 MB | 88.3% | 1107 | 139,685 |
| Day | 0.5 | 843 / 24,924 | 96.6% | 1,503 | 0.31 MB / 2.47 MB | 87.6% | 1091 | 172,784 |

### V2-2. Top-K overlap and entry detection

Snapshots were taken every 50 articles. The matching tolerance was ±1,000 articles for article-based decay and ±11 days for day-based decay. Top-K overlap measures agreement with the unpruned reference.

| Decay unit | θ | Top-10 overlap | Mean Top-20 overlap | Minimum Top-20 overlap | Identical order | Reference entries | Matched (recall) | Same snapshot | Early / late | Absolute offset in articles (median / p95 / max) | Late beyond tolerance | Pending at end | Left reference Top K | Approx.-only entries |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Article | 0.1 | 100.00% | 100.00% | 100.0% | 100.0% | 57 | 57 (100.0%) | 100.0% | 0.0% / 0.0% | 0 / 0 / 0 | 0 | 0 | 0 | 0 |
| Article | 0.5 | 100.00% | 100.00% | 100.0% | 100.0% | 57 | 57 (100.0%) | 100.0% | 0.0% / 0.0% | 0 / 0 / 0 | 0 | 0 | 0 | 0 |
| Article | 0.9 | 100.00% | 100.00% | 100.0% | 100.0% | 57 | 57 (100.0%) | 100.0% | 0.0% / 0.0% | 0 / 0 / 0 | 0 | 0 | 0 | 0 |
| Day | 0.5 | 99.98% | 99.94% | 60.0% | 99.3% | 217 | 214 (98.6%) | 100.0% | 0.0% / 0.0% | 0.0 / 0 / 0 | 0 | 0 | 3 | 12 |

### V2-3. Score error and implementation bounds

| Decay unit | θ | Max error across either Top K | Max error across all keywords | Bound θ/(1-θ) | Bound violations | `key_error` violations | Approx. score above reference |
|---|---|---|---|---|---|---|---|
| Article | 0.1 | 0.0 | 0.1048 | 0.1111 | 0 | 0 | 0 |
| Article | 0.5 | 0.0 | 0.8528 | 1.0 | 0 | 0 | 0 |
| Article | 0.9 | 0.0 | 3.7768 | 9.0 | 0 | 0 | 0 |
| Day | 0.5 | 0.4829 | 0.6947 | 1.0 | 0 | 0 | 0 |

## V3. DGIM setup

For each tracked keyword, an article contributed one bit indicating whether its title contained the keyword. DGIM estimated the count in the index interval `(t-N, t]`. The table below reports the relative-error bound applied to each configuration. The midpoint and textbook estimates differed by 0.5 when the bucket list was nonempty.

## V4. DGIM error, bucket counts, and memory

The tracked keywords were `tracka` (246 articles), `trackb` (992), `trackc` (3,863), `trackd` (14,481), and `tracke` (2,186). Results are aggregated across the tracked keywords. Logical bits use the compact storage model implemented in `memory_bits()`. Python bytes use `deep_sizeof` to estimate the in-memory object size per tracked keyword.

| N | r | Estimator | Evaluations | Mean absolute error | Max absolute error | Mean relative error | p95 relative error | Max relative error / bound | Max partial-window absolute error | Buckets (mean / max) | DGIM logical bits | Exact logical bits | DGIM Python bytes | Exact Python bytes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 500 | 2 | midpoint | 955 | 4.845 | 120.5 | 8.48% | 22.73% | 38.9% / 50% | 0.0 | 5.1 / 14 | 66 | 201 | 850 | 2,440 |
| 500 | 3 | midpoint | 955 | 2.671 | 56.5 | 4.79% | 13.64% | 21.4% / 25% | 0.0 | 7.1 / 20 | 92 | 201 | 1,073 | 2,440 |
| 500 | 5 | midpoint | 955 | 1.945 | 31.5 | 2.90% | 8.75% | 11.5% / 12% | 0.0 | 10.0 / 31 | 130 | 201 | 1,411 | 2,440 |
| 2,000 | 2 | midpoint | 925 | 19.916 | 310.5 | 8.72% | 20.12% | 38.9% / 50% | 63.5 | 7.5 / 17 | 112 | 903 | 1,104 | 6,900 |
| 2,000 | 3 | midpoint | 925 | 11.436 | 255.5 | 5.70% | 15.00% | 20.8% / 25% | 63.5 | 10.9 / 24 | 163 | 903 | 1,491 | 6,900 |
| 2,000 | 5 | midpoint | 925 | 7.659 | 127.5 | 3.73% | 8.65% | 11.5% / 12% | 31.5 | 16.3 / 40 | 245 | 903 | 2,080 | 6,900 |
| 2,000 | 2 | textbook | 925 | 19.916 | 310.0 | 8.65% | 21.28% | 33.3% / 50% | 64.0 | 7.5 / 17 | 112 | 903 | 1,104 | 6,900 |
| 8,000 | 2 | midpoint | 805 | 85.973 | 511.5 | 9.17% | 21.23% | 33.0% / 50% | 511.5 | 10.4 / 20 | 176 | 4,123 | 1,431 | 24,880 |
| 8,000 | 3 | midpoint | 805 | 46.625 | 255.5 | 5.67% | 15.26% | 20.1% / 25% | 255.5 | 15.5 / 28 | 264 | 4,123 | 2,004 | 24,880 |
| 8,000 | 5 | midpoint | 805 | 32.079 | 254.5 | 3.89% | 9.03% | 10.9% / 12% | 127.5 | 24.4 / 44 | 416 | 4,123 | 2,948 | 24,880 |

## Check results

All C2 and C2D checks passed. The tables in V2 report the measured score errors, Top-K overlap, and entry timing for each threshold. The three unmatched day-based entries left the reference Top K before a match was recorded.

| ID | Check | Status | Evidence |
|---|---|---|---|
| C1.1 | Day-based reference against direct summation | Passed | All keywords matched at every step across 45 articles, with error below 1e-9. The input included same-day articles and long gaps. |
| C1.2 | Hand-calculated examples | Passed | Included two articles at the same time and a 30-day gap. |
| C1.3 | Reappearance after pruning | Passed | The keyword was removed on day 39 and restarted at 1.000000 on reappearance. The reference score was 1.014318. Error was 0.014318, below `key_error` (0.900) and the bound (1.00). |
| C1.4 | Pruning below θ against the textbook rule | Passed | Active sets and scores agreed at every step across 2,000 articles with mixed day increments. |
| C1.5 | Article-based reference against direct summation | Passed | All keyword scores agreed across 1,500 articles. |
| C2.a–c | Article-based score bounds, overlap, and entry timing at θ = 0.1, 0.5, and 0.9 | Passed (9 checks) | All 57 reference entries matched at the same snapshot for each threshold. See V2. |
| C2D.a–c | Day-based score bounds, overlap, and entry timing at θ = 0.5 | Passed (3 checks) | 214 of 217 reference entries matched at the same snapshot. See V2. |
| C3.1 | Stream with no mentions | Passed | Both queries (at 0 and 100) returned 0.0 with zero buckets. |
| C3.2 | Partial window and bucket merge | Passed | The merged bucket retained the newer timestamp. Buckets were `[[2, 1], [1, 2]]` as expected. The estimate at index 2 was 2.5, within the expected interval [2, 3]. |
| C3.3 | Window expiration | Passed | Estimates at indices 9, 11, and 12 were 2.5, 1.0, and 0.0, respectively. All matched the expected values. |
| C3.4 | Duplicate adds at the same index | Passed | The estimate at index 6 was 2.0 as expected. |
| C3.5 | Difference between midpoint and textbook estimators | Passed | The difference was 0.5 for nonempty buckets and zero for empty buckets at every step of a 300-step random stream. |
| C3.6 | DGIM and exact counting against a direct count | Passed | The check used bits `[1, 1, 0, 1, 0, 1]` and `N = 4`, with a common tracking start. Both estimators met their respective checks. |
| C3.7 | Bucket invariants and relative-error bounds | Passed | The 4,000-step random stream preserved power-of-two buckets, at most `r` buckets per size, and descending timestamps. For `r = 2, 3, 5`, midpoint maximum relative errors were 0.248, 0.202, and 0.114. The textbook maximum was 0.500 in each case. |
| C4.1 | DGIM error bounds and windows with zero mentions | Passed | Zero bound violations and zero nonzero estimates on windows with an exact count of zero. |
| C4.2 | Efficient exact count against a direct count | Passed | `ExactWindowCount` matched every query for the same window and tracking start. |
| C4.3 | Logical storage and Python object size | Passed | V4 reports both measurements. The model assumes `log2(N) + log2(log2(N))` bits per DGIM bucket and `min(mentions × log2(N), N)` bits for exact counting. Python measurements cover the list and deque objects. |
| C5.1 | GDELT replay | Not run | No local replay data or aggregate CSV was available. At the time of the run, the repository had no GDELT loader and `verify/verify_gdelt.py` was an empty placeholder. |
| C5.2 | Separate HuffPost replay | Not run | `data/raw/News_Category_Dataset_v3.json` was unavailable. V2 and V4 can be run on the HuffPost article stream when the input is available. |
| C5.3 | Synthetic input generation and recording | Passed | Generated 48,000 articles over 2,455 days with 30- and 60-day gaps using seed `20261116`. |
