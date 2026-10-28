---
sample: true
---
# Runbook: failed payment webhooks

Use this page when the alert "payment webhook failures above 5%" fires. Related issue: ACME-12.

## First checks

1. Open the webhook dashboard and look at the error class: timeouts, signature errors or server errors.
2. Check the status page of the payment provider.
3. Look at the last deploy of `billing-api`. If it was less than an hour ago, roll it back first and investigate afterwards.

## Replaying events

Failed events are kept for 7 days. Replay them from the admin tool, oldest first. Replays are safe: each event carries an id and the handler ignores ids it has already processed.

## Who to call

The on-call engineer for billing. If the provider is down, nothing needs fixing on our side; post a note in the incident channel and replay when they recover.
