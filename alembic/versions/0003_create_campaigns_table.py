"""create campaigns table

Revision ID: 0003_create_campaigns_table
Revises: 0002_create_workers_table
Create Date: 2026-09-06 13:14:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0003_create_campaigns_table"
down_revision = "0002_create_workers_table"
branch_labels = None
depends_on = None


CAMPAIGN_STATUS_VALUES = (
    "draft", "analyzing", "ready", "active",
    "paused", "completed", "archived",
)
CAMPAIGN_SOURCE_VALUES = (
    "twitter", "youtube", "instagram", "tiktok",
    "reddit", "twitch", "manual", "other",
)


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(256), nullable=False, unique=True),
        sa.Column(
            "status", sa.String(32),
            nullable=False, server_default="draft",
        ),
        # Source/provider info — campaigns can come from various websites
        sa.Column(
            "source_provider", sa.String(64),
            nullable=False, server_default="manual",
        ),
        sa.Column("source_id", sa.String(256), nullable=True),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column(
            "source_metadata", JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_instructions", sa.String, nullable=True),
        sa.Column(
            "spec", JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "assets_count", sa.Integer,
            nullable=False, server_default="0",
        ),
        sa.Column(
            "clips_approved", sa.Integer,
            nullable=False, server_default="0",
        ),
        sa.Column(
            "clips_published", sa.Integer,
            nullable=False, server_default="0",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            f"status IN {CAMPAIGN_STATUS_VALUES!r}",
            name="ck_campaigns_status",
        ),
        sa.CheckConstraint(
            f"source_provider IN {CAMPAIGN_SOURCE_VALUES!r}",
            name="ck_campaigns_source_provider",
        ),
    )
    op.create_index("ix_campaigns_status", "campaigns", ["status"])
    op.create_index("ix_campaigns_name", "campaigns", ["name"])
    op.create_index(
        "ix_campaigns_source_provider", "campaigns", ["source_provider"],
    )


def downgrade() -> None:
    op.drop_index("ix_campaigns_source_provider", table_name="campaigns")
    op.drop_index("ix_campaigns_name", table_name="campaigns")
    op.drop_index("ix_campaigns_status", table_name="campaigns")
    op.drop_table("campaigns")
