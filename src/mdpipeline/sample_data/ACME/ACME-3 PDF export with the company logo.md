---
jira:
  key: ACME-3
  type: Story
  status: In Progress
  status_category: In Progress
  priority: High
  labels:
  - billing
  - design
  components:
  - billing-api
  assignee: Wen Zhao
  reporter: Dana Okafor
  parent: ACME-1
  created: '2026-08-04T08:05:00Z'
  updated: '2026-09-30T10:12:00Z'
  sprints:
  - Sprint 24
sample: true
---

# ACME-3: PDF export with the company logo

As a billing admin I can download an invoice as a PDF that shows our company logo and legal address.

Open question: which PDF library. The two candidates are in ACME-14 and ACME-15.

## Links

- is blocked by ACME-9: Logo upload fails for SVG files

## Comments

### Wen Zhao · 2026-09-22 15:30 UTC

First render works. Long company names overflow the header.

### Sam Lindqvist · 2026-09-23 08:50 UTC

I can take the layout fix once the font licence question is answered.
