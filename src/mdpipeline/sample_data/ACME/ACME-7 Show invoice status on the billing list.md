---
jira:
  key: ACME-7
  type: Story
  status: Done
  status_category: Done
  priority: Medium
  labels:
  - billing
  components:
  - web
  assignee: Sam Lindqvist
  reporter: Dana Okafor
  created: '2026-07-14T09:00:00Z'
  updated: '2026-08-12T15:30:00Z'
  resolved: '2026-08-12T15:30:00Z'
  sprints:
  - Sprint 22
sample: true
---

# ACME-7: Show invoice status on the billing list

As a billing admin I see paid, due or overdue next to each invoice in the list, so I know what needs action.

## Comments

### Sam Lindqvist · 2026-08-12 15:29 UTC

Shipped. Overdue is red and also has a text label, so it does not rely on colour.
