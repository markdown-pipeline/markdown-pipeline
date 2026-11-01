"""MD-199 (registry R4): server.json is the MCP Registry entry. Marketing's listing text (MM-147, MD-195 comment 11073) is fixed here, and the entry must
resolve to the exact command a registry client runs: `uvx mdpipeline --install-method registry mcp`."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
SERVER = json.loads((APP / "server.json").read_text(encoding="utf-8")) if (APP / "server.json").exists() else {}
PROJECT = tomllib.loads((APP / "pyproject.toml").read_text(encoding="utf-8"))["project"]
NAME = "io.github.markdown-pipeline/mdpipeline"
SCHEMA_URL = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"


def test_the_entry_uses_the_current_registry_schema_and_the_agreed_name():
    assert SERVER["$schema"] == SCHEMA_URL and SERVER["name"] == NAME


def test_the_name_is_the_one_the_pypi_readme_proves_ownership_with():
    assert f"<!-- mcp-name: {NAME} -->" in (APP / "docs" / "pypi-readme.md").read_text(encoding="utf-8")


def test_the_listing_words_are_marketings():
    assert SERVER["title"] == "Markdown Pipeline"
    assert SERVER["description"] == "Your team's Jira as clean Markdown files, kept current for your AI agent. Local and read-only."
    assert SERVER["websiteUrl"] == "https://markdownpipeline.com/?utm_source=mcp-registry&utm_medium=listing"


def test_the_registry_length_limits_hold():
    assert len(SERVER["title"]) <= 100 and len(SERVER["description"]) <= 100 and len(SERVER["version"]) <= 255


def test_the_versions_are_the_one_in_pyproject():
    assert SERVER["version"] == PROJECT["version"] and SERVER["packages"][0]["version"] == PROJECT["version"]


def test_the_package_is_the_pypi_one_run_with_uvx_over_stdio():
    [package] = SERVER["packages"]
    assert (package["registryType"], package["identifier"], package["runtimeHint"], package["transport"]) == ("pypi", PROJECT["name"], "uvx", {"type": "stdio"})


def test_the_repository_is_the_public_one():
    assert SERVER["repository"] == {"url": "https://github.com/markdown-pipeline/markdown-pipeline", "source": "github"}


def test_no_environment_variable_is_declared():
    """An environment variable in server.json has no fixed value (MD-197 finding); the install method is a fixed argument instead."""
    assert "environmentVariables" not in SERVER["packages"][0]


def _arguments() -> list[str]:
    out = []
    for arg in SERVER["packages"][0]["packageArguments"]:
        out += [arg["name"], arg["value"]] if arg["type"] == "named" else [arg["value"]]
    return out


def test_the_package_arguments_are_install_method_registry_then_mcp_in_that_order():
    assert _arguments() == ["--install-method", "registry", "mcp"]


def test_every_argument_has_a_fixed_value_so_a_client_asks_the_person_for_nothing():
    for arg in SERVER["packages"][0]["packageArguments"]:
        assert arg.get("value") and not arg.get("isRequired")


def test_the_arguments_parse_as_the_registry_install_of_the_mcp_server():
    from mdpipeline import cli

    args = cli.build_parser().parse_args(_arguments())
    assert args.command == "mcp" and args.install_method == "registry" and args.brain is None


def test_the_entry_names_no_secret_path_or_personal_address():
    text = (APP / "server.json").read_text(encoding="utf-8")
    assert "@zigiwave" not in text and "/home/" not in text and "avi@" not in text
