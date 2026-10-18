# Usage events (MD-116, FR-S2)

Generated from `src/mdpipeline/usage_events.py` by `scripts/gen_usage_events_doc.py`. Do not edit by hand: a test fails when this file
differs from the allowlist. For marketing's PostHog funnels (MM-91).

## Rules

- **Consent first.** Nothing is queued or sent unless the user chose "Share usage" (MD-115). Declining, or not choosing, sends nothing.
- **Allowlist.** Only the events and properties below can be sent. Anything else is dropped before it is queued, and a value that is not
  one of the allowed values is dropped too. There is no free-text property on any automatic event. The only words that can leave are the ones
  a person types in a source request or a feedback answer and sends with the Send button (see the two explicit sends below).
- **Never sent:** titles, file or folder names, paths, JQL or any query text, project keys, URLs, site names, repository names, emails,
  tokens, error text.
- **Allowed:** fixed lists we define, whole-number counts, durations, booleans and the random IDs below.

## Transport

Events are queued locally, then sent in batches of 20 to PostHog's US cloud (`https://us.i.posthog.com/batch/`, https only, only that
host) from a background thread or a detached process, so a sync, the UI, the CLI and the MCP server never wait for the network. A failed
batch stays queued; each event carries a unique `uuid`, so a resent duplicate is dropped by the analytics host. The last 50 events sent
are shown exactly as sent in Settings → Privacy & usage and by `mdpipeline usage log`.

## What every event carries

| Property | Meaning |
|---|---|
| `distinct_id` | the random `install_id` (reset any time in Settings or with `mdpipeline usage reset-id`): the only identifier |
| `app_version` | the running version |
| `deployment` | always `local` for this app |
| `session_id` | random, new at each launch, never stored across launches |
| `is_internal` | `true` only for the developers' own installs and test runs, `false` for everyone else. It is a yes/no and says nothing about you. It is set by running with `MDPIPELINE_INTERNAL=1` (kept afterwards; `MDPIPELINE_INTERNAL=0` takes it off) |
| `$process_person_profile` | always `false`: tells PostHog not to build a person profile |
| `timestamp` | when the event happened (an `agent_read` day count is dated to that day) |
| `uuid` | unique per event |

## Not events of this list

- **`consent_outcome`** (`share` / `decline` / `no_answer`): the identifier-free consent counter. It has no `distinct_id` of the user, no
  time and no other property, is **off by default** until the owner's legal read, and counts only an install's first decision.
- **Filter events** (`filter_*`) ship with the filter UI. **`teammate_added`** ships with team features. Neither is allowed yet.

## A. Activation path and friction (time to value)

### `app_opened`

Start of the time-to-value clock. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `first_open` | `true` / `false` |

Example payload as sent:

