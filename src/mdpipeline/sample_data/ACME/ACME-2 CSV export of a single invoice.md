---
jira:
  key: ACME-2
  type: Story
  status: Done
  status_category: Done
  priority: High
  labels:
  - billing
  components:
  - billing-api
  assignee: Tomás Ibarra
  reporter: Dana Okafor
  parent: ACME-1
  created: '2026-08-04T08:00:00Z'
  updated: '2026-09-02T16:21:00Z'
  resolved: '2026-09-02T16:21:00Z'
  sprints:
  - Sprint 23
sample: true
---

# ACME-2: CSV export of a single invoice

As a billing admin I can download one invoice as CSV from the invoice page.

Acceptance criteria:

- the file has one row per line item
- amounts use the invoice currency, two decimals
- the file name is the invoice number

## Comments

### Tomás Ibarra · 2026-08-20 13:42 UTC

Decision: we write the CSV on the server, not in the browser. A browser export would differ by locale and we need the same file for support.

### Wen Zhao · 2026-08-21 09:10 UTC

Agreed. Please add a test for amounts with a comma as the decimal separator.

### Tomás Ibarra · 2026-09-02 16:20 UTC

Merged and released to staging.
