"""MD-237: built-in recipes. A recipe is a public Jira project that ``mdpipeline recipe <id>`` syncs into Markdown in one command.

The allowlist is in code, not in user config: changing a window or adding a recipe is a release (the experiment needs the recipe fixed).
The window is pinned at the first run (a fixed date in the saved JQL), so a re-run never deletes issues that fell out of a rolling window.
Every printed string is taken from work/marketing/specs/2026-11-12_rc1-recipe-copy.md (marketing owns the wording)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

DEFAULT_FOLDER = "~/mdpipeline-recipes"                  # approved by marketing, 2026-11-13


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str                      # {PROJECT_NAME} in the copy
    site: str
    jql_project: str               # the project key
    window_days: int
    questions: tuple[str, str, str]


HHH_QUESTIONS = (
    "Here's my Hibernate stack trace: <paste your stack trace>. Is it a known issue? Is it fixed, and in which version?",
    "Which bugs in hibernate-core were reported in the last two weeks and are still open? Do any have a workaround in the comments?",
    "What was fixed for version 8.0.0.CR1 in these issues? Group it by component.",
)
RECIPES: dict[str, Recipe] = {r.id: r for r in (
    Recipe("jira-ecosystem-frge", "Atlassian Forge (FRGE)", "https://ecosystem.atlassian.net", "FRGE", 30, (       # questions: marketing's FRGE fallback
        "Is my Forge error a known issue? <paste your error>",
        "Which open bugs and which suggestions got the most votes in the last 30 days?",
        "What did Atlassian reply on webhook issues?")),
    Recipe("jira-hibernate-hhh", "Hibernate ORM (HHH)", "https://hibernate.atlassian.net", "HHH", 14, HHH_QUESTIONS),
    Recipe("jira-sakaiproject-sak", "Sakai (SAK)", "https://sakaiproject.atlassian.net", "SAK", 14, (           # questions: NOT in the spec, for marketing review
        "Here's the error my Sakai server shows: <paste your error>. Is it a known issue? Is it fixed, and in which version?",
        "Which bugs were reported in the last two weeks and are still open? Do any have a workaround in the comments?",
        "What was fixed in the last two weeks? Group it by tool.")),
)}


def today() -> date:
    return date.today()


def since_date(recipe: Recipe, on: date) -> date:
    return on - timedelta(days=recipe.window_days)


def pinned_jql(recipe: Recipe, on: date) -> str:
    """The recipe's JQL with its window fixed to a date (Jira's ``yyyy-MM-dd`` form)."""
    return f'project = {recipe.jql_project} AND updated >= "{since_date(recipe, on).isoformat()}"'


def pinned_since(jql: str) -> str:
    """The fixed date inside a saved recipe JQL."""
    found = re.search(r'updated >= "(\d{4}-\d{2}-\d{2})"', jql)
    return found.group(1) if found else ""


def listing(cmd: str) -> str:
    rows = [f"  {r.id:<24} {r.name:<24} issues updated in the last {r.window_days} days" for r in RECIPES.values()]
    return "Recipes you can run:\n" + "\n".join(rows) + f"\nRun one, for example: {cmd} recipe jira-hibernate-hhh"
