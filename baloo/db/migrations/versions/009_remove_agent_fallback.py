"""Remove automatic model fallback configuration and storage.

Revision ID: 009
Revises: 008
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: str = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "runtime_settings" in tables:
        conn.execute(
            sa.text("DELETE FROM runtime_settings WHERE key = :key"),
            {"key": "agent_fallback_model"},
        )

    if "reviews" in tables:
        columns = {column["name"] for column in inspector.get_columns("reviews")}
        if "fallback_model" in columns:
            op.drop_column("reviews", "fallback_model")


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "reviews" in tables:
        columns = {column["name"] for column in inspector.get_columns("reviews")}
        if "fallback_model" not in columns:
            op.add_column(
                "reviews",
                sa.Column("fallback_model", sa.String(length=100), nullable=True),
            )
