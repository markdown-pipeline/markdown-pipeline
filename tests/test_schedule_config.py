"""MD-121 AC1: every pipeline has a sync interval (15m / 1h / 6h / daily / off), default 1h, kept per pipeline."""
import pytest

from mdpipeline.config import DEFAULT_SCHEDULE, SCHEDULE_VALUES, Pipeline, load_config, save_config


def test_the_default_interval_is_one_hour():
    assert DEFAULT_SCHEDULE == "1h"
    assert Pipeline.new("p", ["/x"]).schedule == "1h"


def test_the_allowed_intervals_are_the_five_buckets_of_the_event_allowlist():
    from mdpipeline.usage_events import INTERVAL

    assert set(SCHEDULE_VALUES) == set(INTERVAL.values)


def test_a_default_schedule_is_not_written_so_existing_configs_stay_byte_identical(brain, add_pipeline):
    add_pipeline(Pipeline.new("p", ["/x"]))
    assert "schedule" not in brain.config_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("value", ["15m", "6h", "daily", "off"])
def test_a_non_default_interval_round_trips(brain, add_pipeline, value):
    add_pipeline(Pipeline.new("p", ["/x"], schedule=value))
    assert load_config(brain).get("p").schedule == value


def test_a_config_without_schedule_loads_as_the_default(brain):
    brain.config_path.write_text("version: 1\npipelines:\n- name: p\n  sources:\n  - {path: /x, label: x}\n", encoding="utf-8")
    assert load_config(brain).get("p").schedule == "1h"


def test_an_unknown_interval_in_the_file_falls_back_to_the_default(brain):
    brain.config_path.write_text("version: 1\npipelines:\n- name: p\n  schedule: every-blue-moon\n  sources:\n  - {path: /x, label: x}\n", encoding="utf-8")
    assert load_config(brain).get("p").schedule == "1h"
