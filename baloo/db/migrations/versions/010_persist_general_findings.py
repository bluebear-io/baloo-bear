"""Persist general findings and unresolved-thread blockers.

Revision ID: 010
Revises: 009
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: str = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reviews",
        sa.Column(
            "awaiting_thread_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    with op.batch_alter_table("findings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "finding_type",
                sa.String(length=20),
                nullable=False,
                server_default="inline",
            )
        )
        batch_op.alter_column(
            "file_path",
            existing_type=sa.String(length=500),
            nullable=True,
        )


def downgrade() -> None:
    # General findings have no file anchor. Preserve their text while restoring
    # the old non-null shape by using an empty location.
    op.execute(sa.text("UPDATE findings SET file_path = '' WHERE file_path IS NULL"))
    with op.batch_alter_table("findings") as batch_op:
        batch_op.alter_column(
            "file_path",
            existing_type=sa.String(length=500),
            nullable=False,
        )
        batch_op.drop_column("finding_type")
    op.drop_column("reviews", "awaiting_thread_count")
