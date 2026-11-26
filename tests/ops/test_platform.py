"""On Linux without systemctl the schedule commands refuse before they write anything, status says
there's no scheduler yet, and db up names Docker for each platform."""

import re
import subprocess
from pathlib import Path

import pytest

from riffle.db import compose
from riffle.ops import schedule as sched
from riffle.ops import status
from tests.conftest import NO_DOCKER, invoke
from tests.ops.conftest import every_schedule_command, never, no_launchctl, on

NEEDS_SYSTEMD = "scheduling needs systemd on Linux"


@every_schedule_command
def test_schedule_commands_refuse_on_linux_without_systemctl(monkeypatch, plain, args):
    on(monkeypatch, "linux", None)
    for name in ("install", "status", "remove"):
        monkeypatch.setattr(sched, name, never)
    result = invoke("schedule", *args)
    assert result.exit_code == 2, result.output
    assert NEEDS_SYSTEMD in plain(result.output), result.output
    assert not (Path.home() / "Library").exists()


def test_status_says_there_is_no_scheduler_on_linux_without_systemctl(monkeypatch):
    on(monkeypatch, "linux", None)
    # what asking launchd would come to on Linux
    monkeypatch.setattr(sched, "status", no_launchctl)
    monkeypatch.setattr(status, "backup_line", lambda *_, **__: "none")
    result = invoke("status", "--days", "3")
    assert result.exit_code == 0, result.output
    assert re.search(r"^jobs\s+no scheduler on this platform yet$", result.output, re.M), result.output
    assert "unknown (launchctl)" not in result.output


def test_db_up_names_docker_for_linux_and_macos_when_none_is_on_path():
    with pytest.raises(FileNotFoundError) as caught:
        compose.up(run=lambda args: subprocess.CompletedProcess(args, 0), which=lambda _: None, places=())
    assert str(caught.value) == NO_DOCKER


@pytest.mark.parametrize("args", [["set"], ["trickle", "--bogus"]], ids=["no times", "an unknown option"])
def test_the_platform_is_checked_before_a_subcommand_s_arguments(monkeypatch, plain, args):
    on(monkeypatch, "linux", None)
    result = invoke("schedule", *args)
    assert result.exit_code == 2, result.output
    assert NEEDS_SYSTEMD in plain(result.output)
