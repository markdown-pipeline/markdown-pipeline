"""MD-212 AC5: a run with HOME and XDG_CONFIG_HOME pointing at a sandbox never touches the real login item. The 2026-11-04 incident: a test sandbox had its
own HOME but inherited XDG_CONFIG_HOME, so `schedule background off` deleted the real ~/.config/autostart item."""
import os
import subprocess
import sys
from pathlib import Path

from mdpipeline import autostart


def test_every_test_runs_with_xdg_config_home_inside_the_sandbox_home(tmp_path):
    assert Path(os.environ["XDG_CONFIG_HOME"]).resolve().is_relative_to(tmp_path.resolve())
    assert autostart.item_path().resolve().is_relative_to(tmp_path.resolve())


def test_an_inherited_xdg_config_home_is_what_decides_where_the_item_goes(tmp_path, monkeypatch):
    """The hazard itself, written down: HOME alone is NOT enough on Linux."""
    monkeypatch.setattr(sys, "platform", "linux")
    other = tmp_path / "somebody-elses-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(other))
    assert autostart.item_path(home=tmp_path / "sandbox-home") == other / "autostart" / "mdpipeline-background.desktop"


def test_turning_background_off_in_a_sandboxed_process_leaves_a_real_login_item_alone(tmp_path):
    """A child process with HOME, XDG_CONFIG_HOME and the data dir all in the sandbox, while a 'real' item exists elsewhere."""
    real = tmp_path / "real-config" / "autostart" / "mdpipeline-background.desktop"
    real.parent.mkdir(parents=True)
    real.write_text("[Desktop Entry]\n")
    sandbox = tmp_path / "sandbox"
    env = {**os.environ, "HOME": str(sandbox), "XDG_CONFIG_HOME": str(sandbox / ".config"), "MDPIPELINE_DATA_DIR": str(sandbox / "data"),
           "MDPIPELINE_NO_BACKGROUND_START": "1", "MDPIPELINE_POSTHOG_KEY": ""}
    subprocess.run([sys.executable, "-m", "mdpipeline", "schedule", "background", "off"], env=env, capture_output=True, timeout=60, check=False)
    assert real.read_text() == "[Desktop Entry]\n"


def test_re_creating_the_login_item_writes_only_inside_the_sandbox(app_data, tmp_path):
    from mdpipeline import scheduler
    from mdpipeline.schedulestore import SchedulerStore

    with SchedulerStore().update() as data:
        data["background"].update({"enabled": True, "announced": True})
    scheduler.ensure_login_item()
    assert autostart.item_path().resolve().is_relative_to(tmp_path.resolve()) and autostart.item_path().is_file()
