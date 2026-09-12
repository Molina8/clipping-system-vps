"""restrict ck_campaigns_source_provider to whop|manual.

Per Molina 2026-09-11: only Whop-sourced campaigns (and manual entries) are
allowed right now. Any other source_provider values currently in the DB
would be cleaned up by the wipe step that precedes this migration in dev.
"""
from alembic import op


revision = "0010_whop_only"
down_revision = "0009_clips_location"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_campaigns_source_provider", "campaigns", type_="check")
    op.create_check_constraint(
        "ck_campaigns_source_provider",
        "campaigns",
        "source_provider IN ('whop','manual')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_campaigns_source_provider", "campaigns", type_="check")
    op.create_check_constraint(
        "ck_campaigns_source_provider",
        "campaigns",
        "source_provider IN ("
        "'twitter','youtube','instagram','tiktok',"
        "'reddit','twitch','manual','whop','other'"
        ")",
    )
