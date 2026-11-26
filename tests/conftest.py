"""Shared by every test: an in-memory Catalog (no database, no download), a home, config, and vault
of the test's own, a progress recorder, a test Postgres, the command runner, and stand-ins for a
terminal, the scheduler, `riffle status`, and the sync's network and steps."""

import io
import os
import pwd
import re
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from riffle import config, net
from riffle.cli import app
from riffle.ingest.remote import cardmarket, goatbots, mtgjson, pricelists, scryfall, scryfall_catalog, tcgcsv
from riffle.links import safe_name
from riffle.models import CardRules, CardView, Face, PricedPrinting, Prices, Printing
from riffle.ops import disk

CARDS = {
    # card_id: (name, layout, usd, tix)
    "o-sol": ("Sol Ring", "normal", 1.0, 0.05),
    "o-rift": ("Cyclonic Rift", "normal", 30.0, 2.0),
    "o-forest": ("Forest", "normal", 0.1, 0.01),
    "o-snowf": ("Snow-Covered Forest", "normal", 0.5, 0.02),
    "o-aesi": ("Aesi, Tyrant of Gyre Strait", "normal", 5.0, 0.3),
    "o-fire": ("Fire // Ice", "split", 1.0, 0.1),
    "o-rider": ("Murderous Rider // Swift End", "adventure", 2.0, None),
    "o-yshtola": ("Y'shtola, Night's Blessed", "normal", 3.0, None),
    "o-bolt": ("Lightning Bolt", "normal", 1.0, 0.02),
    "o-rats": ("Relentless Rats", "normal", 0.2, 0.01),
    "o-crypt": ("Mana Crypt", "normal", 150.0, 20.0),
    "o-thrasios": ("Thrasios, Triton Hero", "normal", 10.0, 1.0),
    "o-tymna": ("Tymna the Weaver", "normal", 12.0, 1.0),
    "o-dwarves": ("Seven Dwarves", "normal", 0.25, 0.01),
    "o-teferi": ("Teferi, Hero of Dominaria", "normal", 15.0, 3.0),
    "o-lurrus": ("Lurrus of the Dream-Den", "normal", 3.0, 0.4),
    "o-delver": ("Delver of Secrets // Insectile Aberration", "transform", 0.25, 0.02),
    "o-summon": ("Summon: Bahamut", "normal", 4.0, None),
    "o-anduril": ("Andúril, Flame of the West", "normal", 2.0, None),
}

