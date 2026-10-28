---
jira:
  key: ACME-15
  type: Task
  status: Done
  status_category: Done
  priority: Medium
  labels:
  - billing
  assignee: Wen Zhao
  reporter: Dana Okafor
  parent: ACME-1
  created: '2026-08-25T09:00:00Z'
  updated: '2026-09-12T09:00:00Z'
  resolved: '2026-09-12T09:00:00Z'
  sprints:
  - Sprint 23
sample: true
---

# ACME-15: Compare PDF libraries for invoice export

Compare the two candidate libraries on layout control, fonts, licence and speed. Write down the result.

## Comments

### Wen Zhao · 2026-09-12 08:55 UTC

Decision: use the server-side library. It handles our fonts and costs nothing. The other one needs a paid licence for our volume.

### Dana Okafor · 2026-09-12 09:20 UTC

Approved.
