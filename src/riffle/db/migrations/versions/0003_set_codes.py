"""set codes: one live set per code within a game, checked at commit.

A source can pass a code from one set to another in a single update: Scryfall
renamed Black Lotus Unknown Planechase's code from punk to ounk on 2026-11-17
and gave punk to a new set. A unique constraint is checked row by row, so the
new set's row met the old one's code before the old row moved, and every load
failed. As the collector-number constraint on printings does, this one is an
exclusion constraint, deferred to commit and holding only live sets, so codes
can move or swap within one load and a retired set no longer keeps its code.

The downgrade's unique constraint covers retired sets too, so once a retired
set shares its code with another set, the downgrade fails and the database
stays at 0003. Clear or change those retired sets' codes by hand first.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("uq_sets_game_id_code"), "sets", type_="unique")
    op.create_exclude_constraint(
        op.f("ex_sets_game_id_code"),
        "sets",
        ("game_id", "="),
        ("code", "="),
        using="btree",
        where=sa.text("retired_at IS NULL"),
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ex_sets_game_id_code"), "sets")
    op.create_unique_constraint(op.f("uq_sets_game_id_code"), "sets", ["game_id", "code"])
