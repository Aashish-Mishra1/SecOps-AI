# Local performance measurements

On Windows with Docker Desktop, 20 authenticated event-page reads through nginx at concurrency four completed at 39.45 requests/s. Client wall-clock latency was 84.65 ms P50 and 178.21 ms P95. Reproduce with the seeded Compose stack, local environment loaded, and `python scripts/load_local.py`. Each request reads ten rows at a different offset.

Seven mock investigations had active execution P50/P95 of 678.47/964.54 ms. Isolated log-agent P50/P95 was 225.96/327.74 ms. Recorded mock-call latency P50/P95 was 4.75/9.39 ms including execution bookkeeping. Percentiles use nearest rank. The graph performs additional retrieval, validation, checkpoint and reporting work.

Mock token consumption, output tokens/s and USD/request are unavailable. The dashboard displays recorded provider usage and configured cost when present. Live throughput uses output tokens divided by nonstreaming call duration.

Hybrid retrieval now projects only citation fields from matching rows, leaving the vector in PostgreSQL for ranking. Versioned prompts share a shorter instruction prefix. Compact evidence selection and checkpoint references remain bounded. No cross-tenant result cache was introduced. These measurements cover a local functional workload. Sustained saturation, live-model latency and independent before/after optimization measurements remain open.
