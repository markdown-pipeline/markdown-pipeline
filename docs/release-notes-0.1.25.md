# Markdown Pipeline 0.1.25

**Markdown Pipeline 0.1.25.** Linux only for now: Windows and macOS are not supported in this release. Update with `mdpipeline update`. Nothing else to do.

## Changes

- **New: try it in one command, with no account and no token.** `mdpipeline recipe jira-hibernate-hhh` reads a public Jira project (Hibernate ORM) and writes it as Markdown files that your AI agent can read. It asks no setup questions (only the usage-data question, the first time you use the app). When it's done, it shows the next step: open the folder with Claude Code and try the example questions it prints.
    - `mdpipeline recipe list` shows the three examples: Atlassian Forge (`jira-ecosystem-frge`), Hibernate ORM (`jira-hibernate-hhh`) and Sakai (`jira-sakaiproject-sak`).
    - The first run reads the issues updated in the last 14 days (30 for Forge). Run the same command again any time to get what was added or updated since.
    - Files go to your Markdown folder if you run it inside one, or else to `~/mdpipeline-recipes`.
    - Remove it with `mdpipeline pipeline remove <id>`. The files stay; add `--purge` to delete them too.
- **New: read a public Jira Cloud site with no login.** When you add a Jira source, `--jira-public` reads a site that lets anyone read a project: give the site and a query, and no email or token. If the site stops allowing it, the app says so and changes nothing. The safety check that stops a sync which would delete too many files works the same as before.
- **Usage data, only if you chose "Share usage":** when you run a built-in example, we learn which of the three it was (never your site, project or query). When the example's first sync finishes, we also learn how many files it wrote (a range) and how long it took. The example's name is added to the events about that pipeline. Running an example doesn't count as syncing your own tool. See what-we-send.md.

## Usage data (only if you chose "Share usage")

- **New count:** `first_recipe_sync_completed`. A new field, `recipe_id` (the example's name, or `none` for your own sources; on `first_agent_read` it can also be `mixed` if the agent's first read covered more than one pipeline, including an example), is on `pipeline_created`, `source_added`, `source_connected`, `first_sync_completed`, `sync_completed`, `sync_failed` and `first_agent_read`. A new field, `auth_mode` (`login` or `public`), says how a Jira source is read. No file names, no paths, no site names and no content. If you did not choose "Share usage", nothing is sent.
