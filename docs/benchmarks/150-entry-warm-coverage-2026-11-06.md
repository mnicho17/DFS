# 150-entry warm-build coverage

Synthetic baseline/native comparisons on application head 89bb1c0, based on PR #192. Neither production inputs nor lineup constraints were changed. These are single warm timing pairs, not stable timing medians or production latency estimates.

| Format | Prepared / screened / validated / audited candidates | Warm baseline | Warm integrated | Integrated selection |
| --- | --- | --- | --- | --- |
| Classic | 300 / 300 / 300 / 300 | 29.233s | 24.171s | 13.334s |
| Showdown | 985 / 985 / 985 / 985 | 89.984s | 77.135s | 61.627s |

All four builds per format completed 150 lineups. Each format retained exact baseline/native parity across stage scores, ordered lineups, structured portfolio evidence and independent audit diagnostics. Screening used 250 scenarios, validation 2,500; both fully replayed on warm runs. Each audit completed 2,000 fresh scenarios. Frozen player inputs and library bytes remained unchanged. Field size was 1,000. Requested candidate and shortlist budgets were both 1,000; actual delivered counts above are authoritative.

Classic preparation used the existing three-second bound. Showdown preparation used 30 seconds: the earlier three-second 20-entry samples did not establish 150-entry coverage. This patch exposes a benchmark-only preparation budget, bounded to 120 seconds, records the actual saved count and caps each search batch at the supported 1,000 while preserving larger candidate targets. No application runtime behavior changes.

Portfolio selection remains the largest measured stage, especially for Showdown. Any further optimization must preserve exact selections, rule enforcement, cancellation and independent audit behavior. These synthetic fixtures do not cover production 150-entry slates, user-entered limits, all Captain pools or every roster composition.

Raw receipts are the adjacent native-classic-150-entry-2026-11-06.json and native-showdown-150-entry-2026-11-06.json. The Classic receipt predates the preparation-count fields; its 300-row stage counts are recorded in its profiles.
