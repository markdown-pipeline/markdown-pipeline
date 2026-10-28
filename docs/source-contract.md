# The source contract (MD-45)

A **source** turns a location (a folder, a Jira site, ...) into a list of items the engine copies into the output folder. The engine (planner,
guard, ownership, naming, writer, state, recovery) knows only this contract (`src/mdpipeline/sources.py`). `LocalFolderSource` is the
folder implementation; a connector registers itself with `register_source_type(name, factory)` and a pipeline source with
`type: name` (plus its `options`) is built by `adapter_for`. `tests/fake_source.py` is an in-memory connector that runs the engine
through this contract (`tests/test_source_contract.py`), which is the proof that a new source type needs no engine change.

## What a source provides
`adapter.scan(include, exclude, known) -> SourceScan`, where `SourceScan` has `label`, `path` (shown to the user and recorded as
`source_root`), `status`, `error`, `kind`, `guard_empty`, `warnings` and `entries`, each a `SourceEntry`:

| Field | Meaning |
|---|---|
| `identity` | the **stable identity** of the item (default: `relpath`). Ownership, names and provenance follow it, so a title change or a move does not make a new item. For Jira it is the issue id. |
| `relpath` | the display path under the source: feeds `{relpath}`, `{dir}`, `{name}` and may change over time. |
| `mtime` / `created` | modified and **created** dates (epoch seconds). `created`, when the source carries it, is the date used by the `{date}` of a file-name policy (a date the source carries wins over the first-seen date); the first-seen date, once recorded, is never changed. |
| `version` | an opaque change marker (Jira: the `updated` stamp plus the renderer version). When it equals the version recorded for the file the engine does **not** fetch the content ("changed since"). Empty means "always read the content". |
| `content` | the bytes, or a callable that fetches them lazily (called only for new or changed items). |
| `provenance` | extra provenance keys written next to the standard ones (`source: <kind>`, site, issue key, URL, ...). Never content, never secrets. |
| `status` / `reason` | `candidate`, or `skip` with a reason. |
| `known` (argument) | `{identity: recorded version}` so a connector can avoid fetching what did not change. |
| `guard_empty` | when true, a **successful** scan that returns zero items for a source that owns files always asks the guard (a wrong query or lost access must not delete everything silently). |
| `warnings` | a count of conversion warnings (never content), shown in the plan, status and log. |

## A full identity listing
`scan` lists **every** current item (identities and versions), even those that did not change: that listing is how deletions and scope
exits are detected. An owned item whose identity is absent from a complete, successful listing is "gone" and follows the pipeline's
`on_source_delete` (`delete` / `keep`), subject to the guard.

## Error semantics: a failure is never read as "item deleted"
A source that cannot be read completely (network, auth, a failing page, a malformed answer, a time-out) returns `status` `unreadable`
(or `missing`) with a message and **no entries**; an adapter that raises is turned into the same by `scan_source`. The planner then keeps
every file of that source ("source unreadable; nothing deleted"), reports the error, and the other sources of the pipeline still sync.
A connector must never return a partial list as a success.

## Identity versus path
If the same identity now maps to a different target (an item moved to another folder: `{dir}` changed), a connector item is **renamed**
(one plan item; its content and name are kept), not deleted and created again. The file name is decided once and kept (MD-35), so a title
change alone never renames. Folder sources keep their previous behaviour.

## Adding a connector
Implement `scan`, call `register_source_type`, import the module from `mdpipeline/connectors.py`. The planner, guard, writer, naming and
state code is not touched; the folder source still runs through the same contract and the whole earlier test suite passes unchanged.
