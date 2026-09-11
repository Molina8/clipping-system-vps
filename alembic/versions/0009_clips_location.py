"""add clips.location, final_path_worker, location_updated_at (Step 18)

Step 18 of architecture_flow.md: organize approved clips by campaign on the
Worker side. The Worker copies the .mp4 to per-campaign folders
(C:\\CODIANT\\clipping\\storage\\clips\\<campaign_id>\\pending_upload\\) on QA pass
and to \\uploaded\\ after social-media publication. The VPS only records the
resulting path.

  pending_upload  -> waiting to upload to social media
  uploaded        -> already published
  archived        -> taken out of the active rotation
  NULL            -> legacy clip, no location yet

Revision ID: 0009_clips_location
Revises: 0008_campaigns_source_provider_whop
Create Date: 2026-09-11 14:55:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "0009_clips_location"
down_revision = "0008_whop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clips",
        sa.Column("location", sa.String(32), nullable=True),
    )
    op.add_column(
        "clips",
        sa.Column("final_path_worker", sa.String(1024), nullable=True),
    )
    op.add_column(
        "clips",
        sa.Column("location_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_clips_campaign_location",
        "clips",
        ["campaign_id", "location"],
    )
    op.create_check_constraint(
        "ck_clips_location",
        "clips",
        "location IS NULL OR location IN ('pending_upload', 'uploaded', 'archived')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_clips_location", "clips", type_="check")
    op.drop_index("ix_clips_campaign_location", table_name="clips")
    op.drop_column("clips", "location_updated_at")
    op.drop_column("clips", "final_path_worker")
    op.drop_column("clips", "location")
