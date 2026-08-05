"""Add runtime_settings table for DB-backed config overrides.

Revision ID: 008
Revises: 007
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: str = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "runtime_settings" not in tables:
        op.create_table(
            "runtime_settings",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("key", sa.String(length=100), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("installation_id", sa.String(length=255), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_by", sa.String(length=255), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_runtime_settings_installation_id",
            "runtime_settings",
            ["installation_id"],
        )
        op.create_index(
            "uq_runtime_settings_null_tenant",
            "runtime_settings",
            ["key"],
            unique=True,
            postgresql_where=sa.text("installation_id IS NULL"),
            sqlite_where=sa.text("installation_id IS NULL"),
        )
        op.create_index(
            "uq_runtime_settings_with_tenant",
            "runtime_settings",
            ["key", "installation_id"],
            unique=True,
            postgresql_where=sa.text("installation_id IS NOT NULL"),
            sqlite_where=sa.text("installation_id IS NOT NULL"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if "runtime_settings" not in tables:
        return

    indexes = {idx["name"] for idx in inspector.get_indexes("runtime_settings")}
    for name in (
        "uq_runtime_settings_with_tenant",
        "uq_runtime_settings_null_tenant",
        "ix_runtime_settings_installation_id",
    ):
        if name in indexes:
            op.drop_index(name, table_name="runtime_settings")
    op.drop_table("runtime_settings")
