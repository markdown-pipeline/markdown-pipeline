"""MD-237: run a built-in recipe: find or create the brain, add the pipeline once (window pinned), sync, and print the next steps.

All printed text comes from work/marketing/specs/2026-11-12_rc1-recipe-copy.md §1 and §2."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from . import progress, recipes
from .brain import Brain, BrainError, find_brain, init_brain
from .config import Pipeline, Source, load_config
from .engine import sync_pipeline
from .recipes import Recipe
from .service import ValidationFailed, create_pipeline

BAR = 20


def _brain(args) -> tuple[Brain, str]:
    """The current brain if there is one, else the default recipes folder (created, no questions)."""
    try:
        brain = find_brain(args.brain or os.getcwd())
        return brain, f"Using your Markdown folder: {brain.root}"
    except BrainError:
        brain = init_brain(Path(recipes.DEFAULT_FOLDER).expanduser(), create=True)
        return brain, f"Created a folder for recipes: {brain.root}"


def _pipeline(brain: Brain, recipe: Recipe) -> tuple[Pipeline, bool]:
    """The recipe's pipeline: the saved one (its pinned window is never moved), or a new one."""
    existing = load_config(brain).get(recipe.id)
    if existing is not None:
        return existing, False
    options = {"site": recipe.site, "jql": recipes.pinned_jql(recipe, recipes.today()), "auth_mode": "public", "recipe_id": recipe.id}
    source = Source(recipe.site, "jira", type="jira", options=options)
    pipeline = Pipeline.new(recipe.id, [source], target=f"{recipe.id}/{{relpath}}")
    create_pipeline(brain, pipeline)
    return pipeline, True


def duration(seconds: int) -> str:
    """"(57s)" under a minute, "(1m 05s)" from a minute up."""
    return f"({seconds}s)" if seconds < 60 else f"({seconds // 60}m {seconds % 60:02d}s)"


def _claude_step(folder: Path) -> str:
    """One line on macOS/Linux; two on Windows (PowerShell 5.1 has no `&&`)."""
    if sys.platform == "win32":
        return f'  cd "{folder}"\n  claude'
    return f'  cd "{folder}" && claude'


def _bar(done: int, total: int) -> str:
    filled = BAR * done // total if total else 0
    return "[" + "#" * filled + "-" * (BAR - filled) + f"]  {done}/{total} issues"


class _Progress:
    """Shows the issue count on a terminal (one line, rewritten); remembers where it stopped for the "Stopped after" message."""

    def __init__(self) -> None:
        self.done = self.total = 0
        self.tty = sys.stdout.isatty()

    def __call__(self, state: dict) -> None:
        if state["phase"] == "reading" and state["total"]:
            self.done, self.total = state["done"], state["total"]
            if self.tty:
                print("\r" + _bar(self.done, self.total), end="", flush=True)


def _failure(recipe: Recipe, category: str, message: str, shown: _Progress, cmd: str, failed: int = 0) -> str:
    host = recipe.site.split("//", 1)[1]
    if category == "rate-limited":
        return f"{host} is limiting requests right now. Wait a few minutes, then run the same command again."
    if category == "auth":
        return f"{host} no longer lets anyone read {recipe.jql_project} without a login, so this recipe can't run. Try another one: {cmd} recipe list"
    if shown.total and failed:
        return f"Stopped after {max(shown.total - failed, 0)} of {shown.total} issues. Run the same command again to finish."
    if category == "network":
        return f"Couldn't reach {host}. Check your internet connection, then run the same command again."
    return message


def run_recipe(args, recipe: Recipe, cmd: str) -> int:
    brain, brain_line = _brain(args)
    print(brain_line)
    shown = _Progress()
    try:
        pipeline, first_run = _pipeline(brain, recipe)
    except ValidationFailed as exc:                  # the first look at the site (a count) failed: say why in the recipe's words
        issue = exc.result.errors[0]
        print(_failure(recipe, issue.code.removeprefix("jira-"), issue.message, shown, cmd), file=sys.stderr)
        return 1
    jql = pipeline.sources[0].options["jql"]
    window = f"the last {recipe.window_days} days" if first_run else "the window set at your first run"      # a re-run's pinned date is older than N days
    print(f"Syncing {recipe.name}: issues updated since {recipes.pinned_since(jql)} ({window}).\n"
          "No login needed: this is a public tracker, read directly by your machine.")
    progress.listen(shown)
    started = time.monotonic()
    try:
        progress.start("listing", noun="issues", source=recipe.name)
        result = sync_pipeline(brain, recipe.id, trigger="cli")
    finally:
        progress.finish()
        progress.listen(None)
    if shown.tty:
        print()
    if result.result not in ("ok", "conflicts"):
        print(_failure(recipe, result.category, result.message, shown, cmd, len(result.errors)), file=sys.stderr)
        return 1
    folder = brain.root / recipe.id
    count = sum(1 for _ in folder.rglob("*.md"))
    seconds = int(time.monotonic() - started)
    print(f"\nDone: {count} files in {folder}  {duration(seconds)}\n\n"
          "Next: ask Claude Code about them.\n"
          f"{_claude_step(folder)}\n"
          "Try:\n" + "\n".join(f"  • {q}" for q in recipe.questions) + "\n\n"
          "Run the same command again any time to get new and updated issues.\n"
          f"Remove it: {cmd} pipeline remove {recipe.id}   (add --purge to delete the files too)\n\n"
          f"Do the same for your team's Jira: {cmd} start")
    return 0
