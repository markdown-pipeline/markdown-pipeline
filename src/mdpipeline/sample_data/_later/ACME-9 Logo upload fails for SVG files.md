---
jira:
  key: ACME-9
  type: Bug
  status: Done
  status_category: Done
  priority: High
  labels:
  - billing
  - design
  components:
  - web
  assignee: Sam Lindqvist
  reporter: Priya Nair
  created: '2026-09-21T11:11:00Z'
  updated: '2026-10-01T09:30:00Z'
  resolved: '2026-10-01T09:30:00Z'
  sprints:
  - Sprint 24
sample: true
---

# ACME-9: Logo upload fails for SVG files

Uploading a company logo as an SVG shows 'Unsupported file' even though the help page says SVG is allowed.

We either allow SVG, after cleaning it, or fix the help page.

## Links

- blocks ACME-3: PDF export with the company logo

## Comments

### Sam Lindqvist · 2026-09-22 09:00 UTC

SVG can contain scripts, so we cannot store it as is. Options: convert to PNG on upload, or sanitise.

### Wen Zhao · 2026-09-22 09:30 UTC

Prefer converting to PNG. The PDF needs a raster logo anyway.

### Sam Lindqvist · 2026-10-01 09:30 UTC

Fixed: SVG logos are converted to PNG on upload. The help page now says so.
