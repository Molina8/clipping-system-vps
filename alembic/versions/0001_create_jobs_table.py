"""create jobs table

Revision ID: 0001_create_jobs_table
Revises:
Create Date: 2026-09-04 21:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0001_create_jobs_table"
down_revision = None
branch_labels = None
depends_on = None


JOB_STATUS_VALUES = ("pending", "assigned", "processing", "completed", "failed", "cancelled")


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("priority", sa.Integer, nullable=False, server_default="5"),
        sa.Column(
            "payload",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error_message", sa.String, nullable=True),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"status IN {JOB_STATUS_VALUES!r}",
            name="ck_jobs_status",
        ),
    )

    op.create_index("ix_jobs_job_type", "jobs", ["job_type"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_worker_id", "jobs", ["worker_id"])
    # Composite index for the atomic claim query:
    # WHERE status = 'pending' AND available_at <= now
    # ORDER BY priority DESC, created_at ASC
    op.create_index(
        "ix_jobs_claim",
        "jobs",
        ["status", "priority", "available_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.drop_index("ix_jobs_worker_id", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_job_type", table_name="jobs")
    op.drop_table("jobs")
