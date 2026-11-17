# Markdown Pipeline 0.1.26
Markdown Pipeline 0.1.26. Linux only for now: Windows and macOS are not supported in this release. Update with `mdpipeline update`. Nothing else to do.
## Changes
- Fixed: the progress line no longer says "0 of None". While the first Jira sync is still counting your issues, it now reads "Reading issues from Jira… 0 so far", and "N of M" once the total is known. Writing files says "Writing files… 0 so far" the same way. (MD-246, wording approved by marketing)
- Fixed, only if you chose "Share usage": creating a pipeline from a built-in example (`mdpipeline recipe`) no longer counts as you changing what is read. When you edit the query of an example's pipeline yourself, we now also learn which example it was (its name only, never your site, project or query). For your own pipelines, the example name is sent as `none`. See what-we-send.md. (MD-243)
## Usage data (only if you chose "Share usage")
- `source_scope_edited` is no longer sent when `mdpipeline recipe` creates its pipeline. When it is sent, it has a new field, `recipe_id`: the example's name, or `none` for your own sources. No file names, no paths, no site names and no content. If you did not choose "Share usage", nothing is sent.
