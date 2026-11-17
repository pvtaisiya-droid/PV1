"""Preserve historical IDs; scope only newly interpreted R3 sender IDs."""
import sqlalchemy as sa
from alembic import op

revision = "20261110_0029"
down_revision = "20261002_0028"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    # 0028 uses a unique index. Do not silently rebuild an unexpected custom
    # unique constraint, which needs a separately reviewed migration.
    if any(c["column_names"] == ["local_sender_case_id"]
           for c in inspector.get_unique_constraints("tblCases")):
        raise RuntimeError("Unexpected local_sender_case_id unique constraint; review schema before migration.")
    op.add_column("tblCases", sa.Column("e2b_identity_profile", sa.String(64), nullable=True))
    op.add_column("tblCases", sa.Column("e2b_sender_scope", sa.Text(), nullable=True))
    for index in inspector.get_indexes("tblCases"):
        if index["unique"] and index["column_names"] == ["local_sender_case_id"]:
            op.drop_index(index["name"], table_name="tblCases")
    remaining = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("tblCases")}
    if "ix_tblCases_local_sender_case_id" not in remaining:
        op.create_index("ix_tblCases_local_sender_case_id", "tblCases", ["local_sender_case_id"])
    op.create_index("uq_case_e2b_sender_local", "tblCases",
                    ["e2b_sender_scope", "local_sender_case_id"], unique=True)
    # No UPDATE, guessed scope, date reconstruction or ID swapping.


def downgrade():
    # Restoring global uniqueness would discard valid scope information.
    raise RuntimeError("R3 source identity migration requires a reviewed restore; automatic downgrade is disabled.")
