# Markdown Pipeline 0.1.27
Markdown Pipeline 0.1.27. Linux only for now: Windows and macOS are not supported in this release. Update with `mdpipeline update`. Nothing else to do.
## Changes
- New: `mdpipeline status` and `mdpipeline schedule` now tell you when a pipeline that has a schedule will not sync by itself. If background sync isn't serving that output folder, it says so and gives you the one command to run. If the last sync was more than twice the schedule's interval ago, it says the pipeline is overdue and how to restart background sync or sync now. Before this, such a pipeline could show "next sync now" for weeks and give no reason. (MD-240)
## Usage data (only if you chose "Share usage")
- No change: nothing new is sent.
