"""`riffle compare` and `riffle similar`: two lists measured a zone at a time, and a deck's nearest."""

import os
import re
from pathlib import Path

import pytest

from riffle.store import postgres
from tests.cli.conftest import lines, write_decks
from tests.conftest import CARDS, invoke

EXTRA = {
    # the plan's fixture cards conftest lacks: an accent, a dot, the example's burn cards and a basic
    "x-jotun": ("Jötun Grunt", "normal", 0.5, None),
    "x-cerberus": ("Summon G.F. Cerberus", "normal", 0.5, None),
    "x-guide": ("Goblin Guide", "normal", 2.0, 0.1),
    "x-swiftspear": ("Monastery Swiftspear", "normal", 0.5, 0.05),
    "x-eidolon": ("Eidolon of the Great Revel", "normal", 3.0, 0.2),
    "x-skewer": ("Skewer the Critics", "normal", 0.5, 0.02),
    "x-mountain": ("Mountain", "normal", 0.1, 0.01),
}

NOTE = """---
game: mtg
format: {fmt}
{colors}---

## Moxfield import

```
{cards}
```
"""

BURN_A = {
    "folder": "modern",
    "slug": "burn-a",
    "main": "4 Lightning Bolt\n4 Goblin Guide\n4 Monastery Swiftspear\n2 Fire // Ice\n"
    "1 Summon G.F. Cerberus\n20 Mountain",
    "side": "2 Jötun Grunt\n1 Fire // Ice",
}
BURN_B = {
    "folder": "modern",
    "slug": "burn-b",
    "main": "4 Lightning Bolt\n4 Eidolon of the Great Revel\n4 Skewer the Critics\n1 Fire // Ice\n"
    "1 Summon G.F. Cerberus\n20 Mountain",
    "side": "2 Jötun Grunt\n1 Cyclonic Rift",
}
RATS_A = {
    "folder": "commander",
    "slug": "rats-a",
    "format": "commander",
    "colors": "[B]",
    "commander": "1 Y'shtola, Night's Blessed",
    "main": "10 Relentless Rats\n1 Sol Ring",
}
RATS_B = {**RATS_A, "slug": "rats-b", "main": "10 Relentless Rats\n1 Mana Crypt"}
RATS_C = {**RATS_A, "slug": "rats-c", "commander": "1 Teferi, Hero of Dominaria", "main": "1 Cyclonic Rift"}
DWARVES = {**RATS_C, "slug": "dwarves", "colors": "[R]", "main": "7 Seven Dwarves"}

BURN_MAIN = "4 Lightning Bolt\n4 Goblin Guide\n4 Monastery Swiftspear\n4 Fire // Ice\n20 Mountain"
BURN = {"folder": "modern", "slug": "burn", "main": BURN_MAIN}
# nearest to burn, in the order the plan sorts them: main-deck swaps, then Jaccard higher first, then name;
# the folders put the two at 0.88 in the other order on disk; above each, its swaps and Jaccard
NEAR = [
    # 1, 0.94
    {"folder": "modern", "slug": "burn-plus", "main": f"{BURN_MAIN}\n1 Skewer the Critics"},
    # 1, 0.88
    {
        "folder": "old",
        "slug": "burn-eidolon",
        "main": BURN_MAIN.replace("4 Fire", "3 Fire") + "\n1 Eidolon of the Great Revel",
    },
    # 1, 0.88
    {
        "folder": "new",
        "slug": "burn-skewer",
        "main": BURN_MAIN.replace("4 Fire", "3 Fire") + "\n1 Skewer the Critics",
    },
    # 2, 0.89
    {
        "folder": "modern",
        "slug": "burn-two",
        "main": f"{BURN_MAIN}\n1 Skewer the Critics\n1 Eidolon of the Great Revel",
    },
    # 8, 0.33
    {
        "folder": "modern",
        "slug": "red-prowess",
        "main": "4 Lightning Bolt\n4 Eidolon of the Great Revel\n4 Skewer the Critics\n4 Fire // Ice\n"
        "20 Mountain",
    },
]

EXAMPLE = [
    "burn-a (A) against burn-b (B), basic lands aside",
    "             main  sideboard",
    "  shared        6          2",
    "  only in A     9          1",
    "  only in B     8          1",
    "  swaps         9          1",
    "  Jaccard    0.26       0.50",
    "  A in B     0.40       0.67",
    "  B in A     0.43       0.67",
    "verdict: no line measured for modern yet",
    "only in A, main: Fire // Ice; 4 Goblin Guide; 4 Monastery Swiftspear",
    "only in B, main: 4 Eidolon of the Great Revel; 4 Skewer the Critics",
    "only in A, sideboard: Fire // Ice",
    "only in B, sideboard: Cyclonic Rift",
]


