"""Migration: add needed indexes for job state transitions (Fase C).

No new tables — this is an incremental index migration to optimize the
auto-create-next-job queries in job_state_transitions.

Revision ID: 0007_job_state
Revises: 0006_clips
Create Date: 2026-09-06 13:38:00.000000

"""
from alembic import op


revision = "0007_job_state"
down_revision = "0006_clips"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # GIN index on jobs.payload so asset_id lookups in the state-transition
    # functions are fast.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_jobs_payload_gin ON jobs USING gin (payload jsonb_path_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_payload_gin")
