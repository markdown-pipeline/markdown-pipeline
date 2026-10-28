---
jira:
  key: ACME-5
  type: Bug
  status: To Do
  status_category: To Do
  priority: Low
  labels:
  - auth
  components:
  - web
  reporter: Sam Lindqvist
  created: '2026-09-18T12:00:00Z'
  updated: '2026-09-26T09:00:00Z'
sample: true
---

# ACME-5: Password reset email arrives twice

A few users receive two reset emails within a second. Possibly a double submit of the form.

## Comments

### Sam Lindqvist · 2026-09-26 09:00 UTC

Seen three times in the logs this month. Not urgent; parking until the login work in ACME-6.
