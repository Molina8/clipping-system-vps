"""publish gate: social_accounts, clip_publications, clips.publish_approved_at

Milestone 1 YouTube. See artifacts/publish_and_submit.md.

Revision ID: 0014_publish_gate
Revises: 0013_block_folder_dl_jobs
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0014_publish_gate"
down_revision = "0013_block_folder_dl_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clips",
        sa.Column("publish_approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_clips_publish_approved_at",
        "clips",
        ["publish_approved_at"],
    )

    op.create_table(
        "social_accounts",
        sa.Column("platform", sa.String(32), primary_key=True),
        sa.Column("handle", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="healthy"),
        sa.Column("auth_kind", sa.String(32), nullable=False, server_default="oauth"),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
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
            "platform IN ('youtube', 'tiktok', 'instagram', 'facebook')",
            name="ck_social_accounts_platform",
        ),
        sa.CheckConstraint(
            "status IN ('healthy', 'expired', 'disabled')",
            name="ck_social_accounts_status",
        ),
        sa.CheckConstraint(
            "auth_kind IN ('oauth', 'browser_profile')",
            name="ck_social_accounts_auth_kind",
        ),
    )
    op.execute(
        "INSERT INTO social_accounts (platform, status, auth_kind) "
        "VALUES ('youtube', 'healthy', 'oauth')"
    )

    op.create_table(
        "clip_publications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "clip_id",
            UUID(as_uuid=True),
            sa.ForeignKey("clips.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("post_url", sa.String(1024), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column(
            "submit_status", sa.String(32), nullable=False, server_default="pending"
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submit_ref", sa.String(256), nullable=True),
        sa.Column("submit_error", sa.String(), nullable=True),
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
        sa.UniqueConstraint(
            "clip_id", "platform", name="uq_clip_publications_clip_platform"
        ),
        sa.CheckConstraint(
            "platform IN ('youtube', 'tiktok', 'instagram', 'facebook')",
            name="ck_clip_publications_platform",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'uploading', 'posted', 'failed', 'skipped')",
            name="ck_clip_publications_status",
        ),
        sa.CheckConstraint(
            "submit_status IN ('not_needed', 'pending', 'submitted', 'rejected', 'failed')",
            name="ck_clip_publications_submit_status",
        ),
    )
    op.create_index(
        "ix_clip_publications_clip_id",
        "clip_publications",
        ["clip_id"],
    )
    op.create_index(
        "ix_clip_publications_status",
        "clip_publications",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_clip_publications_status", table_name="clip_publications")
    op.drop_index("ix_clip_publications_clip_id", table_name="clip_publications")
    op.drop_table("clip_publications")
    op.drop_table("social_accounts")
    op.drop_index("ix_clips_publish_approved_at", table_name="clips")
    op.drop_column("clips", "publish_approved_at")
