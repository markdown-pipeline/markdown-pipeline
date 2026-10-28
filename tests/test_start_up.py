"""MD-179 (W1) AC1: the app starts as a program, on every OS.

`python -m mdpipeline --help` runs the real entry point in a new process, so an import that only
works on one OS (the old module-level `fcntl`) fails here, not in a user's terminal.
"""
import subprocess
import sys


def test_python_dash_m_mdpipeline_help_starts_and_lists_the_commands():
    done = subprocess.run([sys.executable, "-m", "mdpipeline", "--help"], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    assert "usage:" in done.stdout.lower() and "mdpipeline" in done.stdout
    assert "Traceback" not in done.stderr
