"""Append-only local R3 artifacts. No historical exports are invented."""
import sqlalchemy as sa
from alembic import op

revision='20261111_0034'
down_revision='20261111_0033'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('tblIcsrExportArtifacts',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('case_id',sa.String(36),sa.ForeignKey('tblCases.id'),nullable=False),
        sa.Column('revision_id',sa.String(36),sa.ForeignKey('tblIcsrRevisions.id'),nullable=False),
        sa.Column('previous_artifact_id',sa.String(36),sa.ForeignKey('tblIcsrExportArtifacts.id'),nullable=True),
        sa.Column('idempotency_key',sa.String(36),nullable=False,unique=True),
        sa.Column('request_checksum',sa.String(64),nullable=False),
        sa.Column('revision_checksum',sa.String(64),nullable=False),
        sa.Column('snapshot_schema',sa.String(30),nullable=False),
        sa.Column('profile_id',sa.String(80),nullable=False),
        sa.Column('profile_manifest_sha256',sa.String(64),nullable=False),
        sa.Column('profile_json',sa.Text(),nullable=False),
        sa.Column('exporter_version',sa.String(50),nullable=False),
        sa.Column('rules_version',sa.String(50),nullable=False),
        sa.Column('envelope_json',sa.Text(),nullable=False),
        sa.Column('validation_json',sa.Text(),nullable=False),
        sa.Column('xml_sha256',sa.String(64),nullable=False),
        sa.Column('storage_path',sa.Text(),nullable=False),
        sa.Column('byte_length',sa.Integer(),nullable=False),
        sa.Column('locally_valid',sa.Boolean(),nullable=False),
        sa.Column('xsd_valid',sa.Boolean(),nullable=False),
        sa.Column('author_id',sa.String(36),sa.ForeignKey('tblUsers.id'),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('reason',sa.Text(),nullable=False))
    for field in ('case_id','revision_id'):
        op.create_index('ix_tblIcsrExportArtifacts_'+field,'tblIcsrExportArtifacts',[field])
    if op.get_bind().dialect.name=='sqlite':
        for action in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER immutable_icsr_export_{action.lower()} BEFORE {action} ON tblIcsrExportArtifacts BEGIN SELECT RAISE(ABORT, 'R3 artifact is immutable'); END")

def downgrade():
    raise RuntimeError('Export artifacts are evidence; use a reviewed backup restore, not destructive downgrade.')
