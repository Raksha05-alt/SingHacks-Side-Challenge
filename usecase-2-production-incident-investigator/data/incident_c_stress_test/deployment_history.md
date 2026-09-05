# Deployment history

| Version | Timestamp (UTC) | Component | Change |
|---|---|---|---|
| v3.1.0 | 2026-08-10 09:00 | inventory-service | Lowered internal lock-retry backoff from 200ms to 100ms |
| v3.1.4 | 2026-08-30 11:00 | pricing-service | Promotion engine rule update (unrelated) |

The `inventory-service` change on 2026-08-10 predates the report window
by more than two weeks and is not believed related to the symptom -
reservation retry volume has not measurably changed since that release
according to the weekly rollup dashboard.
