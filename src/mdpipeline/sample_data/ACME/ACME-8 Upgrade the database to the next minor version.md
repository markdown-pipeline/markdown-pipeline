---
jira:
  key: ACME-8
  type: Task
  status: Done
  status_category: Done
  priority: Medium
  labels:
  - infrastructure
  components:
  - platform
  assignee: Wen Zhao
  reporter: Dana Okafor
  created: '2026-07-20T08:30:00Z'
  updated: '2026-08-02T21:10:00Z'
  resolved: '2026-08-02T21:10:00Z'
  sprints:
  - Sprint 22
sample: true
---

# ACME-8: Upgrade the database to the next minor version

Routine upgrade. Done in the maintenance window; no customer impact.

Rollback plan: restore the snapshot taken before the window.

## Comments

### Wen Zhao · 2026-08-02 21:05 UTC

Upgrade finished in 11 minutes. Replication lag back to normal.
