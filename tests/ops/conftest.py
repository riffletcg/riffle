"""Shared by the scheduler's tests: the platform they run as, stand-ins for what mustn't be asked,
every schedule command, a job's log as the sync writes it, and a price list as a store keeps it."""

import gzip
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from riffle import config
from riffle.ingest.remote import pricelists
from riffle.ops import schedule

POSTGRES = (
    "can't reach Postgres at postgresql+psycopg://tcg@localhost:5432/tcg: connection refused"
    " — start it with: riffle db up"
)

every_schedule_command = pytest.mark.parametrize(
    "args",
    [
        [],
        ["show"],
        ["set", "07:00"],
        ["remove"],
        ["trickle"],
        ["trickle", "--remove"],
        ["watch"],
        ["watch", "--remove"],
    ],
    ids=lambda args: " ".join(args) or "bare",
)


def on(monkeypatch, platform: str, systemctl: str | None) -> None:
    """The tests' machine as `platform`, with systemctl on PATH at `systemctl` or not at all."""
    monkeypatch.setattr(sys, "platform", platform)
    which = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda cmd, *a, **k: systemctl if cmd == "systemctl" else which(cmd, *a, **k)
    )


def never(*args, **_):
    """Stands in for what a test must not ask."""
    raise AssertionError(f"asked, and nothing should be: {args}")


def no_launchctl(job: schedule.Job = schedule.SYNC) -> schedule.Status:
    """A job's status on a machine with no launchctl to ask."""
    raise FileNotFoundError("launchctl")


def job_log(job: schedule.Job, text: str) -> Path:
    """Writes a job's log where the schedule keeps it."""
    path = schedule.log_path(job)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def sync_run(
    k: int, code: int | None = 1, first: datetime = datetime(2026, 10, 13, 13, 2, 3, tzinfo=UTC)
) -> str:
    """Run k of the sync's twice-daily runs from `first`, as its log has it: the header, the
    Postgres error unless it exited 0, and the closing line unless code is None."""
    started = first + timedelta(days=k // 2, hours=9 * (k % 2))
    lines = [f"{started:%Y-%m-%d %H:%M:%S} UTC  riffle sync"]
    if code != 0:
        lines.append(POSTGRES)
    if code is not None:
        lines.append(f"{started + timedelta(seconds=1):%H:%M:%S}  exited {code} after 1.2s")
    return "".join(f"{line}\n" for line in lines)


# When a kept list was fetched, unless a test says: 13:51 in Seattle.
FETCHED = datetime(2026, 9, 27, 20, 51, 50, tzinfo=UTC)


def gz(path: Path, body: bytes, fetched: datetime | None = FETCHED) -> None:
    """Keeps body gzipped at path, the time it was fetched in the gzip's header, or none there."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body, mtime=int(fetched.timestamp()) if fetched else 0))


def ck(made: str | None = "2026-09-27 13:08:38") -> bytes:
    """A Card Kingdom list of one row, made when its stamp says, or with no stamp."""
    meta = {"created_at": made} if made else {}
    return json.dumps({"meta": meta, "data": [{"id": 1}]}).encode()


def kept_singles(*days: str) -> None:
    """Card Kingdom's singles kept whole under each day: made at 13:08 Pacific, fetched 43 minutes on."""
    for day in days:
        fetched = datetime.fromisoformat(f"{day}T13:51:50").replace(tzinfo=pricelists.PACIFIC)
        gz(
            config.data_dir() / "cardkingdom" / "daily" / day / "singles.json.gz",
            ck(f"{day} 13:08:38"),
            fetched,
        )
