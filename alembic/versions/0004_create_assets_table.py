"""create assets table

Revision ID: 0004_create_assets_table
Revises: 0003_create_campaigns_table
Create Date: 2026-09-06 13:19:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0004_create_assets_table"
down_revision = "0003_create_campaigns_table"
branch_labels = None
depends_on = None


ASSET_STATUS_VALUES = ("pending", "downloaded", "transcribed", "failed")


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "campaign_id", sa.Integer,
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Source / provider info
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=True),
        sa.Column("source_provider", sa.String(64), nullable=True),
        sa.Column(
            "asset_type", sa.String(64),
            nullable=False, server_default="video",
        ),
        # State
        sa.Column(
            "status", sa.String(32),
            nullable=False, server_default="pending",
        ),
        # Local-side info (filled by Worker)
        sa.Column("local_path", sa.String(1024), nullable=True),
        sa.Column("file_size", sa.BigInteger, nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("mime_type", sa.String(128), nullable=True),
        # Provider-specific metadata
        sa.Column(
            "extra_metadata", JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        # Timestamps
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transcribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"status IN {ASSET_STATUS_VALUES!r}",
            name="ck_assets_status",
        ),
    )
    op.create_index("ix_assets_campaign_id", "assets", ["campaign_id"])
    op.create_index("ix_assets_status", "assets", ["status"])
    op.create_index(
        "ix_assets_source_provider", "assets", ["source_provider"],
    )
    # Composite for filtering
    op.create_index(
        "ix_assets_campaign_status", "assets", ["campaign_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_assets_campaign_status", table_name="assets")
    op.drop_index("ix_assets_source_provider", table_name="assets")
    op.drop_index("ix_assets_status", table_name="assets")
    op.drop_index("ix_assets_campaign_id", table_name="assets")
    op.drop_table("assets")
