# GDELT GAL Experiment Results

The run used 789,845 English articles observed November 9–15, 2026 (UTC), with three missing observation slots. Input SHA-256 was `0c5043b3874e3b07f2343687d0d70cc5c03bfdf40446d666859356221bc3c00f`. The fixed input was replayed in full. E1 evaluated every observation batch (approximately 15 minutes). E2 evaluated every four batches. See the [verification report](../../../docs/GDELT_SUBMISSION.md) for commands and limitations.

## E1. Seven-day decayed ranking with pruning

The baseline retained every active contribution. Each variant pruned scores below its threshold after snapshot synchronization. Pruning discarded the keyword's expiration state and started a new score on reappearance. All configurations used a 48-hour half-life and the same title tokens, input, and snapshots.

| θ | Top-20 overlap (mean / min) | Identical order | Absolute error (mean / max) | Relative error (mean / p95 / max) | Approximation above reference | Keywords at end (variant / reference) | Aggregation MB (variant / reference) | Memory reduction |
|---|---|---|---|---|---|---|---|---|
| 0.05 | 1.0000 (1.0) | 100.0% | 0.0 / 0.0 | 0.0000 / 0.0000 / 0.0000 | 0 | 156,693 / 156,693 | 54.5 / 54.5 | 0.0% |
| 0.1 | 1.0000 (1.0) | 100.0% | 0.0 / 0.0 | 0.0000 / 0.0000 / 0.0000 | 0 | 153,239 / 156,693 | 54.5 / 54.5 | 0.0% |
| 0.3 | 1.0000 (1.0) | 100.0% | 0.0 / 0.0 | 0.0000 / 0.0000 / 0.0000 | 0 | 110,681 / 156,693 | 51.8 / 54.5 | 5.0% |

Score errors cover the union of both Top-20 lists. For relative error, the reference score had to be positive. Aggregation memory was sampled every 20 snapshots. Article storage (1,274 MB) and the related-article index (49 MB) were measured separately.

### Ranking entries

The event comparison matched first entries and reentries one to one within ±60 minutes. The observed interval after the 144-hour warm-up contained no entries. Precision, recall, and timing statistics therefore have no observations.

| θ | Reference entries (first / reentry) | Variant entries | Matches | Recall | Precision | Matched by type | Same snapshot | Offset (median / max minutes) | Unmatched (edge / other) | Variant-only |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.05 | 0 (0/0) | 0 | 0 | N/A | N/A | 0/0 | N/A | N/A | 0 / 0 | 0 |
| 0.1 | 0 (0/0) | 0 | 0 | N/A | N/A | 0/0 | N/A | N/A | 0 / 0 | 0 |
| 0.3 | 0 (0/0) | 0 | 0 | N/A | N/A | 0/0 | N/A | N/A | 0 / 0 | 0 |

## E2. DGIM versus exact sliding count

Both counters tracked the same article indices from index 0. The tracked set was the final reference Top 10 plus high-, median-, and low-frequency terms with at least 100 mentions. The four configurations below report the observed error and the bound applied to each pair.

| N | r | Evaluations | Absolute error (mean / max) | Relative error (mean / p95 / max / bound) | False positives at zero | Exact-count mismatches | Buckets (mean / max) | Modeled bits (DGIM / exact) | Python bytes (DGIM / exact) |
|---|---|---|---|---|---|---|---|---|---|
| 200 | 2 | 2,630 | 0.455 / 7.5 | 0.0925 / 0.2500 / 0.3889 / 0.500 | 0 | 0 | 2.6 / 8 | 31 / 32 | 448 / 975 |
| 1,000 | 2 | 3,153 | 1.825 / 26.5 | 0.1111 / 0.2500 / 0.4412 / 0.500 | 0 | 0 | 5.0 / 12 | 71 / 162 | 698 / 1,313 |
| 1,000 | 3 | 3,153 | 1.203 / 15.5 | 0.0720 / 0.1591 / 0.2333 / 0.250 | 0 | 0 | 6.6 / 16 | 93 / 162 | 852 / 1,313 |
| 5,000 | 2 | 3,419 | 8.046 / 119.5 | 0.1070 / 0.2344 / 0.3889 / 0.500 | 0 | 0 | 7.8 / 16 | 133 / 962 | 1,021 / 3,087 |

Modeled bits use `memory_bits()`. Python bytes are average in-memory object sizes per tracked keyword. Results from earlier HuffPost experiments are retained as historical evidence.
