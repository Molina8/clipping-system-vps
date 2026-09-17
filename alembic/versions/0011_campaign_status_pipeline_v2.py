"""Pipeline v2: split paso 3 into 3a/3b/3c and retire paso 2.

Adds new status values to campaigns.status check constraint:
  - discovered   (paso 1: minimal upsert, no assets)
  - briefed      (paso 3a: brief-reader wrote rules + raw asset_links)
  - assets_resolved (paso 3b: drive-resolver turned folders into real assets)
  - scored       (paso 3c: campaign-scorer wrote score+priority)
  - blocked_no_assets (paso 3c: 0 real assets → parked)

Keeps legacy values (draft/ready/active/paused/completed/archived) for
backward compatibility with existing rows and the prior cron.

Per Molina 2026-09-17: kill the single-shot paso 3 cron that loaded
3 skills and blew past 31k context; replace with one-skill-per-cron
chain driven by status transitions.
"""
from alembic import op

revision = "0011_pipeline_v2_status"
down_revision = "0010_whop_only"
branch_labels = None
depends_on = None


LEGACY_STATUSES = (
    "draft", "analyzing", "ready", "active",
    "paused", "completed", "archived",
)
NEW_STATUSES = (
    "discovered", "briefed", "assets_resolved", "scored",
    "blocked_no_assets", "failed_brief", "failed_resolve",
)
ALL_STATUSES = LEGACY_STATUSES + NEW_STATUSES


def upgrade() -> None:
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint(
        "ck_campaigns_status",
        "campaigns",
        f"status IN {ALL_STATUSES!r}",
    )


def downgrade() -> None:
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint(
        "ck_campaigns_status",
        "campaigns",
        f"status IN {LEGACY_STATUSES!r}",
    )
