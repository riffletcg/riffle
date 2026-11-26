"""Shared by the collection's tests: a home whose sync picks up the collection."""

from pathlib import Path

import pytest

from riffle import config


@pytest.fixture
def collection_home(bare_home, bare_mtg, opened, prices_kept) -> Path:
    """A home for a sync that picks up the collection: a config naming ~/vault with card notes
    off, the vault's commander folder, ~/Downloads and the data folder made, the catalog in
    memory, and a price day kept. Returns mtg/."""
    config.write_default().write_text('vault = "~/vault"\nnotes = "games/tcg"\ncard_notes = false\n')
    (bare_mtg / "commander").mkdir(parents=True)
    (bare_home / "Downloads").mkdir()
    config.data_dir().mkdir(parents=True)
    return bare_mtg
