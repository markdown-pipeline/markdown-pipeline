"""Configuration use-cases shared by the CLI and the UI (so both validate identically)."""
from __future__ import annotations

from .brain import Brain
from .config import Pipeline, load_config, save_config
from .destinations import bind_target
from .locking import brain_lock
from .state import State
from . import usage_hooks
from .validation import ValidationResult, validate_pipeline


class ValidationFailed(Exception):
    def __init__(self, result: ValidationResult) -> None:
        super().__init__("; ".join(i.message for i in result.errors))
        self.result = result


class NotFound(Exception):
    """No pipeline with that name."""


def create_pipeline(brain: Brain, pipeline: Pipeline) -> ValidationResult:
    """Validate and save a new pipeline. Raises ValidationFailed (nothing saved) on any error.

    Typed Jira credentials of the pipeline (MD-88) are checked against Jira and kept in the vault first; a failed save removes what was stored."""
    from . import jirasetup

    config = load_config(brain)
    created: list[str] = []
    if jirasetup.has_typed(pipeline):
        early = validate_pipeline(brain, config, jirasetup.with_placeholders(pipeline))      # the cheap checks before any secret is stored
        if not early.ok:
            raise ValidationFailed(early)
        created = jirasetup.apply_typed(pipeline)
    try:
        result = validate_pipeline(brain, config, pipeline, remote=True)
        if not result.ok:
            raise ValidationFailed(result)
        config.pipelines.append(pipeline)
        save_config(brain, config)
    except Exception:
        jirasetup.rollback(created)
        raise
    usage_hooks.pipeline_created(len(config.pipelines), pipeline)      # MD-116: content-free usage events; never raises
    return result


def update_pipeline(brain: Brain, name: str, pipeline: Pipeline, relabels: dict[str, str] | None = None, after_validation=None) -> ValidationResult:
    """Validate and replace pipeline ``name``. ``relabels`` ({old: new}) migrates ownership records of renamed folders.

    ``after_validation``: called once the new configuration has passed every check (Jira credentials included) and before anything
    is saved, e.g. to remove sources; if it raises, nothing of this update is kept (typed secrets are taken back out of the vault)."""
    config = load_config(brain)
    index = next((i for i, p in enumerate(config.pipelines) if p.name == name), None)
    if index is None:
        raise NotFound(name)
    pipeline.name = name
    from . import jirasetup

    created: list[str] = []
    if jirasetup.has_typed(pipeline):
        created = jirasetup.apply_typed(pipeline, config.pipelines[index])
    result = validate_pipeline(brain, config, pipeline, editing=name, remote=True)
    if not result.ok:
        jirasetup.rollback(created)
        raise ValidationFailed(result)
    if after_validation is not None:
        try:
            after_validation()
        except Exception:
            jirasetup.rollback(created)
            raise
    with brain_lock(brain):
        if relabels:
            state = State.load(bind_target(brain, config.pipelines[index]))
            for rec in state.pipeline(name).files.values():
                rec.source_label = relabels.get(rec.source_label, rec.source_label)
            state.save()
        previous = config.pipelines[index]
        config.pipelines[index] = pipeline
        save_config(brain, config)
    usage_hooks.pipeline_updated(previous, pipeline)                   # MD-116
    return result


def set_paused(brain: Brain, name: str, paused: bool, *, trigger: str) -> bool:
    """Pause or resume a pipeline. Logged only when the state actually changes. Returns whether it changed."""
    from .log import ActivityLog

    config = load_config(brain)
    pipeline = config.get(name)
    if pipeline is None:
        raise NotFound(name)
    if pipeline.paused == paused:
        return False
    pipeline.paused = paused
    save_config(brain, config)
    ActivityLog(brain).event("pause" if paused else "resume", name, trigger)
    return True


def set_schedule(brain: Brain, name: str, interval: str, *, trigger: str) -> bool:
    """Change how often a pipeline syncs by itself (MD-121). Returns whether it changed; a pipeline already on that interval is not touched."""
    from .config import SCHEDULE_VALUES

    if interval not in SCHEDULE_VALUES:
        raise ValueError(f"'{interval}' is not an interval. Choose one of: {', '.join(SCHEDULE_VALUES)}.")
    config = load_config(brain)
    pipeline = config.get(name)
    if pipeline is None:
        raise NotFound(name)
    if pipeline.schedule == interval:
        return False
    previous, pipeline.schedule = pipeline.schedule, interval
    save_config(brain, config)
    usage_hooks.schedule_changed(previous, interval)
    return True
