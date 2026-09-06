"""create workers table

Revision ID: 0002_create_workers_table
Revises: 0001_create_jobs_table
Create Date: 2026-09-06 11:50:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0002_create_workers_table"
down_revision = "0001_create_jobs_table"
branch_labels = None
depends_on = None


WORKER_STATUS_VALUES = ("online", "offline", "busy", "disabled")


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("worker_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="offline",
        ),
        sa.Column("gpu_name", sa.String(128), nullable=True),
        sa.Column(
            "gpu_available",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "capabilities",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "system_info",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "tools_info",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            f"status IN {WORKER_STATUS_VALUES!r}",
            name="ck_workers_status",
        ),
    )
    op.create_index("ix_workers_status", "workers", ["status"])
    op.create_index(
        "ix_workers_last_heartbeat_at", "workers", ["last_heartbeat_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_workers_last_heartbeat_at", table_name="workers")
    op.drop_index("ix_workers_status", table_name="workers")
    op.drop_table("workers")
