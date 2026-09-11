"""add whop to ck_campaigns_source_provider

Revision ID: 0008_whop
Revises: 0007_job_state
Create Date: 2026-09-10 18:40:00.000000

"""
from alembic import op


revision = "0008_whop"
down_revision = "0007_job_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_campaigns_source_provider", "campaigns", type_="check")
    op.create_check_constraint(
        "ck_campaigns_source_provider",
        "campaigns",
        "source_provider IN ("
        "'twitter','youtube','instagram','tiktok',"
        "'reddit','twitch','manual','whop','other'"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_campaigns_source_provider", "campaigns", type_="check")
    op.create_check_constraint(
        "ck_campaigns_source_provider",
        "campaigns",
        "source_provider IN ("
        "'twitter','youtube','instagram','tiktok',"
        "'reddit','twitch','manual','other'"
        ")",
    )
