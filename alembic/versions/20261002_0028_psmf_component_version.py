"""Add an independent concurrency counter to PSMF text versions."""
import sqlalchemy as sa
from alembic import op


revision = "20261002_0028"
down_revision = "20261002_0027"
branch_labels = None
depends_on = None


def upgrade():
    existing = next((c for c in sa.inspect(op.get_bind()).get_columns("psmf_component_versions")
                     if c["name"] == "row_version"), None)
    if existing:
        if not isinstance(existing["type"], sa.Integer) or existing["nullable"]:
            raise RuntimeError("psmf_component_versions.row_version must be a non-null integer")
        invalid = op.get_bind().execute(sa.text(
            "SELECT COUNT(*) FROM psmf_component_versions WHERE row_version < 1")).scalar_one()
        if invalid:
            raise RuntimeError("psmf_component_versions contains invalid row versions")
        return
    op.add_column("psmf_component_versions", sa.Column(
        "row_version", sa.Integer(), nullable=False, server_default=sa.text("1")))


def downgrade():
    op.drop_column("psmf_component_versions", "row_version")
