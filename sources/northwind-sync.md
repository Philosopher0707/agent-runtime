# Northwind Sync — service description

## Availability

The service targets an availability of **99.9%** measured monthly, excluding scheduled
maintenance windows announced at least 72 hours in advance.

## Retries

Failed requests are retried automatically **up to three times** with exponential backoff. Retries
apply to idempotent operations only. A non-idempotent operation that fails is not retried, and the
caller is told so.

## Rate limits

The default rate limit is **600 requests per minute** per account. Limits are enforced per
account, not per API key.

## Data retention

Request logs are retained for **30 days**. Payloads are not logged.

## Regions

The service runs in two regions: `eu-west` and `us-east`. There is no automatic failover between
them; a customer may pin their account to one region.