_CMDR = {"commander": "legal", "duel": "legal"}
RULES = {
    # card_id: (legalities, color_identity, type_line, oracle_text)
    "o-sol": (
        {**_CMDR, "modern": "not_legal", "legacy": "banned", "vintage": "restricted"},
        (),
        "Artifact",
        "{T}: Add {C}{C}.",
    ),
    "o-rift": ({**_CMDR, "modern": "not_legal", "legacy": "legal"}, ("U",), "Instant", "Overload {6}{U}"),
    "o-forest": ({**_CMDR, "modern": "legal"}, ("G",), "Basic Land — Forest", "({T}: Add {G}.)"),
    "o-snowf": ({**_CMDR, "modern": "legal"}, ("G",), "Basic Snow Land — Forest", "({T}: Add {G}.)"),
    "o-aesi": (
        {**_CMDR, "modern": "legal"},
        ("U", "G"),
        "Legendary Creature — Serpent",
        "You may play an additional land on each of your turns.",
    ),
    "o-fire": ({**_CMDR, "modern": "legal"}, ("U", "R"), "Instant // Instant", "Fire\nIce"),
    "o-bolt": (
        {**_CMDR, "modern": "legal", "pioneer": "not_legal"},
        ("R",),
        "Instant",
        "Lightning Bolt deals 3 damage to any target.",
    ),
    "o-rats": (
        {**_CMDR, "modern": "legal"},
        ("B",),
        "Creature — Rat",
        "A deck can have any number of cards named Relentless Rats.",
    ),
    "o-crypt": (
        {"commander": "banned", "duel": "banned", "modern": "not_legal", "vintage": "restricted"},
        (),
        "Artifact",
        "At the beginning of your upkeep, flip a coin.",
    ),
    "o-thrasios": (
        {**_CMDR, "modern": "legal"},
        ("U", "G"),
        "Legendary Creature — Merfolk Wizard",
        "{4}: Scry 1. Partner",
    ),
    "o-tymna": (
        {**_CMDR, "modern": "legal"},
        ("W", "B"),
        "Legendary Creature — Human Cleric",
        "Lifelink. Partner",
    ),
    "o-dwarves": (
        {**_CMDR, "modern": "legal"},
        ("R",),
        "Creature — Dwarf",
        "A deck can have up to seven cards named Seven Dwarves.",
    ),
    "o-teferi": (
        {**_CMDR, "modern": "legal"},
        ("W", "U"),
        "Legendary Planeswalker — Teferi",
        "+1: Draw a card.",
    ),
    "o-lurrus": (
        {**_CMDR, "modern": "legal"},
        ("W", "B"),
        "Legendary Creature — Cat Nightmare",
        "Companion — Each permanent card in your starting deck has mana value 2 or less.",
    ),
}
FRONT = "https://cards.scryfall.io/normal/front"
VIEWS = {
    # card_id: what its note shows; any other card shows its rules and is pictured by "s-<id>"
    "o-fire": CardView(
        (
            Face("Fire", "{1}{R}", "Instant", "Fire deals 2 damage."),
            Face("Ice", "{1}{U}", "Instant", "Tap.\nDraw."),
        ),
        (("Fire // Ice", f"{FRONT}/s-fire.jpg?1662"),),
        "s-fire",
        "dmr",
        "215",
        599089,
    ),
    "o-delver": CardView(
        (
            Face("Delver of Secrets", "{U}", "Creature — Human Wizard", "Transform it.", "1/1"),
            Face("Insectile Aberration", "", "Creature — Human Insect", "Flying", "3/2"),
        ),
        (
            ("Delver of Secrets", f"{FRONT}/s-delver.jpg?1"),
            ("Insectile Aberration", "https://cards.scryfall.io/normal/back/s-delver.jpg?1"),
        ),
        "s-delver",
        "inr",
        "60",
    ),
    "o-crypt": CardView((Face("Mana Crypt", "{0}", "Artifact"),)),  # printed only in foil: no picture
}
PRINTINGS = {
    "s-sol-m3c": Printing("s-sol-m3c", "o-sol", "Sol Ring", "m3c", "283"),
    "s-rift-2x2": Printing("s-rift-2x2", "o-rift", "Cyclonic Rift", "2x2", "45"),
    # a precon box's printings, and others with their art or not (riffle.ingest.precons)
    "s-sol-fdc": Printing("s-sol-fdc", "o-sol", "Sol Ring", "fdc", "286", illustration_id="art-sol"),
    "s-sol-c21": Printing("s-sol-c21", "o-sol", "Sol Ring", "c21", "263", illustration_id="art-sol"),
    "s-sol-lea": Printing("s-sol-lea", "o-sol", "Sol Ring", "lea", "270", illustration_id="art-sol-lea"),
    "s-rift-fdc": Printing("s-rift-fdc", "o-rift", "Cyclonic Rift", "fdc", "100", illustration_id="art-rift"),
    "s-forest-fdc": Printing(
        "s-forest-fdc", "o-forest", "Forest", "fdc", "279", illustration_id="art-forest"
    ),
    "s-forest-m3c": Printing(
        "s-forest-m3c", "o-forest", "Forest", "m3c", "300", illustration_id="art-forest"
    ),
}


