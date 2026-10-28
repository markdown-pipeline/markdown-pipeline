"""MD-169 AC5: every test starts inside a sandbox: the app's data dir, HOME and login items are temporary, nothing can reach PostHog and
the background process cannot be started. (conftest.py sets it up; this test pins it. The leak guard in conftest.py fails any test that
still reaches the real home.)"""
import os
from pathlib import Path

from conftest import REAL_HOME


def test_a_test_runs_with_a_temporary_data_dir_home_and_login_item_folder(tmp_path):
    from mdpipeline import autostart, usage, vault

    for path in (vault.data_dir(), Path.home(), autostart.item_path(), usage.data_dir()):
        assert tmp_path in path.parents, f"{path} is outside the test's temporary folder"
    assert Path.home() != REAL_HOME


def test_a_test_cannot_reach_the_analytics_host_or_start_the_background_process():
    from mdpipeline import autostart, usage

    assert usage.get_transport() is None
    assert os.environ["MDPIPELINE_POSTHOG_KEY"] == ""
    assert autostart.start_now() is False
