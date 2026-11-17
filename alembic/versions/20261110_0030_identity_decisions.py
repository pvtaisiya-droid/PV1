"""Append-only manual decisions; historical identities are left unchanged."""
import sqlalchemy as sa
from alembic import op

revision = "20261110_0030"
down_revision = "20261110_0029"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("tblCaseIdentityDecisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("tblCases.id"), nullable=False),
        sa.Column("case_version_before", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("tblE2BImportBatches.id"), nullable=False),
        sa.Column("evidence_transmission_id", sa.String(36), sa.ForeignKey("tblE2BTransmissions.id"), nullable=False),
        sa.Column("evidence_checksum", sa.String(64), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=False),
        sa.Column("confirmed_json", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("tblUsers.id"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_id", sa.String(36), sa.ForeignKey("tblCaseIdentityDecisions.id")),
        sa.UniqueConstraint("case_id", "case_version_before", name="uq_identity_decision_version"),
        sa.UniqueConstraint("supersedes_id", name="uq_identity_decision_supersedes"))
    op.create_index("ix_tblCaseIdentityDecisions_case_id", "tblCaseIdentityDecisions", ["case_id"])
    if op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(f'''CREATE TRIGGER identity_decision_no_{action.lower()}
                BEFORE {action} ON "tblCaseIdentityDecisions"
                BEGIN SELECT RAISE(ABORT, 'Identity decisions are append-only'); END''')
    elif op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION reject_identity_decision_change() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Identity decisions are append-only'; END;
            $$ LANGUAGE plpgsql""")
        op.execute('''CREATE TRIGGER identity_decision_immutable BEFORE UPDATE OR DELETE
            ON "tblCaseIdentityDecisions" FOR EACH ROW EXECUTE FUNCTION reject_identity_decision_change()''')


def downgrade():
    raise RuntimeError("Identity decision history requires a reviewed restore; automatic downgrade is disabled.")