class FakeCatalog:
    day: date | None = date(2026, 9, 21)  # the Scryfall prices it holds

    def resolve(self, name):
        key = name.strip().lower()
        for oid, (n, *_) in CARDS.items():
            if n.lower() == key or n.lower().split(" // ")[0] == key.split(" // ")[0]:
                return oid
        for oid, (n, *_) in CARDS.items():
            if safe_name(n).lower() == " ".join(key.split()):
                return oid
        return None

    def name(self, oid):
        return CARDS[oid][0]

    def prices(self, card_ids):
        return {c: Prices(*CARDS[c][2:4]) for c in card_ids if c in CARDS}

    def prices_day(self):
        return self.day

    def price_printings(self, card_ids):
        """One printing a card, playable, its Scryfall ID "s-" and the card's."""
        return {c: [PricedPrinting(f"s-{c}", True, False)] for c in card_ids if c in CARDS}

    def printings(self, sids):
        return {s: PRINTINGS[s] for s in sids if s in PRINTINGS}

    def printings_at(self, places):
        at = {(p.set_code, p.collector_number): p for p in PRINTINGS.values()}
        return {(code, n): at[(code.lower(), n)] for code, n in places if (code.lower(), n) in at}

    def mtgo_name(self, oid):
        name, layout = CARDS[oid][:2]
        if " // " in name:
            return name.replace(" // ", "/") if layout == "split" else name.split(" // ")[0]
        return name

    def arena_rarity(self, oid):
        return {"o-sol": "uncommon", "o-rift": "mythic", "o-aesi": "rare"}.get(oid)

    def is_basic(self, oid):
        return CARDS[oid][0] in {"Forest", "Island", "Plains", "Swamp", "Mountain", "Wastes"}

    def rules(self, card_ids):
        return {c: CardRules(*RULES[c]) for c in card_ids if c in RULES}

    def card_views(self, card_ids):
        views = {}
        for c in card_ids:
            if c in VIEWS:
                views[c] = VIEWS[c]
            elif c in CARDS:
                name, (_, _, kind, text) = CARDS[c][0], RULES.get(c, ({}, (), "", ""))
                views[c] = CardView(
                    (Face(name, "", kind, text),), ((name, f"{FRONT}/s-{c}.jpg?7"),), f"s-{c}", "tst", "1"
                )
        return views


@pytest.fixture
def cat():
    return FakeCatalog()


@pytest.fixture
def opened(cat, monkeypatch):
    """open_catalog yields the in-memory catalog; the list records each time it's opened and closed."""
    from riffle.store import postgres

    events = []

    @contextmanager
    def open_catalog():
        events.append("open")
        yield cat
        events.append("close")

    monkeypatch.setattr(postgres, "open_catalog", open_catalog)
    return events


