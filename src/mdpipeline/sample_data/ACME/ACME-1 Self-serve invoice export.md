---
jira:
  key: ACME-1
  type: Epic
  status: In Progress
  status_category: In Progress
  priority: High
  labels:
  - billing
  assignee: Priya Nair
  reporter: Dana Okafor
  created: '2026-08-03T09:12:00Z'
  updated: '2026-09-29T14:40:00Z'
  sprints:
  - Sprint 23
  - Sprint 24
sample: true
---

# ACME-1: Self-serve invoice export

Customers on the Team plan ask for their invoices as CSV and PDF without writing to support. Goal: a customer can export any invoice from the billing page by the end of Sprint 24.

Success measure: support tickets tagged `invoice-export` drop by half within a month of release.

## Sub-tasks

- ACME-2: CSV export of a single invoice (Done)
- ACME-3: PDF export with the company logo (In Progress)

## Comments

### Dana Okafor · 2026-08-03 09:30 UTC

Scope for this epic is CSV and PDF only. Bulk export and an API endpoint are out for now.

### Priya Nair · 2026-09-15 11:05 UTC

CSV shipped behind a flag. PDF is next; the layout needs a design pass.
