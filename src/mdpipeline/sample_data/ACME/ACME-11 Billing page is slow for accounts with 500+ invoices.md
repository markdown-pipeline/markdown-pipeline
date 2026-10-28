---
jira:
  key: ACME-11
  type: Bug
  status: Done
  status_category: Done
  priority: High
  labels:
  - performance
  - billing
  components:
  - billing-api
  assignee: Tomás Ibarra
  reporter: Sam Lindqvist
  created: '2026-08-22T08:00:00Z'
  updated: '2026-09-08T10:00:00Z'
  resolved: '2026-09-08T10:00:00Z'
  sprints:
  - Sprint 23
sample: true
---

# ACME-11: Billing page is slow for accounts with 500+ invoices

The invoice list loads in about 9 seconds for large accounts.

## Comments

### Tomás Ibarra · 2026-09-03 14:00 UTC

The list query had no index on the account and date. Added it, plus pagination.

### Tomás Ibarra · 2026-09-08 09:55 UTC

Down to 400 ms for the largest account. Closing.
