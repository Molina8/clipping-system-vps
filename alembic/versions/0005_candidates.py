"""create candidates table

Revision ID: 0005_candidates
Revises: 0004_create_assets_table
Create Date: 2026-09-06 13:38:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0005_candidates"
down_revision = "0004_create_assets_table"
branch_labels = None
depends_on = None


CANDIDATE_STATUS_VALUES = (
    "pending", "approved", "rejected", "rendered", "superseded",
)


def upgrade() -> None:
    op.create_table(
        "candidates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "campaign_id", sa.Integer,
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id", UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_time", sa.Float, nullable=False),
        sa.Column("end_time", sa.Float, nullable=False),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("reasoning", sa.Text, nullable=True),
        sa.Column(
            "extra_metadata", JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status", sa.String(32),
            nullable=False, server_default="pending",
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
            f"status IN {CANDIDATE_STATUS_VALUES!r}",
            name="ck_candidates_status",
        ),
        sa.CheckConstraint(
            "end_time > start_time",
            name="ck_candidates_time_order",
        ),
    )
    op.create_index("ix_candidates_campaign_id", "candidates", ["campaign_id"])
    op.create_index("ix_candidates_asset_id", "candidates", ["asset_id"])
    op.create_index("ix_candidates_status", "candidates", ["status"])
    op.create_index(
        "ix_candidates_campaign_status", "candidates", ["campaign_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_candidates_campaign_status", table_name="candidates")
    op.drop_index("ix_candidates_status", table_name="candidates")
    op.drop_index("ix_candidates_asset_id", table_name="candidates")
    op.drop_index("ix_candidates_campaign_id", table_name="candidates")
    op.drop_table("candidates")
