---
jira:
  key: ACME-4
  type: Bug
  status: In Progress
  status_category: In Progress
  priority: Highest
  labels:
  - billing
  - customer-reported
  components:
  - billing-api
  assignee: Tomás Ibarra
  reporter: Dana Okafor
  created: '2026-09-24T07:45:00Z'
  updated: '2026-09-30T17:02:00Z'
  sprints:
  - Sprint 24
sample: true
---

# ACME-4: Invoice total is off by one cent for some currencies

Reported by support: for invoices in Japanese yen and a few other zero-decimal currencies the total shows one unit more than the sum of the lines.

Steps to reproduce:

1. create an invoice in JPY with three lines
2. open the invoice page
3. compare the total with the sum of the lines

Suspected cause: rounding applied twice.

## Comments

### Tomás Ibarra · 2026-09-25 10:15 UTC

Confirmed: the tax is rounded per line and then again on the total. Fix is to round once, at the end.

### Priya Nair · 2026-09-25 10:40 UTC

Please keep the old behaviour for invoices already sent. Only new invoices get the fix.

### Tomás Ibarra · 2026-09-30 17:00 UTC

Fix is in review. Added a test table for 12 currencies.