@pytest.fixture
def catalog(opened, monkeypatch):
    """The in-memory catalog behind open_catalog, resolving the plan's fixture cards too."""
    for oid, card in EXTRA.items():
        monkeypatch.setitem(CARDS, oid, card)
    return opened


def _list(note):
    parts = [f"Commander\n{note['commander']}"] if "commander" in note else []
    parts.append(f"Deck\n{note['main']}")
    if "side" in note:
        parts.append(f"Sideboard\n{note['side']}")
    return "\n\n".join(parts)


def _text(note):
    colors = f"colors: {note['colors']}\n" if "colors" in note else ""
    return NOTE.format(fmt=note.get("format", "modern"), colors=colors, cards=_list(note))


def _vault(mtg, *notes):
    """The test config's vault holding these deck notes. Each note is a dict: folder, slug, main,
    and any of side, commander, format (modern unless said), colors."""
    write_decks(mtg, notes, _text)


def without(note, key):
    return {k: v for k, v in note.items() if k != key}


CELL = re.compile(r"\S+(?: \S+)*")


def cells(line):
    """A table line's cells: words a single space apart make one cell, cells are two or more apart."""
    return [m.group() for m in CELL.finditer(line)]


def check_columns(table, right):
    """The plan's column rule: in a column in `right` every cell ends where the others do (right-aligned),
    in another every cell starts together, and each column starts two spaces past the widest cell of
    the one before. A line with fewer cells (the compare table's header, no label) fills from the right."""
    width = max(len(cells(line)) for line in table)
    columns = [[] for _ in range(width)]
    for line in table:
        spans = [(m.start(), m.end()) for m in CELL.finditer(line)]
        for column, span in zip(columns[width - len(spans) :], spans, strict=True):
            column.append(span)
    for i, column in enumerate(columns):
        edges = {end for _, end in column} if i in right else {start for start, _ in column}
        assert len(edges) == 1, (i, column)
    for before, after in zip(columns, columns[1:], strict=False):
        assert max(end for _, end in before) + 2 == min(start for start, _ in after)


# `riffle compare`: the header, a column a zone, each labeled line with its values, shares to two decimals,
# the verdict line, and the card lines in reading order with the count before a card held more than once more
def test_compare_prints_the_table_the_verdict_and_the_card_lines(mtg, catalog):
    _vault(mtg, BURN_A, BURN_B)
    assert lines("compare", "burn-a", "burn-b") == EXAMPLE
    # needs the catalog, as `own` does: once
    assert catalog == ["open", "close"]


# each table line is two spaces, the label, then a value per zone, right-aligned, each column two spaces
# past the widest cell above or below it; the labels call the decks A and B, as the first line names them,
# so a long name doesn't widen the table
def test_values_are_right_aligned_and_columns_two_spaces_past_the_widest_cell(mtg, catalog):
    _vault(mtg, BURN_A, {**BURN_B, "slug": "goblin-charbelcher-burn"})
    out = lines("compare", "burn-a", "goblin-charbelcher-burn")
    assert out[0] == "burn-a (A) against goblin-charbelcher-burn (B), basic lands aside"
    table = out[1:9]
    assert cells(table[0]) == ["main", "sideboard"]
    assert [cells(line)[0] for line in table[1:]] == [
        "shared",
        "only in A",
        "only in B",
        "swaps",
        "Jaccard",
        "A in B",
        "B in A",
    ]
    assert all(line.startswith("  ") and line[2] != " " for line in table[1:])
    check_columns(table, right={1, 2})


# the sideboard's column only when a list has one: neither here, so the main column alone and no
# sideboard card lines
def test_without_sideboards_the_table_has_the_main_column_alone(mtg, catalog):
    _vault(mtg, without(BURN_A, "side"), without(BURN_B, "side"))
    out = lines("compare", "burn-a", "burn-b")
    assert cells(out[1]) == ["main"]
    assert [cells(line) for line in out[2:9]] == [
        ["shared", "6"],
        ["only in A", "9"],
        ["only in B", "8"],
        ["swaps", "9"],
        ["Jaccard", "0.26"],
        ["A in B", "0.40"],
        ["B in A", "0.43"],
    ]
    assert out[9:] == EXAMPLE[9:12]


