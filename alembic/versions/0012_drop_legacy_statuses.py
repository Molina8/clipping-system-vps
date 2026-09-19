"""Drop legacy campaign statuses (2026-09-18).

Removes the 7 pre-pipeline-v2 statuses from the model and the
ck_campaigns_status check constraint:

    draft, analyzing, ready, active, paused, completed, archived

Rationale (Molina 2026-09-18): the pipeline v2 statuses are the only
ones the system actually uses now. The legacy values were only kept
for backward compat and never appear in current campaigns.

Pre-check: if any campaign row still uses a legacy value, this
migration aborts with a clear error pointing at the cleanup script.
"""
from alembic import op
from sqlalchemy import text

revision = "0012_drop_legacy_statuses"
down_revision = "0011_pipeline_v2_status"
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
ALL_STATUSES = NEW_STATUSES


def upgrade() -> None:
    conn = op.get_bind()
    placeholders = ", ".join(f":s{i}" for i in range(len(LEGACY_STATUSES)))
    params = {f"s{i}": s for i, s in enumerate(LEGACY_STATUSES)}
    rows = conn.execute(
        text(f"SELECT id, status FROM campaigns WHERE status IN ({placeholders})"),
        params,
    ).all()
    if rows:
        ids = ", ".join(str(r[0]) for r in rows[:20])
        sample = sorted({r[1] for r in rows})
        raise RuntimeError(
            "Refusing to drop legacy statuses: "
            f"{len(rows)} campaign(s) still on legacy values "
            f"(sample ids={ids}, statuses={sample}). "
            "Reclassify or delete them first."
        )

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
        f"status IN {(LEGACY_STATUSES + NEW_STATUSES)!r}",
    )
