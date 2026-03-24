import json
import os
import pytest
from typer.testing import CliRunner
from cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def cli_home(tmp_path, monkeypatch):
    """Set CLI data directory to tmp for all tests."""
    monkeypatch.setenv("AGENTBOSS_HOME", str(tmp_path))
    return tmp_path


class TestLogin:
    def test_login_saves_key(self, cli_home):
        # Use a real-format nsec (but we'll mock validation)
        result = runner.invoke(app, ["login", "--key", "aa" * 32])
        assert result.exit_code == 0
        key_file = cli_home / "identity.json"
        assert key_file.exists()

    def test_login_invalid_key_rejected(self):
        result = runner.invoke(app, ["login", "--key", "tooshort"])
        assert result.exit_code != 0 or "Invalid" in result.stdout


class TestWhoami:
    def test_whoami_no_key(self):
        result = runner.invoke(app, ["whoami"])
        assert "No identity" in result.stdout or result.exit_code != 0

    def test_whoami_with_key(self, cli_home):
        runner.invoke(app, ["login", "--key", "aa" * 32])
        result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 0
        assert "npub" in result.stdout


class TestConfig:
    def test_config_set_and_show(self, cli_home):
        runner.invoke(app, ["config", "set", "relay", "ws://localhost:7777"])
        result = runner.invoke(app, ["config", "show"])
        assert "ws://localhost:7777" in result.stdout

    def test_config_set_max_jobs(self, cli_home):
        runner.invoke(app, ["config", "set", "max-jobs", "50"])
        result = runner.invoke(app, ["config", "show"])
        assert "50" in result.stdout


class TestRegionsList:
    def test_regions_list_empty(self, cli_home):
        result = runner.invoke(app, ["regions", "list"])
        assert result.exit_code == 0


class TestListJobs:
    def test_list_empty(self, cli_home):
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No jobs" in result.stdout or result.stdout.strip() == ""


class TestProfile:
    def test_profile_show_no_identity(self, cli_home):
        """Show profile without identity should fail."""
        result = runner.invoke(app, ["profile", "show"])
        assert "No identity" in result.stdout or result.exit_code != 0

    def test_profile_set_saves_profile(self, cli_home):
        """Profile set saves profile locally."""
        runner.invoke(app, ["login", "--key", "aa" * 32])
        result = runner.invoke(app, ["profile", "set", "--name", "Alice", "--bio", "Dev"])
        assert result.exit_code == 0
        assert "Profile saved" in result.stdout

    def test_profile_show_local(self, cli_home):
        """Show profile after setting."""
        runner.invoke(app, ["login", "--key", "aa" * 32])
        runner.invoke(app, ["profile", "set", "--name", "Alice", "--bio", "Developer"])
        result = runner.invoke(app, ["profile", "show"])
        assert result.exit_code == 0
        assert "Alice" in result.stdout
        assert "Developer" in result.stdout

    def test_profile_show_no_profile(self, cli_home):
        """Show profile when none set."""
        runner.invoke(app, ["login", "--key", "aa" * 32])
        result = runner.invoke(app, ["profile", "show"])
        assert "No profile set" in result.stdout or result.exit_code != 0


class TestSubmit:
    def test_submit_fails_without_login(self, cli_home):
        """submit without login shows error."""
        result = runner.invoke(app, ["submit", "abc123", "--message", "Hello"])
        assert result.exit_code != 0
        assert "identity" in result.stdout.lower() or "login" in result.stdout.lower()

    def test_submit_fails_for_unknown_job(self, cli_home):
        """submit for unknown job shows error."""
        runner.invoke(app, ["login", "--key", "aa" * 32])
        result = runner.invoke(app, ["submit", "nonexistent", "--message", "Hello"])
        assert result.exit_code != 0
        assert "not found" in result.stdout.lower() or "run" in result.stdout.lower()

    def test_submit_command_exists(self, cli_home):
        """submit command is registered."""
        result = runner.invoke(app, ["submit", "--help"])
        assert result.exit_code == 0
        assert "JOB_ID" in result.output
