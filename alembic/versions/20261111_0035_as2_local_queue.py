"""Durable local AS2 intent/attempt/event ledger; no historical sends invented."""
from alembic import op
import sqlalchemy as s

revision='20261111_0035'
down_revision='20261111_0034'
branch_labels=None
depends_on=None


def identifier():return s.Column('id',s.String(36),primary_key=True)
def text(name,nullable=False):return s.Column(name,s.Text(),nullable=nullable)
def string(name,length,nullable=False):return s.Column(name,s.String(length),nullable=nullable)
def fk(name,table,nullable=False):return s.Column(name,s.String(36),s.ForeignKey(table+'.id'),nullable=nullable)
def date(name):return s.Column(name,s.DateTime(timezone=True),nullable=False)


def upgrade():
    op.create_table('tblAS2Dispatches',identifier(),fk('case_id','tblCases'),fk('artifact_id','tblIcsrExportArtifacts'),
        string('command_key',36),string('command_hash',64),string('contour',30),string('recipient',100),
        string('config_version',50),string('config_hash',64),text('config_json'),string('state',40),string('transport_state',40),string('ack_state',40),
        string('lease_owner',36,True),s.Column('lease_until',s.Float(),nullable=True),s.Column('next_at',s.Float(),nullable=False),s.Column('version',s.Integer(),nullable=False),
        text('last_error',True),fk('author_id','tblUsers'),text('reason'),date('created_at'),date('updated_at'),
        s.UniqueConstraint('command_key'),s.UniqueConstraint('artifact_id','contour','recipient',name='uq_as2_intent_recipient'))
    op.create_index('ix_tblAS2Dispatches_case_id','tblAS2Dispatches',['case_id'])
    op.create_table('tblAS2Attempts',identifier(),fk('dispatch_id','tblAS2Dispatches'),s.Column('number',s.Integer(),nullable=False),string('state',40),
        string('engine_id',36,True),string('message_id',255,True),s.Column('send_count',s.Integer(),nullable=False),text('mic',True),string('mic_algorithm',30,True),
        text('error',True),date('created_at'),date('updated_at'),s.UniqueConstraint('dispatch_id','number',name='uq_as2_attempt_number'))
    for field in ('dispatch_id','message_id'):op.create_index('ix_tblAS2Attempts_'+field,'tblAS2Attempts',[field])
    op.create_table('tblAS2Events',identifier(),string('engine_id',36),s.Column('sequence',s.Integer(),nullable=False),string('event_key',150),string('kind',30),
        fk('dispatch_id','tblAS2Dispatches',True),fk('attempt_id','tblAS2Attempts',True),text('metadata_json'),text('raw_path',True),string('raw_sha256',64,True),
        text('payload_path',True),string('payload_sha256',64,True),string('processing_state',30),text('review_reason',True),date('created_at'),
        s.UniqueConstraint('engine_id','sequence',name='uq_as2_engine_event'))
    op.create_index('ix_tblAS2Events_dispatch_id','tblAS2Events',['dispatch_id'])
    op.create_table('tblAS2AckResults',identifier(),fk('event_id','tblAS2Events'),s.Column('ordinal',s.Integer(),nullable=False),string('message_id',255),string('code',50),
        string('interpretation',30),text('detail'),s.UniqueConstraint('event_id','ordinal',name='uq_as2_ack_ordinal'))
    op.create_index('ix_tblAS2AckResults_event_id','tblAS2AckResults',['event_id'])
    if op.get_bind().dialect.name=='sqlite':
        op.execute("CREATE TRIGGER as2_event_source_immutable BEFORE UPDATE OF engine_id,sequence,event_key,kind,metadata_json,raw_path,raw_sha256,payload_path,payload_sha256 ON tblAS2Events BEGIN SELECT RAISE(ABORT,'AS2 event evidence is immutable'); END")
        op.execute("CREATE TRIGGER as2_intent_source_immutable BEFORE UPDATE OF case_id,artifact_id,command_key,command_hash,contour,recipient,config_version,config_hash,config_json,author_id,reason,created_at ON tblAS2Dispatches BEGIN SELECT RAISE(ABORT,'AS2 intent evidence is immutable'); END")
        for table in ('tblAS2Dispatches','tblAS2Attempts','tblAS2Events','tblAS2AckResults'):
            op.execute(f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'AS2 history must be retained'); END")


def downgrade():
    raise RuntimeError('AS2 history is evidence; use a reviewed backup restore instead of destructive downgrade.')