```json
{
  "event": "app_opened",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "first_open": true,
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `consent_decided`

Consent rate (only a 'share' can be sent: a decline sends nothing, it is only counted by the opt-in counter). Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `choice` | `share` |

Example payload as sent:

```json
{
  "event": "consent_decided",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "choice": "share",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `onboarding_step_viewed`

Where people stop (the key funnel). Emitted from: ui.

| Property | Allowed values |
|---|---|
| `step` | `consent` / `choose_source` / `authorize` / `choose_target` / `preview` / `first_sync` / `connect_agent` |

Example payload as sent:

```json
{
  "event": "onboarding_step_viewed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "step": "choose_source",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `onboarding_step_completed`

Where people stop (the key funnel); on the review step also whether the first run writes to a folder or a GitHub repo (MD-152, H-TGT-1). Emitted from: ui.

| Property | Allowed values |
|---|---|
| `step` | `consent` / `choose_source` / `authorize` / `choose_target` / `preview` / `first_sync` / `connect_agent` |
| `ms_on_step` | whole number, 0 to 3,000,000,000 |
| `target` | `directory` / `github` |

Example payload as sent:

```json
{
  "event": "onboarding_step_completed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "step": "preview",
    "ms_on_step": 21000,
    "target": "directory",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `source_add_started`

Which source people reach for first. Emitted from: ui.

| Property | Allowed values |
|---|---|
| `source_type` | `folder` / `jira` / `other` |
| `entry_point` | `empty_state` / `editor` / `onboarding` / `home` / `activity` / `view` / `settings` / `other` |

Example payload as sent:

```json
{
  "event": "source_add_started",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "source_type": "jira",
    "entry_point": "empty_state",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `source_auth_started`

Credential friction. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `source_type` | `folder` / `jira` / `other` |
| `auth_method` | `token` / `oauth` |

Example payload as sent:

```json
{
  "event": "source_auth_started",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "source_type": "jira",
    "auth_method": "token",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `source_auth_completed`

Credential friction. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `source_type` | `folder` / `jira` / `other` |
| `auth_method` | `token` / `oauth` |

Example payload as sent:

```json
{
  "event": "source_auth_completed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "source_type": "jira",
    "auth_method": "token",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `source_auth_failed`

Credential friction. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `source_type` | `folder` / `jira` / `other` |
| `auth_method` | `token` / `oauth` |
| `error_class` | `auth` / `rate_limit` / `network` / `guard_blocked` / `target_write` / `validation` / `probe_rejected` / `unknown` |

Example payload as sent:

```json
{
  "event": "source_auth_failed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "source_type": "jira",
    "auth_method": "token",
    "error_class": "auth",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `source_connected`

R4: a source is connected. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `source_type` | `folder` / `jira` / `other` |

Example payload as sent:

```json
{
  "event": "source_connected",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "source_type": "jira",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `target_connected`

Destination preference. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `target_type` | `folder` / `github` |

Example payload as sent:

```json
{
  "event": "target_connected",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "target_type": "github",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `dry_run_viewed`

Use of the trust preview. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `items_bucket` | `0` / `1-10` / `11-100` / `101-1000` / `1000+` |
| `would_delete_bucket` | `0` / `1-10` / `11-100` / `101-1000` / `1000+` |

Example payload as sent:

```json
{
  "event": "dry_run_viewed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "items_bucket": "11-100",
    "would_delete_bucket": "0",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `first_sync_completed`

Time to first file. Emitted from: engine.

| Property | Allowed values |
|---|---|
| `source_type` | `folder` / `jira` / `other` |
| `files_bucket` | `0` / `1-10` / `11-100` / `101-1000` / `1000+` |
| `duration_s` | seconds, 0 to 1,000,000, one decimal |
| `ms_since_first_open` | whole number, 0 to 3,000,000,000 |

Example payload as sent:

```json
{
  "event": "first_sync_completed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "source_type": "jira",
    "files_bucket": "11-100",
    "duration_s": 12.4,
    "ms_since_first_open": 241000,
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `error_shown`

Friction hotspots. Emitted from: ui.

| Property | Allowed values |
|---|---|
| `ui_area` | `home` / `editor` / `source` / `target` / `preview` / `activity` / `view` / `settings` / `onboarding` / `other` |
| `error_class` | `auth` / `rate_limit` / `network` / `guard_blocked` / `target_write` / `validation` / `probe_rejected` / `unknown` |

Example payload as sent:

```json
{
  "event": "error_shown",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "ui_area": "source",
    "error_class": "auth",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `help_opened`

Where users get confused. Emitted from: ui.

| Property | Allowed values |
|---|---|
| `topic_id` | `jira_token` / `github_signin` / `big_change_guard` / `target` / `agent_connect` / `other` |

Example payload as sent:

```json
{
  "event": "help_opened",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "topic_id": "jira_token",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `session_ended_before_first_sync`

Abandonment point. Emitted from: ui.

| Property | Allowed values |
|---|---|
| `last_step` | `consent` / `choose_source` / `authorize` / `choose_target` / `preview` / `first_sync` / `connect_agent` |

Example payload as sent:

```json
{
  "event": "session_ended_before_first_sync",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "last_step": "authorize",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```


## B. Value and habit

### `agent_snippet_copied`

The aha step was taken. Emitted from: ui.

| Property | Allowed values |
|---|---|
| `agent` | `claude_code` / `codex` / `cursor` / `other` |

Example payload as sent:

```json
{
  "event": "agent_snippet_copied",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "agent": "claude_code",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `agent_read`

R4+ aha and R5 habit: reads through MCP per day (a count, nothing else). Emitted from: mcp.

| Property | Allowed values |
|---|---|
| `reads_count` | whole number, 0 to 1,000,000 |

Example payload as sent:

```json
{
  "event": "agent_read",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "reads_count": 7,
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `first_agent_read`

Did the agent ever read the files, and how long after the first sync. Emitted from: mcp.

| Property | Allowed values |
|---|---|
| `ms_since_first_sync` | whole number, 0 to 3,000,000,000 |

Example payload as sent:

```json
{
  "event": "first_agent_read",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "ms_since_first_sync": 340000,
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `sync_completed`

'Always current' works. Emitted from: engine.

| Property | Allowed values |
|---|---|
| `trigger` | `schedule` / `manual` |
| `source_type` | `folder` / `jira` / `other` |
| `changed_bucket` | `0` / `1-10` / `11-100` / `101-1000` / `1000+` |
| `deleted_bucket` | `0` / `1-10` / `11-100` / `101-1000` / `1000+` |
| `duration_s` | seconds, 0 to 1,000,000, one decimal |

Example payload as sent:

```json
{
  "event": "sync_completed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "trigger": "manual",
    "source_type": "jira",
    "changed_bucket": "1-10",
    "deleted_bucket": "0",
    "duration_s": 3.2,
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `sync_failed`

Reliability as users see it. Emitted from: engine.

| Property | Allowed values |
|---|---|
| `error_class` | `auth` / `rate_limit` / `network` / `guard_blocked` / `target_write` / `validation` / `probe_rejected` / `unknown` |
| `source_type` | `folder` / `jira` / `other` |

Example payload as sent:

```json
{
  "event": "sync_failed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "error_class": "network",
    "source_type": "jira",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `guard_triggered`

Big-change friction. Emitted from: engine.

| Property | Allowed values |
|---|---|
| `cause` | `filter_change` / `scope_change` / `source_change` / `unknown` |

Example payload as sent:

```json
{
  "event": "guard_triggered",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "cause": "unknown",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `guard_resolved`

Big-change friction. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `cause` | `filter_change` / `scope_change` / `source_change` / `unknown` |
| `action` | `approved` / `cancelled` |

Example payload as sent:

```json
{
  "event": "guard_resolved",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "cause": "unknown",
    "action": "approved",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `view_mode_opened`

Do users check state. Emitted from: ui.

| Property | Allowed values |
|---|---|
| `entry_point` | `empty_state` / `editor` / `onboarding` / `home` / `activity` / `view` / `settings` / `other` |

Example payload as sent:

```json
{
  "event": "view_mode_opened",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "entry_point": "home",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `activity_viewed`

Do users check state. Emitted from: ui.

| Property | Allowed values |
|---|---|
| `entry_point` | `empty_state` / `editor` / `onboarding` / `home` / `activity` / `view` / `settings` / `other` |

Example payload as sent:

```json
{
  "event": "activity_viewed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "entry_point": "home",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `item_reason_viewed`

'Why is my file missing?' volume. Emitted from: ui.

Not emitted yet: ships with MD-122.

| Property | Allowed values |
|---|---|
| `reason` | `filtered` / `waiting_to_settle` / `deleted_in_source` |

Example payload as sent:

```json
{
  "event": "item_reason_viewed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "reason": "deleted_in_source",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```


## C. Expansion

### `source_added`

Expansion. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `source_type` | `folder` / `jira` / `other` |
| `n_sources` | whole number, 0 to 1,000 |

Example payload as sent:

```json
{
  "event": "source_added",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "source_type": "folder",
    "n_sources": 2,
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `source_toggled`

Expansion. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `state` | `on` / `off` |

Example payload as sent:

```json
{
  "event": "source_toggled",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "state": "off",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `source_removed`

Expansion. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `age_bucket` | `<1h` / `<1d` / `<7d` / `>=7d` |

Example payload as sent:

```json
{
  "event": "source_removed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "age_bucket": "<7d",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `pipeline_created`

Expansion. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `n_pipelines` | whole number, 0 to 1,000 |

Example payload as sent:

```json
{
  "event": "pipeline_created",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "n_pipelines": 1,
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```


## D. Intent

### `source_scope_edited`

Do users narrow scope today? (cheapest demand evidence). Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `source_type` | `folder` / `jira` / `other` |
| `kind` | `jql` / `glob` / `project` |
| `fields_used` | list of: `status` / `priority` / `issuetype` / `updated` / `created` / `labels` / `component` / `assignee` / `project` / `sprint` / `custom` |
| `clause_count` | whole number, 0 to 1,000 |
| `pattern_count` | whole number, 0 to 1,000 |
| `uses_order_by` | `true` / `false` |

Example payload as sent:

```json
{
  "event": "source_scope_edited",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "source_type": "jira",
    "kind": "jql",
    "fields_used": [
      "status",
      "updated"
    ],
    "clause_count": 2,
    "pattern_count": 0,
    "uses_order_by": true,
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `jira_project_pick`

Does the project picker cut the Jira step? (MD-141: method and a bucket, never a key, name or query). Emitted from: ui.

| Property | Allowed values |
|---|---|
| `method` | `picker` / `advanced_jql` |
| `count_bucket` | `0` / `1-10` / `11-100` / `101-1000` / `1000+` |

Example payload as sent:

```json
{
  "event": "jira_project_pick",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "method": "picker",
    "count_bucket": "1-10",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `folder_pick`

Do people browse for a folder or type it? (MD-142: a method, never a path or folder name). Emitted from: ui.

| Property | Allowed values |
|---|---|
| `method` | `browse` / `typed` / `recent` |

Example payload as sent:

```json
{
  "event": "folder_pick",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "method": "browse",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `branch_pick`

Do people choose a GitHub branch from the list or type it? (MD-143: a method, never a branch name). Emitted from: ui.

| Property | Allowed values |
|---|---|
| `method` | `picker` / `typed` |

Example payload as sent:

```json
{
  "event": "branch_pick",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "method": "picker",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```


## E. Product additions (install, agent, schedule, requests, notices)

### `install_completed`

Install funnel. Emitted from: cli.

| Property | Allowed values |
|---|---|
| `os` | `macos` / `linux` / `windows` / `other` |
| `install_method` | `script` / `pip` / `pipx` / `uv` / `source` / `other` |

Example payload as sent:

```json
{
  "event": "install_completed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "os": "macos",
    "install_method": "script",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `agent_connected`

The aha step: the agent is connected. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `agent` | `claude_code` / `codex` / `cursor` / `other` |
| `method` | `auto` / `manual` |

Example payload as sent:

```json
{
  "event": "agent_connected",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "agent": "claude_code",
    "method": "auto",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `try_started`

Taste with sample data. Emitted from: ui, cli.

No properties.

Example payload as sent:

```json
{
  "event": "try_started",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `try_completed`

Taste with sample data. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `duration_s` | seconds, 0 to 1,000,000, one decimal |

Example payload as sent:

```json
{
  "event": "try_completed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "duration_s": 4.5,
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `schedule_changed`

Is scheduled sync kept on. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `old_interval` | `off` / `15m` / `1h` / `6h` / `daily` |
| `new_interval` | `off` / `15m` / `1h` / `6h` / `daily` |

Example payload as sent:

```json
{
  "event": "schedule_changed",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "old_interval": "1h",
    "new_interval": "daily",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `source_requested`

Source demand (an explicit send: goes out even with sharing off). Emitted from: ui.

**An explicit send:** it goes out even when sharing is off, only when the user presses Send, and the live log labels it "goes to the team".

| Property | Allowed values |
|---|---|
| `source_id` | `confluence` / `notion` / `google_drive` / `github_issues` / `linear` / `slack` / `sharepoint` / `jira_data_center` / `other` |
| `other_source` | words the person typed and pressed Send on, up to 100 characters (explicit sends only; labelled "goes to the team") |
| `email` | an e-mail address the person typed next to "Email me if we add it" and pressed Send on (optional; explicit sends only) |

Example payload as sent:

```json
{
  "event": "source_requested",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "source_id": "other",
    "other_source": "Linear",
    "email": "dana@acme.dev",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `feedback_answered`

The why behind drop-offs (an explicit send). Emitted from: ui.

**An explicit send:** it goes out even when sharing is off, only when the user presses Send, and the live log labels it "goes to the team".

| Property | Allowed values |
|---|---|
| `question_id` | `first_sync_expectation` / `no_agent_read_3d` / `last_source_removed` |
| `answer` | `yes` / `partly` / `no` / `other` / `no_agent_installed` / `dont_know_how` / `tried_failed` / `no_time` / `files_not_useful` / `just_testing` / `wrong_source` / `not_useful` / `privacy_worry` / `broke` |
| `comment` | words the person typed and pressed Send on, up to 500 characters (explicit sends only; labelled "goes to the team") |

Example payload as sent:

```json
{
  "event": "feedback_answered",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "question_id": "first_sync_expectation",
    "answer": "partly",
    "comment": "Missing the comments",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `notice_shown`

Reliability notices users get. Emitted from: ui, engine.

| Property | Allowed values |
|---|---|
| `kind` | `sync_failed` / `source_missing` / `stale` / `guard_blocked` |

Example payload as sent:

```json
{
  "event": "notice_shown",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "kind": "sync_failed",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```

### `app_updated`

Update adoption. Emitted from: ui, cli.

| Property | Allowed values |
|---|---|
| `previous_version` | a release number such as `0.3.1` |

Example payload as sent:

```json
{
  "event": "app_updated",
  "distinct_id": "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d",
  "uuid": "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58",
  "timestamp": "2026-10-08T09:15:02Z",
  "properties": {
    "previous_version": "0.3.1",
    "app_version": "1.2.3",
    "deployment": "local",
    "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c",
    "is_internal": false,
    "$process_person_profile": false
  }
}
```
