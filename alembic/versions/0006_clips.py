"""create clips table

Revision ID: 0006_clips
Revises: 0005_candidates
Create Date: 2026-09-06 13:38:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0006_clips"
down_revision = "0005_candidates"
branch_labels = None
depends_on = None


CLIP_QA_STATUS_VALUES = ("pending", "pass", "fail", "review")
CLIP_STATUS_VALUES = ("created", "approved", "rejected", "review", "published")


def upgrade() -> None:
    op.create_table(
        "clips",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "campaign_id", sa.Integer,
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id", UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "asset_id", UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "render_job_id", UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "qa_job_id", UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("file_path", sa.String(1024), nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("file_size", sa.BigInteger, nullable=True),
        sa.Column(
            "qa_status", sa.String(32),
            nullable=False, server_default="pending",
        ),
        sa.Column(
            "qa_result", JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status", sa.String(32),
            nullable=False, server_default="created",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("qa_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"qa_status IN {CLIP_QA_STATUS_VALUES!r}",
            name="ck_clips_qa_status",
        ),
        sa.CheckConstraint(
            f"status IN {CLIP_STATUS_VALUES!r}",
            name="ck_clips_status",
        ),
    )
    op.create_index("ix_clips_campaign_id", "clips", ["campaign_id"])
    op.create_index("ix_clips_candidate_id", "clips", ["candidate_id"])
    op.create_index("ix_clips_asset_id", "clips", ["asset_id"])
    op.create_index("ix_clips_render_job_id", "clips", ["render_job_id"])
    op.create_index("ix_clips_qa_job_id", "clips", ["qa_job_id"])
    op.create_index("ix_clips_status", "clips", ["status"])
    op.create_index("ix_clips_qa_status", "clips", ["qa_status"])
    op.create_index(
        "ix_clips_campaign_status", "clips", ["campaign_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_clips_campaign_status", table_name="clips")
    op.drop_index("ix_clips_qa_status", table_name="clips")
    op.drop_index("ix_clips_status", table_name="clips")
    op.drop_index("ix_clips_qa_job_id", table_name="clips")
    op.drop_index("ix_clips_render_job_id", table_name="clips")
    op.drop_index("ix_clips_asset_id", table_name="clips")
    op.drop_index("ix_clips_candidate_id", table_name="clips")
    op.drop_index("ix_clips_campaign_id", table_name="clips")
    op.drop_table("clips")
