"""Nullable reaction assessments and bounded source facts; no historical recoding."""
from alembic import op
import sqlalchemy as sa

revision = "20261110_0031"
down_revision = "20261110_0030"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tblReactions") as batch:
        for name in ("is_serious", "seriousness_death", "seriousness_life_threatening",
                     "seriousness_hospitalization", "seriousness_disability",
                     "seriousness_congenital_anomaly", "seriousness_other_medically_important"):
            batch.alter_column(name, existing_type=sa.Boolean(), nullable=True)
        batch.add_column(sa.Column("reported_seriousness", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("interpretation_profile", sa.String(64), nullable=True))
        batch.add_column(sa.Column("source_facts_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("reviewed_fields_json", sa.Text(), nullable=True))
    # Old values (including False) and all links survive; NULL profile means unverified.


def downgrade():
    raise RuntimeError("Restoring NOT NULL would invent assessments; use a reviewed restore.")
