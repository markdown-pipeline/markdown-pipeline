# Development reference

For people working on the app itself. The user guide is the [reference](reference.md).

## Decisions and assumptions (review items A1-A6, B1-B8)

| Item | Decision |
|---|---|
| A1 brain-edit detection | `content_hash` is of the source. Brain edits are detected by a separate hash of the full bytes last written (state file). Any byte change counts, including the provenance block. |
| A2 guard formula | count = planned deletions + overwrites of existing owned files (including normal source updates and forced overwrites); block iff `count / owned_total > threshold` **and** `count >= min_files`; `owned_total` = files the pipeline currently owns, including those of missing folders. In "sync all", a blocked pipeline does not stop the others. (PM-confirmed.) |
| A3 template semantics | see the table above. |
| A4 concurrency | brain-wide lock; the second sync writes nothing and returns `busy` (CLI exit 5). (PM-confirmed.) |
| A5 non-.md matches | skipped with reason, never renamed. (PM-confirmed.) |
| A6 re-used pipeline name | a new pipeline never re-owns files of a removed one. (PM-confirmed.) |
| B1 | MP-5 ACs are covered by the tests of MD-13 (crash safety, idempotence, incremental). |
| B2 | reference fixture: 500 files of 5 KB on a local disk, full sync asserted under 10 s (`tests/test_sync_copy.py`). |
| B3 | timestamps ISO 8601 UTC; `source_root` absolute as configured; `synced_at` changes only when the file is written. |
| B4 | no global registry; registering another directory creates an independent brain. The config inside the brain (`.mdpipeline/`) is bookkeeping, not brain content. |
| B5 | exit codes above. |
| B6 | UI live update within 3 s (polling). |
| B7 | the Definition of Done (MD-6) is applied to every story by `tests/test_data_loss.py` and the per-story tests. |
| B8 | Linux/macOS supported; target-collision checks are case-insensitive on every platform. |

Further assumptions: dry-runs are not written to the activity log; the guard also counts the deletions of
`pipeline edit --remove-source --delete-files`; a purge or folder-file deletion never deletes a file you edited in
the brain; a source file named with `..` inside its name is just a name; a `{source}` label containing `/`, `\`,
`.` or `..` is rejected at save time and refused again at plan time.

## Tests

```bash
cd app
uv run pytest                       # the full suite
uv run pytest tests/test_data_loss.py tests/test_data_loss_names.py   # the release gates on their own (MD-30, MD-41)
uv run pytest tests/test_release_gate_telemetry.py tests/test_release_gate_scheduler.py   # MD-126: no content in telemetry, decline = zero calls, scheduler never deletes
```

## Layout

```
src/mdpipeline/
  brain.py config.py validation.py template.py globs.py      configuration
  sources.py planner.py engine.py guard.py                   the engine (source-agnostic: planner takes SourceScan objects)
  frontmatter.py writer.py boundary.py state.py log.py locking.py
  naming.py                                                  file-name policies: slug, date, patterns, clash suffix (the separator constant lives here)
  recovery.py rebuild.py                                     state backup restore and rebuild from provenance
  removal.py service.py status.py                            use-cases shared by CLI, MCP and UI
  brainquery.py mcp_server.py snippets.py                    MCP
  schedule.py scheduler.py schedulestore.py notices.py       scheduled sync (MD-121): when a pipeline is due, the tick, settings, notices
  background.py autostart.py                                 the background process and its login item
  install_info.py lifecycle.py                               how the app was installed; update and uninstall (MD-117); scripts/install.sh
  cli.py                                                     command line
  ui_server.py uiapi.py uieditor.py uiactivity.py ui_static/ local web UI (stdlib HTTP server + vanilla JS)
tests/                               one test module per story area, plus the data-loss suite
```