@pytest.fixture(autouse=True)
def isolated(tmp_path_factory, monkeypatch):
    """Every test gets its own home, config and data directories, a vault at ~/vault, and a
    configured database no one can reach, so nothing a test runs touches real files or the
    real database. Only the config is written: a test makes the folders it needs. A test that
    reads ~/.config or ~/.local/share in what Riffle prints takes bare_home; database tests use
    the pg fixtures below, and libpq still finds the password in the real ~/.pgpass."""
    home = tmp_path_factory.mktemp("home")
    (home / "config" / "riffle").mkdir(parents=True)
    (home / "config" / "riffle" / "config.toml").write_text(
        'vault = "~/vault"\nnotes = "games/tcg"\n'
        'database_url = "postgresql+psycopg://tcg@127.0.0.1:1/unreachable"\n'
        'card_images = "link"\n'  # no test fetches a card's picture unless it says so
    )
    if "PGPASSFILE" not in os.environ:  # the user's home by the password database, not by HOME
        try:
            real = Path(pwd.getpwuid(os.getuid()).pw_dir)
        except KeyError:  # a container's user with no entry: HOME, before it's changed below
            real = Path.home()
        monkeypatch.setenv("PGPASSFILE", str(real / ".pgpass"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    monkeypatch.setattr(disk, "WARN_BELOW", 0)  # the machine's own free space warns nowhere


@pytest.fixture
def bare_home(tmp_path, monkeypatch) -> Path:
    """HOME as the test's own folder, with the config and data folders where they go when no XDG
    variable names them (~/.config and ~/.local/share) and no config written: for a test that
    reads those paths in what Riffle prints. Returns the home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ".local" / "share"))
    return tmp_path


@pytest.fixture
def no_config(tmp_path, monkeypatch) -> None:
    """The config folder as the test's own empty one, so there is no config until the test
    writes it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


@pytest.fixture
def data_dir() -> Path:
    """Riffle's data folder in the test's own home; nothing has made it yet."""
    return config.data_dir()


# Where a vault at ~/vault keeps its Magic decks, from the home.
MTG = Path("vault", "games", "tcg", "mtg")


@pytest.fixture
def mtg() -> Path:
    """The folder the test's config reads Magic decks from, ~/vault/games/tcg/mtg; the test
    makes it."""
    return Path.home() / MTG


@pytest.fixture
def bare_mtg(bare_home) -> Path:
    """The same folder under bare_home, for a test that writes its own config there; the test
    makes it."""
    return bare_home / MTG


@pytest.fixture
def unreadable():
    """Takes a file's read permission for the test and gives it back after, so its folder can
    be cleaned up."""
    if os.geteuid() == 0:
        pytest.skip("root reads any file")
    taken: list[Path] = []

    def take(path: Path) -> Path:
        path.chmod(0)
        taken.append(path)
        return path

    yield take
    for path in taken:
        path.chmod(0o644)


# ---- terminals and times ---------------------------------------------------------------


class Terminal(io.StringIO):
    """A stream that says it's a terminal."""

    def isatty(self) -> bool:
        return True


@pytest.fixture
def denver():
    """The Mac's clock set to Denver's time (MDT in summer), for what the terminal shows."""
    with pytest.MonkeyPatch.context() as m:
        m.setenv("TZ", "America/Denver")
        time.tzset()
        yield
    time.tzset()


@pytest.fixture
def on_a_terminal(denver, monkeypatch):
    """riffle.times.shown thinks it prints to a terminal, in Denver."""
    from riffle import times

    monkeypatch.setattr(times, "sys", SimpleNamespace(stdout=Terminal()))


# ---- progress -----------------------------------------------------------------------


@dataclass
class RecordedStep:
    label: str
    total: int | None
    unit: str
    updates: list[tuple[int, int | None]] = field(default_factory=list)
    outcome: tuple[str, ...] | None = None  # ("ok", note), ("warn", note), ("fail", why), ("drop",)

    def update(self, done: int, total: int | None = None) -> None:
        self.updates.append((done, total))

    def ok(self, note: str = "") -> None:
        self.outcome = ("ok", note)

    def warn(self, note: str) -> None:
        self.outcome = ("warn", note)

    def fail(self, why: str) -> None:
        self.outcome = ("fail", why)

    def drop(self) -> None:
        self.outcome = ("drop",)


class Recorder:
    """A progress Tracker that keeps every step it was given, in order."""

    def __init__(self) -> None:
        self.steps: list[RecordedStep] = []

    def step(self, label: str, total: int | None = None, unit: str = "") -> RecordedStep:
        self.steps.append(RecordedStep(label, total, unit))
        return self.steps[-1]

    def outcomes(self) -> dict[str, tuple[str, ...] | None]:
        return {s.label: s.outcome for s in self.steps}


@pytest.fixture
def tracker() -> Recorder:
    return Recorder()


@pytest.fixture
def plain() -> Callable[[str], str]:
    """A command's output as its words alone: CI's terminal gets colour codes, and an error box
    wraps its text between │ borders at 80 columns, where a long path moves the breaks."""
    return lambda output: " ".join(re.sub(r"\x1b\[[0-9;]*m", "", output).replace("│", " ").split())


# ---- Postgres ------------------------------------------------------------------
# Database tests use their own database, tcg_test, dropped and recreated once per
# session and migrated to head. Each test then runs in a transaction that's rolled
# back, so tests never see each other's rows. No server reachable: the tests skip,
# unless RIFFLE_REQUIRE_POSTGRES is set (Linux CI), where they fail instead.

PG_TEST_URL = os.environ.get("RIFFLE_TEST_DATABASE_URL", "postgresql+psycopg://tcg@localhost:5432/tcg_test")


@pytest.fixture(scope="session")
def pg_engine():
    from sqlalchemy import create_engine, make_url, text
    from sqlalchemy.exc import OperationalError

    from riffle.db import migrate

    url = make_url(PG_TEST_URL)
    if not (url.database or "").endswith("_test"):
        pytest.fail(f"refusing to drop {url.database!r}: the test database's name must end in _test")
    admin = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3}
    )
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except OperationalError:
        if os.environ.get("RIFFLE_REQUIRE_POSTGRES"):
            raise
        pytest.skip(f"no Postgres at {url.render_as_string(hide_password=True)} (start it: riffle db up)")
    finally:
        admin.dispose()
    engine = create_engine(url)
    migrate.upgrade(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def backup_made(monkeypatch):
    """Each sync's database backup made at once: the database every test's config names can't be
    reached, so a test of the sync's other steps would see its backup fail."""
    from riffle.db import backup

    def made(url, where, at, **kwargs):
        return backup.Dump(where / backup.name("tcg", at), 0, 1)

    monkeypatch.setattr(backup, "backup", made)


@pytest.fixture
def pg(pg_engine):
    """A connection inside a transaction that's rolled back after the test."""
    with pg_engine.connect() as conn:
        trans = conn.begin()
        try:
            yield conn
        finally:
            trans.rollback()


# ---- Docker ----------------------------------------------------------------------------

NO_DOCKER = (
    "docker isn't on PATH, in /usr/local/bin, or in /opt/homebrew/bin: install Docker Engine (Linux)"
    " or OrbStack or Docker Desktop (macOS), and start it"
)


def docker_env() -> dict[str, str]:
    """The environment docker runs in: the test's, plus the user's own Docker config directory,
    where a Mac's docker finds the compose plugin (~/.docker/cli-plugins); every test's HOME is a
    temporary one, under which `docker compose` is an unknown command."""
    env = dict(os.environ)
    if "DOCKER_CONFIG" not in env:
        # a container's user with no entry: docker's system plugin directories serve
        with suppress(KeyError):
            env["DOCKER_CONFIG"] = str(Path(pwd.getpwuid(os.getuid()).pw_dir) / ".docker")
    return env


# ---- the command line ------------------------------------------------------------------


def invoke(*args: str, **kwargs):
    """Runs riffle with these arguments; the keywords are CliRunner.invoke's."""
    return CliRunner().invoke(app, list(args), **kwargs)


# ---- the scheduler ---------------------------------------------------------------------


class FakeLaunchctl:
    def __init__(self, loaded=False, bootstrap_rc=0, print_out=""):
        self.calls = []
        self.loaded = loaded
        self.bootstrap_rc = bootstrap_rc
        self.print_out = print_out

    def __call__(self, args):
        self.calls.append(args[1])
        verb = args[1]
        if verb == "bootout":
            rc, self.loaded = (0 if self.loaded else 3), False
            return subprocess.CompletedProcess(args, rc, "", "")
        if verb == "bootstrap":
            self.loaded = self.bootstrap_rc == 0
            err = "Bootstrap failed: 5: Input/output error"
            return subprocess.CompletedProcess(args, self.bootstrap_rc, "", err)
        if verb == "print":
            if self.loaded:
                return subprocess.CompletedProcess(args, 0, self.print_out, "")
            return subprocess.CompletedProcess(args, 113, "", "Could not find service")
        raise AssertionError(f"unexpected launchctl {verb}")


PRINT = """gui/501/gg.riffletcg.riffle-sync = {
	active count = 0
	path = /Users/pat/Library/LaunchAgents/gg.riffletcg.riffle-sync.plist
	state = not running
	program = /Users/pat/riffle/.venv/bin/riffle
	runs = 3
	last exit code = 0
	event triggers = {
		com.apple.launchd.calendarinterval = {
			state = active
		}
	}
}
"""


@pytest.fixture
def schedule_cli(tmp_path, monkeypatch):
    """`riffle` with the scheduler's own program faked: called with the fake, it returns a
    function that runs a command and gives its output."""
    from riffle.ops import schedule

    def faked(fake):
        install, status, remove = schedule.install, schedule.status, schedule.remove
        monkeypatch.setattr(
            schedule,
            "install",
            lambda times, job=schedule.SYNC: install(times, tmp_path / "riffle", fake, job=job),
        )
        monkeypatch.setattr(schedule, "status", lambda job=schedule.SYNC: status(fake, job=job))
        monkeypatch.setattr(schedule, "remove", lambda job=schedule.SYNC: remove(fake, job=job))
        return lambda *args: invoke(*args).output

    return faked


@pytest.fixture
def launchd_cli(monkeypatch, schedule_cli):
    """schedule_cli on a Mac; the plists land under the test's own home."""
    monkeypatch.setattr(sys, "platform", "darwin")
    return schedule_cli


# ---- riffle status ---------------------------------------------------------------------


@pytest.fixture
def quiet(monkeypatch):
    """`riffle status` at the time it's called with, its jobs and backup lines given without
    asking this machine, the jobs' as on a Mac."""
    from riffle.ops import status

    def at(now):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(status, "jobs_lines", lambda *_, **__: ["9 of 9 loaded; every last run exited 0"])
        monkeypatch.setattr(status, "backup_line", lambda *_, **__: "Time Machine to Backup Disk")
        monkeypatch.setattr(status.times, "now", lambda: now)

    return at


# ---- the sync --------------------------------------------------------------------------


@pytest.fixture
def prices_kept(monkeypatch) -> None:
    """The day's Scryfall prices kept already, 2026-09-26, so a sync reads no bulk file and its
    price step has nothing new to keep."""
    monkeypatch.setattr(scryfall, "snapshot_prices", lambda: (Path("/d/2026-09-26.jsonl.gz"), False))


# A deck note for the sync's vault half to read.
BURN_NOTE = "---\ngame: mtg\nformat: modern\n---\n\n## Moxfield import\n\n```\n4 Lightning Bolt\n```\n"


def write_burn(mtg: Path, note: str = BURN_NOTE) -> Path:
    """Writes the one deck note, modern/burn.md, under mtg/, which it returns."""
    (mtg / "modern").mkdir(parents=True)
    (mtg / "modern" / "burn.md").write_text(note)
    return mtg


@pytest.fixture
def burn_note() -> str:
    """The note one_deck writes; a test file whose deck is written another way gives its own."""
    return BURN_NOTE


@pytest.fixture
def one_deck(mtg, opened, prices_kept, burn_note) -> Path:
    """One deck note, modern/burn.md, in the vault the test's config names, with the catalog in
    memory and a price day kept, so a sync has its vault half to do. Returns mtg/."""
    return write_burn(mtg, burn_note)


class Clock:
    """time.monotonic and time.sleep for net.wait_online, with no real waiting."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def network(monkeypatch, up_after: float | None, down: tuple[str, ...] = ()) -> list[tuple[str, int]]:
    """The network comes up after up_after seconds (None: never), but for the hosts in down, whose
    names never resolve. Returns every address tried."""
    clock, tried = Clock(), []

    def create_connection(address, timeout):
        tried.append(address)
        if address[0] in down:
            raise socket.gaierror(8, "nodename nor servname provided, or not known")
        if up_after is None or clock.now < up_after:
            raise OSError("Network is unreachable")
        return socket.socket()

    monkeypatch.setattr(net.socket, "create_connection", create_connection)
    monkeypatch.setattr(net.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(net.time, "sleep", clock.sleep)
    return tried


# What an online sync does, in order: the Scryfall download and catalog load, then each price source.
ONLINE = [
    "refresh",
    "catalog",
    "mtgjson",
    "mtgjson 90 days",
    "goatbots",
    "goatbots years",
    "cardmarket",
    "cardkingdom",
    "manapool",
    "tcgcsv",
]


def online_steps(monkeypatch, calls, refresh_fails=False):
    """The online steps, recorded in calls, each reporting a step as the real one does."""

    def refresh(force, tracker):
        calls.append("refresh")
        step = tracker.step("Scryfall bulk data")
        if refresh_fails:
            step.fail("HTTP 503")
        else:
            step.ok("current")

    def watch(delay, tracker, always):
        calls.append("tcgcsv")
        tracker.step("tcgcsv mtg").ok("456 groups")
        snap = tcgcsv.Snapshot(day=date(2026, 9, 26), fetched=["mtg"], groups={"mtg": 456})
        return tcgcsv.Watched(asked=True, snap=snap)

    monkeypatch.setattr(scryfall, "refresh", refresh)
    monkeypatch.setattr(scryfall_catalog, "update", lambda tracker, force: calls.append("catalog"))
    monkeypatch.setattr(mtgjson, "watch", lambda tracker, always: calls.append("mtgjson"))
    monkeypatch.setattr(mtgjson, "snapshot", lambda tracker: calls.append("mtgjson 90 days"))
    monkeypatch.setattr(goatbots, "watch", lambda tracker, always: calls.append("goatbots"))
    monkeypatch.setattr(goatbots, "snapshot", lambda tracker: calls.append("goatbots years"))
    monkeypatch.setattr(cardmarket, "watch", lambda tracker, always: calls.append("cardmarket"))
    monkeypatch.setattr(pricelists, "watch", lambda lists, tracker: calls.append(lists[0].store))
    monkeypatch.setattr(tcgcsv, "watch", watch)


# ---- the closing line of a run written as plain lines ----------------------------------

EXITED = re.compile(r"\d\d:\d\d:\d\d  exited (\d) after \d+\.\ds")


def before_closing(out: str) -> str:
    """A run's last line before its closing line, which says it exited 1."""
    *_v, last, closed = out.rstrip("\n").split("\n")
    assert EXITED.fullmatch(closed).group(1) == "1"
    return last
