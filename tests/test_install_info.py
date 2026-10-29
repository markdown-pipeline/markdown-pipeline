"""MD-117: what the app knows about how it was installed (fixed enums only, for install_completed and `update`)."""
import pytest

from mdpipeline import install_info
from mdpipeline.usage_events import INSTALL_METHOD, OS_NAME


@pytest.mark.parametrize("platform,expected", [("darwin", "macos"), ("linux", "linux"), ("linux2", "linux"), ("win32", "windows"), ("cygwin", "windows"), ("freebsd13", "other")])
def test_the_os_is_one_of_the_fixed_names(platform, expected):
    assert install_info.os_name(platform) == expected and expected in OS_NAME.values


@pytest.mark.parametrize("prefix,expected", [
    ("/home/a/.local/share/uv/tools/mdpipeline", "uv"),
    ("/Users/a/.local/share/uv/tools/mdpipeline", "uv"),
    ("C:\\Users\\a\\AppData\\Roaming\\uv\\tools\\mdpipeline", "uv"),
    ("/home/a/.local/pipx/venvs/mdpipeline", "pipx"),
    ("/Users/a/.local/share/pipx/venvs/mdpipeline", "pipx"),
    ("/usr/lib/python3.12", "pip"),
])
def test_the_install_method_is_read_from_where_the_app_lives(prefix, expected, monkeypatch):
    monkeypatch.delenv("MDPIPELINE_INSTALL_METHOD", raising=False)
    assert install_info.install_method(prefix=prefix, source_checkout=False) == expected and expected in INSTALL_METHOD.values


def test_a_checkout_of_the_source_is_source(monkeypatch):
    monkeypatch.delenv("MDPIPELINE_INSTALL_METHOD", raising=False)
    assert install_info.install_method(prefix="/x/app/.venv", source_checkout=True) == "source"


def test_the_install_script_says_so_and_wins(monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "script")
    assert install_info.install_method(prefix="/home/a/.local/share/uv/tools/mdpipeline", source_checkout=False) == "script"


def test_an_unknown_value_in_the_environment_is_ignored_not_trusted(monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "my secret path /home/a")
    assert install_info.install_method(prefix="/usr/lib/python3.12", source_checkout=False) == "pip"


def test_the_real_environment_gives_a_valid_answer():
    assert install_info.install_method() in INSTALL_METHOD.values and install_info.os_name() in OS_NAME.values


def test_the_install_script_leaves_a_marker_in_the_data_directory_that_is_read_later(app_data, monkeypatch):
    monkeypatch.delenv("MDPIPELINE_INSTALL_METHOD", raising=False)
    assert install_info.record_install_method("script") is True
    assert (app_data.dir / "install-method").read_text(encoding="utf-8").strip() == "script"
    assert install_info.install_method(prefix="/home/a/.local/share/uv/tools/mdpipeline", source_checkout=False) == "script"


def test_a_marker_with_anything_else_is_ignored(app_data, monkeypatch):
    monkeypatch.delenv("MDPIPELINE_INSTALL_METHOD", raising=False)
    (app_data.dir / "install-method").write_text("/home/user/secret", encoding="utf-8")
    assert install_info.install_method(prefix="/usr/lib/python3.12", source_checkout=False) == "pip"


def test_recording_an_unknown_method_is_refused(app_data):
    assert install_info.record_install_method("whatever") is False
    assert not (app_data.dir / "install-method").exists()


def test_the_install_note_command_records_the_method_and_is_hidden_from_help(app_data, capsys):
    from mdpipeline import cli

    assert cli.main(["install-note", "script"]) == 0 and (app_data.dir / "install-method").read_text().strip() == "script"
    assert cli.main(["install-note", "nonsense"]) == cli.EXIT_USAGE
    try:
        cli.main(["--help"])
    except SystemExit:
        pass
    assert "install-note" not in capsys.readouterr().out


# --- MD-196/197 (marketing MM-147): one command, chosen by how the app was installed -------------------------------------------
@pytest.mark.parametrize("method,expected", [("uvx", "uvx mdpipeline"), ("registry", "uvx mdpipeline"), ("uv", "mdpipeline"), ("script", "mdpipeline"),
                                             ("pipx", "mdpipeline"), ("pip", "mdpipeline"), ("source", "mdpipeline"), ("other", "mdpipeline")])
def test_the_command_to_show_depends_on_the_install_method(method, expected):
    assert install_info.command_prefix(method) == expected


def test_the_command_to_show_defaults_to_the_runtime_install_method(monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "registry")
    assert install_info.command_prefix() == "uvx mdpipeline"
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "uv")
    assert install_info.command_prefix() == "mdpipeline"
