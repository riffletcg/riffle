"""Every launchd job is labelled under the project's domain: in the plist names, in what the
schedule commands print, and in status's job names."""

from pathlib import Path

from riffle.ops import schedule as sched
from riffle.ops import status
from riffle.watch import watching
from tests.conftest import PRINT, FakeLaunchctl

PREFIX = "gg.riffletcg.riffle"


def test_every_job_is_labelled_under_the_project_s_domain(tmp_path):
    assert sched.PREFIX == PREFIX
    assert (sched.SYNC.label, sched.TRICKLE.label, sched.LABEL) == (
        f"{PREFIX}-sync",
        f"{PREFIX}-mtgo",
        f"{PREFIX}-sync",
    )
    assert sched.watch_job("manapool").label == f"{PREFIX}-watch-manapool"
    assert sched.plist_path() == Path.home() / "Library" / "LaunchAgents" / f"{PREFIX}-sync.plist"
    assert sched.build([(7, 0)], tmp_path / "riffle", tmp_path / "sync.log")["Label"] == f"{PREFIX}-sync"


def test_status_names_each_job_by_what_follows_the_prefix():
    lines = status.jobs_lines(lambda job: sched.Status(True, False))
    assert "0 of 9 loaded; sync isn't; mtgo isn't; watch-cardkingdom isn't;" in lines[0]


def test_the_schedule_commands_name_the_jobs_by_the_new_labels(launchd_cli):
    run = launchd_cli(FakeLaunchctl(print_out=PRINT))
    stores = list(watching.STORES)
    agents = Path.home() / "Library" / "LaunchAgents"
    assert f"{PREFIX}-sync\n" in run("schedule", "set", "07:00")
    assert (agents / f"{PREFIX}-sync.plist").exists()
    assert f"{PREFIX}-mtgo\n" in run("schedule", "trickle")
    watched = run("schedule", "watch")
    assert all(f"{PREFIX}-watch-{store}\n" in watched for store in stores)
    assert sorted(p.name for p in agents.iterdir()) == sorted(
        [f"{PREFIX}-sync.plist", f"{PREFIX}-mtgo.plist", *(f"{PREFIX}-watch-{s}.plist" for s in stores)]
    )
    every = run("schedule")
    assert "com.keltzbm" not in every
    assert every.index(f"{PREFIX}-sync") < every.index(f"{PREFIX}-mtgo") < every.index(f"{PREFIX}-watch-")
    assert run("schedule", "remove") == f"removed {PREFIX}-sync\n"
    assert run("schedule", "trickle", "--remove") == f"removed {PREFIX}-mtgo\n"
    assert run("schedule", "watch", "--remove") == "".join(f"removed {PREFIX}-watch-{s}\n" for s in stores)