# a sideboard in one list alone still gets the column: — where Definition 2 leaves no value, and "none"
# for the list with nothing only in it
def test_a_sideboard_in_one_list_shows_the_column_with_dashes_and_none(mtg, catalog):
    _vault(mtg, BURN_A, without(BURN_B, "side"))
    out = lines("compare", "burn-a", "burn-b")
    assert cells(out[1]) == ["main", "sideboard"]
    assert [cells(line)[1:] for line in out[2:9]] == [
        ["6", "0"],
        ["9", "3"],
        ["8", "0"],
        ["9", "3"],
        ["0.26", "—"],
        ["0.40", "—"],
        ["0.43", "—"],
    ]
    assert out[12:] == [
        "only in A, sideboard: Fire // Ice; 2 Jötun Grunt",
        "only in B, sideboard: none",
    ]


# `compare` takes a path to a .txt list (MTGO form: a blank line before the sideboard) or a .md note, as `own`
# does; a double-faced card written by its front face is listed by its whole name
def test_compare_takes_a_path_to_a_txt_or_md_list(mtg, tmp_path, catalog):
    _vault(mtg, BURN_A, BURN_B)
    path = tmp_path / "list.txt"
    path.write_text(
        "4 Lightning Bolt\n4 Goblin Guide\n2 Delver of Secrets\n20 Mountain\n\n2 Jötun Grunt\n",
        encoding="utf-8",
    )
    out = lines("compare", "burn-a", str(path))
    assert out[0] == "burn-a (A) against list (B), basic lands aside"
    assert [cells(line)[1:] for line in out[2:9]] == [
        ["8", "2"],
        ["7", "1"],
        ["2", "0"],
        ["7", "1"],
        ["0.47", "0.67"],
        ["0.53", "0.67"],
        ["0.80", "1.00"],
    ]
    assert out[10:] == [
        "only in A, main: 2 Fire // Ice; 4 Monastery Swiftspear; Summon G.F. Cerberus",
        "only in B, main: 2 Delver of Secrets // Insectile Aberration",
        "only in A, sideboard: Fire // Ice",
        "only in B, sideboard: none",
    ]
    assert lines("compare", "burn-a", str(mtg / "modern" / "burn-b.md")) == EXAMPLE


# the same deck twice compares as equal: 0 swaps, every share 1, nothing only in either
def test_the_same_deck_twice_is_equal(mtg, catalog):
    _vault(mtg, BURN_A)
    out = lines("compare", "burn-a", "burn-a")
    assert out[0] == "burn-a (A) against burn-a (B), basic lands aside"
    assert [cells(line) for line in out[2:9]] == [
        ["shared", "15", "3"],
        ["only in A", "0", "0"],
        ["only in B", "0", "0"],
        ["swaps", "0", "0"],
        ["Jaccard", "1.00", "1.00"],
        ["A in B", "1.00", "1.00"],
        ["B in A", "1.00", "1.00"],
    ]
    assert out[10:] == [
        "only in A, main: none",
        "only in B, main: none",
        "only in A, sideboard: none",
        "only in B, sideboard: none",
    ]


# the commander's zone isn't measured, but names the commander for the verdict: the same 99 under another
# commander is 0 swaps and a variant by the 10-swap line; the same commander reads the 20-swap line; and
# two lists in different formats get none
def test_the_commander_names_the_deck_and_is_not_counted(mtg, catalog):
    teferi = {**RATS_A, "slug": "rats-teferi", "commander": "1 Teferi, Hero of Dominaria"}
    _vault(mtg, RATS_A, teferi, RATS_B, BURN_A)
    out = lines("compare", "rats-a", "rats-teferi")
    assert cells(out[5]) == ["swaps", "0"]
    assert out[9:] == [
        "verdict: a variant: 10 swaps or fewer under another commander",
        "only in A, main: none",
        "only in B, main: none",
    ]
    assert (
        lines("compare", "rats-a", "rats-b")[9]
        == "verdict: a variant: 20 swaps or fewer under the same commander"
    )
    assert lines("compare", "rats-a", "burn-a")[9] == "verdict: different formats: commander and modern"


# fails for a name that is no deck note, with its line on standard error and exit 1; `similar` the same way
@pytest.mark.parametrize(
    "command", [["compare", "burn-a", "nope"], ["compare", "nope", "burn-a"], ["similar", "nope"]]
)
def test_an_unknown_name_fails_with_its_line(mtg, catalog, plain, command):
    _vault(mtg, BURN_A)
    result = invoke(*command)
    assert result.exit_code == 1
    assert f"no deck note named 'nope' under {mtg}" in plain(result.stderr)


