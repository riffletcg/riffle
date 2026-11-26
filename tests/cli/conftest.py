"""Shared by the command-line tests: a command's output a line at a time, and deck notes written
into the vault."""

from collections.abc import Callable, Iterable
from pathlib import Path

from tests.conftest import invoke


def lines(*args: str, code: int = 0) -> list[str]:
    """The lines riffle prints for these arguments, once it has exited with `code`."""
    result = invoke(*args)
    assert result.exit_code == code, result.output
    return result.output.splitlines()


def write_decks(mtg: Path, notes: Iterable[dict], text: Callable[[dict], str]) -> None:
    """Writes each deck note, a dict naming its folder and slug, under mtg/; `text` gives a
    note's words."""
    for note in notes:
        path = mtg / note["folder"] / f"{note['slug']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text(note), encoding="utf-8")
