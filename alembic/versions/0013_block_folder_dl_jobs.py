"""Block 'download' jobs whose payload url points to a folder.

2026-09-18 (Molina, audio 17:50): "quiero que las futuras NUNCA se
descargue la puta carpeta". CHECK constraint a nivel BD — no depende
de Python, cubre cualquier INSERT/UPDATE en `jobs` desde cualquier
script (presente o futuro).

Cubre:
  - https://drive.google.com/drive/folders/<id>?...
  - https://www.dropbox.com/scl/fo/<folder>/<file>?dl=0&rlkey=...

El DDL se aplica con :param bindings (NO string interpolation) para
evitar el quoting infernal del `%%`. El `LIKE` se queda en wildcard
real con escape `\` y los parámetros se pasan vía .execution_options
o directamente en op.execute con text().
"""
from alembic import op
from sqlalchemy import text

revision = "0013_block_folder_dl_jobs"
down_revision = "0012_drop_legacy_statuses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = text(
        """
        ALTER TABLE jobs
        ADD CONSTRAINT ck_jobs_no_folder_download
        CHECK (
            job_type <> 'download'
            OR payload IS NULL
            OR NOT (
                (payload->>'url') LIKE :drive_pattern
                OR (payload->>'url') LIKE :dropbox_pattern
            )
        )
        """
    ).execution_options(
        autocommit=False
    ).bindparams(
        drive_pattern="%drive.google.com/drive/folders%",
        dropbox_pattern="%/scl/fo/%",
    )
    op.execute(sql)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_no_folder_download"
    )
