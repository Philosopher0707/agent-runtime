# Northwind Sync — release notes

## 3.2 — 2026-08-14

- Added per-account rate limiting. Previously limits were applied per API key.
- Request logs now retain for 30 days, down from 90.
- Fixed a bug where retries were attempted on non-idempotent operations.

## 3.0 — 2026-05-02

- **Removed** the legacy `/v1/batch` endpoint. Callers must use `/v2/batch`.
- Region pinning became available.

## 2.8 — 2026-01-19

- Added the `us-east` region.