# needs the catalog, as `own` does: without one, exit 1 saying what to run
@pytest.mark.parametrize("command", [["compare", "burn-a", "burn-a"], ["similar", "burn-a"]])
def test_without_a_catalog_the_commands_say_what_to_run(mtg, monkeypatch, command):
    _vault(mtg, BURN_A, BURN_B)

    def open_catalog():
        raise postgres.Unavailable("no Magic cards in Postgres yet — run: riffle ingest scryfall")

    monkeypatch.setattr(postgres, "open_catalog", open_catalog)
    result = invoke(*command)
    assert result.exit_code == 1
    assert "no Magic cards in Postgres yet — run: riffle ingest scryfall" in result.output


# `riffle similar`: the header, the columns, — in the verdict column where no line was measured, and the
# order: main-deck swaps, then Jaccard higher first, then name (burn-eidolon before burn-skewer, though
# burn-skewer's note comes first on disk)
def test_similar_sorts_by_swaps_then_jaccard_then_name(mtg, catalog):
    _vault(mtg, BURN, *NEAR)
    out = lines("similar", "burn")
    assert out[0] == "nearest to burn: modern; 5 of 5 decks, basic lands aside"
    assert [cells(line) for line in out[1:]] == [
        ["swaps", "Jaccard", "verdict", "deck"],
        ["1", "0.94", "—", "burn-plus"],
        ["1", "0.88", "—", "burn-eidolon"],
        ["1", "0.88", "—", "burn-skewer"],
        ["2", "0.89", "—", "burn-two"],
        ["8", "0.33", "—", "red-prowess"],
    ]
    check_columns(out[1:], right={0, 1})


# `similar` keeps to the deck's format: a legacy and a commander note are not among a modern deck's nearest
def test_similar_keeps_to_the_format(mtg, catalog):
    _vault(mtg, BURN, NEAR[0], NEAR[1], {**NEAR[3], "format": "legacy"}, RATS_A)
    out = lines("similar", "burn")
    assert out[0].startswith("nearest to burn: modern; ") and out[0].endswith(" decks, basic lands aside")
    assert [cells(line)[-1] for line in out[2:]] == ["burn-plus", "burn-eidolon"]


# `similar` keeps to the colors of a Commander deck whose note names them, says them in the header, and
# gives the verdict column by the line: variant, or another deck
def test_similar_keeps_to_a_commander_decks_colors(mtg, catalog):
    duel = {**RATS_B, "slug": "rats-duel", "format": "duel"}
    _vault(mtg, RATS_A, RATS_B, RATS_C, DWARVES, duel, BURN)
    out = lines("similar", "rats-a")
    assert out[0].startswith("nearest to rats-a: commander, B; ")
    assert [cells(line) for line in out[1:]] == [
        ["swaps", "Jaccard", "verdict", "deck"],
        ["1", "0.83", "variant", "rats-b"],
        ["11", "0.00", "another deck", "rats-c"],
    ]


# a Commander deck whose note names no colors is compared with every deck in its format
def test_a_commander_deck_without_colors_is_compared_across_its_format(mtg, catalog):
    plain_rats = {**without(RATS_A, "colors"), "slug": "rats-plain"}
    _vault(mtg, plain_rats, RATS_B, DWARVES, BURN)
    out = lines("similar", "rats-plain")
    assert out[0].startswith("nearest to rats-plain: commander; ")
    assert [cells(line)[-1] for line in out[2:]] == ["rats-b", "dwarves"]


# --limit: the first N, 10 unless said, at least 1; the header counts the shown against the decks compared
def test_similar_honors_limit(mtg, catalog):
    rats = [
        {"folder": "modern", "slug": f"burn-{n:02d}", "main": f"{BURN_MAIN}\n{n} Relentless Rats"}
        for n in range(1, 12)
    ]
    _vault(mtg, BURN, *rats)
    out = lines("similar", "burn")
    assert out[0] == "nearest to burn: modern; 10 of 11 decks, basic lands aside"
    assert [cells(line)[0] for line in out[2:]] == [str(n) for n in range(1, 11)]
    assert [cells(line)[-1] for line in out[2:]] == [f"burn-{n:02d}" for n in range(1, 11)]
    two = lines("similar", "burn", "--limit", "2")
    assert two[0] == "nearest to burn: modern; 2 of 11 decks, basic lands aside"
    assert [cells(line)[-1] for line in two[2:]] == ["burn-01", "burn-02"]
    assert invoke("similar", "burn", "--limit", "0").exit_code == 2


