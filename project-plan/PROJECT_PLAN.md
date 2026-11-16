# Trend Pulse Project Plan

Trend Pulse identifies emerging topics in English-language news headlines from publishers in multiple countries using the GDELT Article List (GAL). The main dashboard ranks topics over the past seven days using time decay and shows how many publishers and countries are represented in the coverage. The 72-hour view highlights topics whose share of coverage has increased compared with the preceding 72 hours.

The submission includes a reproducible replay of a fixed seven-day dataset, both dashboard views, comparative experiments for the decaying window and DGIM, and a live Upstage Solar demonstration. Periodic ingestion is a stretch goal and can reuse the replay data format and aggregation logic. It adds batch detection, retries, restart checkpoints, duplicate handling, and a visible last-success timestamp. A 30-day coverage history is planned for a later phase.

## Components and Completion Criteria

| Component | Design | Completion criteria |
|---|---|---|
| Data | Replay seven consecutive days of GAL data in observation-time order. Analyze English-language articles from publishers whose countries can be verified. | Record the input hash, article counts by day, publisher, and country, the number of duplicates removed, and collection gaps. Replaying the same input yields the same article order. |
| Seven-day topic ranking | Apply elapsed-time decay to articles observed in the past seven days. Use a 48-hour half-life as the initial experiment setting. | Verify the seven-day boundary and score calculation. Compare the baseline that retains all keywords with the implementation that drops low-scoring keywords, measuring aggregation memory, top-K overlap, and missed or delayed entries. |
| 72-hour surges | Compare the share of articles containing a keyword in the latest 72 hours with its share in the preceding 72 hours. | Reproduce the results once both windows have adequate coverage. Show supporting articles and the publishers that covered each topic. |
| DGIM | Estimate how many of the last N articles mention a selected keyword, using observations made since tracking began. | Compare estimation error and storage cost against an exact count over the same articles. |
| Dashboard and Solar | Connect topic rankings and surges to supporting articles and explanations from Upstage Solar. | Confirm live Solar tool calls and verify the cited articles. Reproduce data preparation, the experiments, and the dashboard demonstration from a fresh checkout. |
