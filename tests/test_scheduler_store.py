"""MD-121: the scheduler's own small file in the app data directory (never inside a brain)."""
from mdpipeline.schedulestore import SchedulerStore


def test_a_missing_file_reads_as_defaults(app_data):
    s = SchedulerStore().read()
    assert s["background"] == {"enabled": True, "announced": False, "acknowledged": False} and s["brains"] == [] and s["runtime"] == {}


def test_update_persists_across_instances(app_data):
    with SchedulerStore().update() as data:
        data["brains"].append("/b")
    assert SchedulerStore().read()["brains"] == ["/b"]


def test_a_failed_update_saves_nothing(app_data):
    try:
        with SchedulerStore().update() as data:
            data["brains"].append("/b")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert SchedulerStore().read()["brains"] == []


def test_a_corrupt_file_reads_as_defaults_instead_of_crashing_the_background_process(app_data):
    (app_data.dir / "scheduler.json").write_text("{not json", encoding="utf-8")
    assert SchedulerStore().read()["brains"] == []


def test_the_file_is_in_the_app_data_directory(app_data):
    with SchedulerStore().update():
        pass
    assert (app_data.dir / "scheduler.json").exists()