# the side column, the sideboard's swaps, only when the deck or any of the others has a sideboard
def test_the_side_column_shows_only_when_a_sideboard_exists(mtg, catalog):
    _vault(mtg, BURN, NEAR[0], {**BURN, "slug": "burn-sided", "side": "3 Jötun Grunt"})
    out = lines("similar", "burn")
    assert [cells(line) for line in out[1:]] == [
        ["swaps", "Jaccard", "side", "verdict", "deck"],
        ["0", "1.00", "3", "—", "burn-sided"],
        ["1", "0.94", "0", "—", "burn-plus"],
    ]
    check_columns(out[1:], right={0, 1, 2})


# no other deck to compare: one line, in these colors or in this format, exit 0
@pytest.mark.parametrize(
    ("notes", "deck", "said"),
    [
        ([BURN, RATS_A], "burn", "no other modern deck in this format"),
        ([RATS_A, DWARVES, BURN], "rats-a", "no other commander deck in these colors"),
        ([without(RATS_A, "colors"), BURN], "rats-a", "no other commander deck in this format"),
    ],
)
def test_no_other_deck_says_so_and_exits_0(mtg, catalog, notes, deck, said):
    _vault(mtg, *notes)
    assert lines("similar", deck) == [said]


# reads the deck notes and the catalog and writes nothing: no note, no store file, no sync step
def test_compare_and_similar_write_nothing(mtg, catalog):
    _vault(mtg, BURN_A, BURN_B)
    before = sorted(Path.home().rglob("*"))
    lines("compare", "burn-a", "burn-b")
    lines("similar", "burn-a")
    assert sorted(Path.home().rglob("*")) == before
    assert not Path(os.environ["XDG_DATA_HOME"]).exists()


# Fixed after review: a .txt list has no format, so compare reads it in the note's: a Modern note against
# a list gets Modern's verdict, not "different formats"
def test_a_txt_list_is_read_in_the_other_decks_format(mtg, tmp_path, catalog):
    _vault(mtg, BURN_A)
    path = tmp_path / "list.txt"
    path.write_text("4 Lightning Bolt\n4 Goblin Guide\n20 Mountain\n", encoding="utf-8")
    assert "verdict: no line measured for modern yet" in lines("compare", "burn-a", str(path))


# Fixed after review: `similar` on a list with no format compares it with every deck note, and says so
def test_similar_on_a_list_without_a_format_reads_every_deck_note(mtg, tmp_path, catalog):
    _vault(mtg, BURN_A, BURN_B, RATS_A)
    path = tmp_path / "list.txt"
    path.write_text("4 Lightning Bolt\n4 Goblin Guide\n20 Mountain\n", encoding="utf-8")
    out = lines("similar", str(path))
    assert out[0] == "nearest to list: any format; 3 of 3 decks, basic lands aside"
    assert {cells(line)[-1] for line in out[2:]} == {"burn-a", "burn-b", "rats-a"}
    assert {cells(line)[-2] for line in out[2:]} == {"—"}


# Fixed after review: with no deck note at all, a list with no format has nothing to be compared with
def test_similar_on_a_list_without_a_format_and_no_notes_says_so(mtg, tmp_path, catalog):
    _vault(mtg)
    path = tmp_path / "list.txt"
    path.write_text("4 Lightning Bolt\n", encoding="utf-8")
    assert lines("similar", str(path)) == ["no deck note to compare it with"]


# Fixed after review: `similar` leaves out the target's own note by its path, so a list given by path keeps
# the note it shares a name with, here at 0 swaps
def test_similar_leaves_out_only_the_target_note_itself(mtg, tmp_path, catalog):
    _vault(mtg, RATS_A, RATS_B)
    path = tmp_path / "rats-a.txt"
    path.write_text(_list(RATS_A) + "\n", encoding="utf-8")
    out = lines("similar", str(path))
    assert [cells(line)[-1] for line in out[2:]] == ["rats-a", "rats-b"]
    assert cells(out[2])[0] == "0"
    assert [cells(line)[-1] for line in lines("similar", "rats-a")[2:]] == ["rats-b"]


