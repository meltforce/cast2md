"""The installed CLI must identify the same release as the package metadata."""

from importlib.metadata import version

from click.testing import CliRunner

from cast2md.cli import cli


def test_cli_version_matches_installed_distribution():
    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0, result.output
    assert result.output.strip().endswith(f"version {version('cast2md')}")
