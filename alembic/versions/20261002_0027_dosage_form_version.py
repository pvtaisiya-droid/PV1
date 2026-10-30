"""Protect dosage form writes with an independent row version."""
import sqlalchemy as sa
from alembic import op


revision = "20261002_0027"
down_revision = "20260930_0026"
branch_labels = None
depends_on = None


def upgrade():
    # Legacy adoption can preserve an already present concurrency column.
    existing = next((c for c in sa.inspect(op.get_bind()).get_columns("dosage_forms")
                     if c["name"] == "row_version"), None)
    if existing:
        if not isinstance(existing["type"], sa.Integer) or existing["nullable"]:
            raise RuntimeError("dosage_forms.row_version must be a non-null integer")
        invalid = op.get_bind().execute(sa.text(
            "SELECT COUNT(*) FROM dosage_forms WHERE row_version < 1")).scalar_one()
        if invalid:
            raise RuntimeError("dosage_forms contains invalid row versions")
        return
    op.add_column("dosage_forms", sa.Column(
        "row_version", sa.Integer(), nullable=False, server_default=sa.text("1")))


def downgrade():
    op.drop_column("dosage_forms", "row_version")