# similar keeps to the same colors, not a subset of them; a Constructed deck's colors don't filter, and a
# Duel Commander deck's do
def test_similar_needs_the_same_colors_and_only_for_commander_formats(mtg, catalog):
    wider = {**RATS_B, "slug": "rats-br", "colors": "[B, R]"}
    burn_red = {**BURN_A, "colors": "[R]"}
    burn_blue = {**BURN_B, "colors": "[U]"}
    duel_b = {**RATS_A, "folder": "duel", "slug": "duel-b", "format": "duel"}
    duel_b2 = {**RATS_B, "folder": "duel", "slug": "duel-b2", "format": "duel"}
    duel_r = {**RATS_C, "folder": "duel", "slug": "duel-r", "format": "duel", "colors": "[R]"}
    _vault(mtg, RATS_A, RATS_B, wider, burn_red, burn_blue, duel_b, duel_b2, duel_r)
    assert [cells(line)[-1] for line in lines("similar", "rats-a")[2:]] == ["rats-b"]
    assert [cells(line)[-1] for line in lines("similar", "burn-a")[2:]] == ["burn-b"]
    assert [cells(line)[-1] for line in lines("similar", "duel-b")[2:]] == ["duel-b2"]


# The verdict reads the main deck alone, in compare and in similar's column: a Commander list's sideboard,
# however different, doesn't move it
def test_the_verdict_reads_the_main_deck_alone(mtg, catalog):
    side_a = {**RATS_A, "side": "4 Lightning Bolt\n4 Goblin Guide\n4 Monastery Swiftspear\n4 Fire // Ice"}
    side_b = {**RATS_B, "side": "1 Cyclonic Rift\n1 Mana Crypt"}
    _vault(mtg, side_a, side_b)
    out = lines("compare", "rats-a", "rats-b")
    assert cells(out[5]) == ["swaps", "1", "16"]
    assert out[9] == "verdict: a variant: 20 swaps or fewer under the same commander"
    assert cells(lines("similar", "rats-a")[2])[-2] == "variant"


# Fixed after review: compare reads commanders through the catalog, so two names the catalog knows as one
# card are one commander
def test_compare_reads_commanders_through_the_catalog(mtg, monkeypatch, catalog, cat):
    alias = {**RATS_B, "commander": "1 Yshtola of the Night"}
    _vault(mtg, RATS_A, alias)
    resolve = cat.resolve
    monkeypatch.setattr(
        cat, "resolve", lambda name: "o-yshtola" if name == "Yshtola of the Night" else resolve(name)
    )
    assert (
        lines("compare", "rats-a", "rats-b")[9]
        == "verdict: a variant: 20 swaps or fewer under the same commander"
    )
    assert cells(lines("similar", "rats-a")[2])[-2] == "variant"


# A .md path that is no deck note fails with what's wrong with it, exit 1
def test_a_md_path_that_is_no_deck_note_fails_with_its_line(mtg, tmp_path, catalog, plain):
    _vault(mtg, BURN_A)
    path = tmp_path / "notes.md"
    path.write_text("# Just a note\n", encoding="utf-8")
    result = invoke("compare", "burn-a", str(path))
    assert result.exit_code == 1
    assert "has no game: mtg frontmatter or no decklist block" in plain(result.stderr)


# It writes nothing: every file is byte for byte as it was
def test_compare_and_similar_change_no_file(mtg, catalog):
    _vault(mtg, BURN_A, BURN_B)
    before = {p: p.read_bytes() for p in Path.home().rglob("*") if p.is_file()}
    lines("compare", "burn-a", "burn-b")
    lines("similar", "burn-a")
    assert {p: p.read_bytes() for p in Path.home().rglob("*") if p.is_file()} == before


# The header counts one deck in the singular
def test_one_deck_is_singular(mtg, catalog):
    _vault(mtg, BURN_A, BURN_B)
    assert lines("similar", "burn-a")[0] == "nearest to burn-a: modern; 1 of 1 deck, basic lands aside"


# Fixed after review: the card lines read accents as their letters, so a name with an accent sorts among its
# letter's, not after Z; semicolons part the names, since a legend's may hold a comma
def test_card_lines_read_accents_as_their_letters(mtg, catalog):
    _vault(
        mtg,
        {**BURN_A, "main": "2 Fire // Ice\n1 Ébène Totem\n1 Zap, the Test\n"},
        without(BURN_B, "side"),
    )
    out = lines("compare", "burn-a", "burn-b")
    assert [line for line in out if line.startswith("only in A, main:")] == [
        "only in A, main: Ébène Totem; Fire // Ice; Zap, the Test"
    ]
