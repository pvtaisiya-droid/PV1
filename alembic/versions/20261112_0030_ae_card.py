"""Add patient contact details and case-level seriousness criteria without rewriting records."""
import sqlalchemy as sa
from alembic import op

revision = "20261112_0030"
down_revision = "20261111_0029"
branch_labels = None
depends_on = None


def upgrade():
    for table, columns in {
        "tblPatients": [("full_name", sa.String(255)), ("phone", sa.String(100)),
                        ("email", sa.String(255)), ("age_text", sa.String(100))],
        "tblCases": [("seriousness_criteria", sa.JSON())],
        "tblContractContacts": [("country", sa.String(100))],
    }.items():
        existing = {row["name"]: row for row in sa.inspect(op.get_bind()).get_columns(table)}
        for name, type_ in columns:
            previous = existing.get(name)
            if previous is None:
                op.add_column(table, sa.Column(name, type_, nullable=True))
            elif (previous["type"]._type_affinity is not type_._type_affinity or not previous["nullable"]
                  or getattr(previous["type"], "length", None) != getattr(type_, "length", None)):
                raise RuntimeError(f"Review existing AE card column: {table}.{name}")


def downgrade():
    raise RuntimeError("Patient and contact details require an explicit export plan before downgrade.")
