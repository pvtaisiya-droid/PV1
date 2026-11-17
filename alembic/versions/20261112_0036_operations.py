"""Operational evidence, manual fallback, publications and persistent incidents."""
from alembic import op
import sqlalchemy as s

revision='20261112_0036'
down_revision='20261111_0035'
branch_labels=None
depends_on=None

def col(name,kind=s.Text(),nullable=False):return s.Column(name,kind,nullable=nullable)
def text(name,nullable=False):return col(name,s.Text(),nullable)
def string(name,n,nullable=False):return col(name,s.String(n),nullable)
def fk(name,table,nullable=False):return s.Column(name,s.String(36),s.ForeignKey(table+'.id'),nullable=nullable)
def ident():return s.Column('id',s.String(36),primary_key=True)
def date(name,nullable=False):return col(name,s.DateTime(timezone=True),nullable)
def integer(name):return col(name,s.Integer())

def upgrade():
    op.add_column('tblAS2Dispatches',s.Column('send_hold',s.Boolean(),nullable=False,server_default=s.false()))
    op.add_column('tblAS2Dispatches',s.Column('hold_state',s.String(30),nullable=False,server_default='none'))
    op.create_table('tblOpsSubmissions',ident(),fk('case_id','tblCases'),fk('artifact_id','tblIcsrExportArtifacts'),fk('dispatch_id','tblAS2Dispatches',True),
        string('contour',30),string('recipient',100),string('config_version',50),string('config_hash',64),col('manual_hold',s.Boolean()),fk('owner_id','tblUsers'),
        string('deadline_raw',50,True),date('deadline_utc',True),string('deadline_precision',10,True),text('deadline_basis',True),integer('version'),fk('author_id','tblUsers'),date('created_at'),
        s.UniqueConstraint('artifact_id','contour','recipient',name='uq_ops_artifact_recipient'))
    op.create_table('tblOpsPolicies',ident(),string('config_version',50),string('config_hash',64),integer('number'),string('autopublish',15),
        *(integer(n) for n in ('mdn_seconds','ack_seconds','publication_seconds','approaching_seconds','certificate_days','health_seconds','reminder_seconds','reminder_limit')),
        fk('owner_id','tblUsers'),fk('author_id','tblUsers'),text('reason'),date('created_at'),s.UniqueConstraint('config_version','number',name='uq_ops_policy_version'))
    op.create_table('tblOpsIncidents',ident(),string('scope',100),fk('submission_id','tblOpsSubmissions',True),fk('policy_id','tblOpsPolicies',True),string('rule',60),integer('cycle'),
        string('status',20),col('active_condition',s.Boolean()),string('severity',15),fk('owner_id','tblUsers'),text('summary'),text('basis'),text('required_action'),
        date('since'),date('last_seen'),date('last_reminder'),integer('reminder_count'),integer('version'),s.UniqueConstraint('scope','rule','cycle',name='uq_ops_incident_cycle'))
    op.create_table('tblOpsActions',ident(),fk('submission_id','tblOpsSubmissions',True),fk('incident_id','tblOpsIncidents',True),string('kind',40),fk('actor_id','tblUsers',True),date('occurred_at'),text('facts_json'),text('reason'))
    op.create_index('ix_tblOpsActions_submission_id','tblOpsActions',['submission_id'])
    op.create_table('tblOpsPublications',ident(),fk('submission_id','tblOpsSubmissions'),string('source_key',90),fk('previous_id','tblOpsPublications',True),string('status',25),fk('actor_id','tblUsers'),
        date('checked_at'),date('published_at',True),string('published_raw',50,True),text('source'),string('registration_number',255,True),fk('attachment_id','tblAttachments',True),text('evidence_reference',True),text('reason'))
    op.create_index('ix_tblOpsPublications_submission_id','tblOpsPublications',['submission_id'])
    op.create_table('tblOpsSignals',s.Column('key',s.String(100),primary_key=True),date('observed_at'),text('facts_json'))
    if op.get_bind().dialect.name=='sqlite':
        for table in ('tblOpsActions','tblOpsPublications','tblOpsPolicies'):
            for command in ('UPDATE','DELETE'):
                op.execute(f"CREATE TRIGGER immutable_{table}_{command.lower()} BEFORE {command} ON {table} BEGIN SELECT RAISE(ABORT,'Operational evidence is append-only'); END")
        op.execute("CREATE TRIGGER immutable_ops_subject BEFORE UPDATE OF case_id,artifact_id,contour,recipient,config_version,config_hash,author_id,created_at ON tblOpsSubmissions BEGIN SELECT RAISE(ABORT,'Submission source is immutable'); END")

def downgrade():
    raise RuntimeError('Restore a coordinated offline backup; do not delete operational evidence.')
