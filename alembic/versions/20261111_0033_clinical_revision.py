"""Clinical repeats, independent incoming messages and immutable revisions."""
import sqlalchemy as sa
from alembic import op
import json, uuid
from datetime import datetime, timezone

revision = "20261111_0033"
down_revision = "20261110_0032"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('tblCaseClinicalRecords',
        sa.Column('case_id', sa.String(length=36), sa.ForeignKey('tblCases.id'), nullable=False),
        sa.Column('kind', sa.String(length=50), nullable=False),
        sa.Column('parent_id', sa.String(length=36), sa.ForeignKey('tblCaseClinicalRecords.id'), nullable=True),
        sa.Column('patient_id', sa.String(length=36), sa.ForeignKey('tblPatients.id'), nullable=True),
        sa.Column('case_product_id', sa.String(length=36), sa.ForeignKey('tblCaseProducts.id'), nullable=True),
        sa.Column('reaction_id', sa.String(length=36), sa.ForeignKey('tblReactions.id'), nullable=True),
        sa.Column('literature_id', sa.String(length=36), sa.ForeignKey('tblCaseLiteratureReferences.id'), nullable=True),
        sa.Column('narrative_id', sa.String(length=36), sa.ForeignKey('tblCaseNarratives.id'), nullable=True),
        sa.Column('attachment_id', sa.String(length=36), sa.ForeignKey('tblAttachments.id'), nullable=True),
        sa.Column('fields_json', sa.Text(), nullable=False),
        sa.Column('source_facts_json', sa.Text(), nullable=True),
        sa.Column('interpretation_profile', sa.String(length=64), nullable=False),
        sa.Column('reviewed_fields_json', sa.Text(), nullable=True),
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_by', sa.String(length=36), nullable=True),
        sa.Column('delete_reason', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
    )
    op.create_index('ix_tblCaseClinicalRecords_patient_id','tblCaseClinicalRecords',['patient_id'],unique=False)
    op.create_index('ix_tblCaseClinicalRecords_parent_id','tblCaseClinicalRecords',['parent_id'],unique=False)
    op.create_index('ix_tblCaseClinicalRecords_reaction_id','tblCaseClinicalRecords',['reaction_id'],unique=False)
    op.create_index('ix_tblCaseClinicalRecords_case_id','tblCaseClinicalRecords',['case_id'],unique=False)
    op.create_index('ix_tblCaseClinicalRecords_case_product_id','tblCaseClinicalRecords',['case_product_id'],unique=False)
    op.create_index('ix_tblCaseClinicalRecords_kind','tblCaseClinicalRecords',['kind'],unique=False)
    op.create_table('tblIcsrMessages',
        sa.Column('batch_id', sa.String(length=36), sa.ForeignKey('tblE2BImportBatches.id'), nullable=False),
        sa.Column('item_id', sa.String(length=36), sa.ForeignKey('tblE2BImportItems.id'), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('source_checksum', sa.String(length=64), nullable=False),
        sa.Column('fragment_checksum', sa.String(length=64), nullable=False),
        sa.Column('source_xml', sa.Text(), nullable=False),
        sa.Column('normalized_json', sa.Text(), nullable=True),
        sa.Column('issues_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('case_id', sa.String(length=36), sa.ForeignKey('tblCases.id'), nullable=True),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('applied_by', sa.String(length=36), sa.ForeignKey('tblUsers.id'), nullable=True),
        sa.Column('decision_json', sa.Text(), nullable=True),
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_by', sa.String(length=36), nullable=True),
        sa.Column('delete_reason', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.UniqueConstraint('item_id','ordinal', name='uq_icsr_message_item_ordinal'),
    )
    op.create_index('ix_tblIcsrMessages_batch_id','tblIcsrMessages',['batch_id'],unique=False)
    op.create_index('ix_tblIcsrMessages_case_id','tblIcsrMessages',['case_id'],unique=False)
    op.create_index('ix_tblIcsrMessages_item_id','tblIcsrMessages',['item_id'],unique=False)
    op.create_table('tblIcsrRevisions',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('case_id', sa.String(length=36), sa.ForeignKey('tblCases.id'), nullable=False),
        sa.Column('revision_number', sa.Integer(), nullable=False),
        sa.Column('schema_version', sa.String(length=30), nullable=False),
        sa.Column('snapshot_json', sa.Text(), nullable=False),
        sa.Column('checksum_sha256', sa.String(length=64), nullable=False),
        sa.Column('validation_json', sa.Text(), nullable=False),
        sa.Column('source_message_id', sa.String(length=36), sa.ForeignKey('tblIcsrMessages.id'), nullable=True),
        sa.Column('author_id', sa.String(length=36), sa.ForeignKey('tblUsers.id'), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('source_message_id', name='uq_icsr_revision_message'),
        sa.UniqueConstraint('case_id','revision_number', name='uq_icsr_case_revision'),
    )
    op.create_index('ix_tblIcsrRevisions_case_id','tblIcsrRevisions',['case_id'],unique=False)
    op.create_table('tblIcsrReviewPlans',
        sa.Column('source_message_id', sa.String(length=36), sa.ForeignKey('tblIcsrMessages.id'), nullable=False),
        sa.Column('case_id', sa.String(length=36), sa.ForeignKey('tblCases.id'), nullable=True),
        sa.Column('source_version', sa.Integer(), nullable=False),
        sa.Column('case_token', sa.String(length=80), nullable=False),
        sa.Column('current_checksum', sa.String(length=64), nullable=False),
        sa.Column('incoming_checksum', sa.String(length=64), nullable=False),
        sa.Column('preview_json', sa.Text(), nullable=False),
        sa.Column('decision_json', sa.Text(), nullable=True),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('author_id', sa.String(length=36), sa.ForeignKey('tblUsers.id'), nullable=False),
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_by', sa.String(length=36), nullable=True),
        sa.Column('delete_reason', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
    )
    op.create_index('ix_tblIcsrReviewPlans_case_id','tblIcsrReviewPlans',['case_id'],unique=False)
    op.create_index('ix_tblIcsrReviewPlans_source_message_id','tblIcsrReviewPlans',['source_message_id'],unique=False)
    with op.batch_alter_table("tblE2BTransmissions") as batch:
        batch.add_column(sa.Column("import_message_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_transmission_message", "tblIcsrMessages", ["import_message_id"], ["id"])
        batch.drop_constraint("uq_e2b_transmission_import_item", type_="unique")
        batch.create_unique_constraint("uq_e2b_transmission_import_message", ["import_message_id"])
    op.create_index("uq_e2b_transmission_legacy_item", "tblE2BTransmissions", ["import_item_id"], unique=True,
        sqlite_where=sa.text("import_message_id IS NULL"), postgresql_where=sa.text("import_message_id IS NULL"))
    if op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER immutable_icsr_revision_{action.lower()} BEFORE {action} ON tblIcsrRevisions BEGIN SELECT RAISE(ABORT, 'Clinical revision is immutable'); END")
    with op.batch_alter_table("tblReactions") as batch:
        batch.alter_column("reported_term", existing_type=sa.String(255), nullable=True)
    for table in ('tblReactions', 'tblCaseProducts', 'tblCaseDrugSubstances', 'tblCaseDrugDosages',
                  'tblCaseDrugIndications', 'tblCaseDrugRechallenges', 'tblCaseProductReactionAssessments'):
        op.add_column(table, sa.Column('current_sources_json', sa.Text(), nullable=True))
    # Existing clinical rows and source XML/JSON stay byte-for-byte unchanged.
    # No evidence is invented and no automatic baseline revision is backdated.
    adopt_legacy()


def adopt_legacy():
    """Copy known lexical fields; do not infer parentage or reinterpret source IDs."""
    connection = op.get_bind()
    metadata = sa.MetaData()
    target = sa.Table('tblCaseClinicalRecords', metadata, autoload_with=connection)
    now = datetime.now(timezone.utc)
    mappings = {
        'tblPatients': ('patient', 'patient_id', {'patient_initials':'D.1', 'birth_date_raw':'D.2.1', 'age_value':'D.2.2a', 'age_unit':'D.2.2b',
            'age_group':'D.2.3', 'weight_kg':'D.3', 'height_cm':'D.4', 'sex':'D.5', 'medical_history_text':'D.7.2', 'death_date_raw':'D.9.1',
            'patient_identifier':'local.patient_id', 'pregnancy_status':'local.pregnancy_status'}),
        'tblCaseLiteratureReferences': ('literature', 'literature_id', {'citation':'C.4.r.1'}),
        'tblCaseNarratives': ('narrative', 'narrative_id', {'narrative_text':'H.1'}),
        'tblReactions': ('reaction_extra', 'reaction_id', {'term_language':'E.i.1.1b', 'verbatim_term':'E.i.1.2',
            'onset_date_raw':'E.i.4', 'end_date_raw':'E.i.5', 'medically_confirmed':'E.i.8', 'term_country_code':'E.i.9'}),
    }
    for table_name, (kind, link, mapping) in mappings.items():
        source = sa.Table(table_name, metadata, autoload_with=connection)
        for old in connection.execute(sa.select(source)).mappings():
            row_kind, row_map = kind, mapping
            if table_name == 'tblCaseNarratives':
                if old['narrative_type'] == 'e2b_followup_snapshot':
                    continue  # historical input snapshot is not a new current narrative
                known = {'medical_narrative':('narrative','H.1'), 'narrative':('narrative','H.1'),
                         'sender_comment':('sender_comment','H.4'), 'company_comment':('sender_comment','H.4'),
                         'reporter_comment':('reporter_comment','H.2'), 'summary':('summary','H.5.r.1a')}
                row_kind, code = known.get(old['narrative_type'], ('narrative','local.legacy_text'))
                row_map = {'narrative_text':code}
            if table_name == 'tblPatients' and old['patient_role'] == 'parent':
                row_kind = 'parent'
                row_map = {'patient_initials':'D.10.1', 'birth_date_raw':'D.10.2.1', 'age_value':'D.10.2.2a', 'age_unit':'D.10.2.2b',
                           'weight_kg':'D.10.4', 'height_cm':'D.10.5', 'sex':'D.10.6', 'medical_history_text':'D.10.7.2'}
            if table_name == 'tblPatients':
                row_map = dict(row_map)
                if old['birth_date_raw'] is None and old['date_of_birth'] is not None:
                    row_map['date_of_birth'] = 'D.10.2.1' if row_kind == 'parent' else 'D.2.1'
                if row_kind == 'patient' and old['death_date_raw'] is None and old['death_date'] is not None:
                    row_map['death_date'] = 'D.9.1'
            values = {code: dict(raw=str(old[field]), value=str(old[field]), state='legacy_unverified', origin='legacy_database',
                         context=dict(table=table_name, id=old['id'], column=field), null_flavor=None, dictionary_validation='not_performed')
                      for field, code in row_map.items() if old[field] is not None}
            if not values and table_name == 'tblReactions':
                continue
            connection.execute(target.insert().values(id=str(uuid.uuid4()), case_id=old['case_id'], kind=row_kind,
                **{link:old['id']}, fields_json=json.dumps(values, ensure_ascii=False),
                source_facts_json=json.dumps(dict(origin='legacy_database', table=table_name, id=old['id'], values=dict(old)), default=str, ensure_ascii=False),
                interpretation_profile='legacy_unverified', created_at=now, updated_at=now, is_active=old['is_active'],
                is_deleted=old['is_deleted'], deleted_at=old['deleted_at'], deleted_by=old['deleted_by'], delete_reason=old['delete_reason'], version=1))
    cases = sa.Table('tblCases', metadata, autoload_with=connection)
    narratives = metadata.tables['tblCaseNarratives']
    for old in connection.execute(sa.select(cases)).mappings():
        for field, kind, code in (('narrative','narrative','H.1'), ('company_comment','sender_comment','H.4')):
            if old[field] is None:
                continue
            represented = connection.execute(sa.select(narratives.c.id).where(narratives.c.case_id == old['id'], narratives.c.narrative_text == old[field], narratives.c.narrative_type != 'e2b_followup_snapshot')).first()
            if represented:
                continue
            fact = dict(raw=old[field], value=old[field], state='legacy_unverified', origin='legacy_database',
                        context=dict(table='tblCases', id=old['id'], column=field), null_flavor=None, dictionary_validation='not_performed')
            connection.execute(target.insert().values(id=str(uuid.uuid4()), case_id=old['id'], kind=kind,
                fields_json=json.dumps({code:fact},ensure_ascii=False), source_facts_json=json.dumps(fact,ensure_ascii=False),
                interpretation_profile='legacy_unverified', created_at=now, updated_at=now, is_active=old['is_active'],
                is_deleted=old['is_deleted'], deleted_at=old['deleted_at'], deleted_by=old['deleted_by'], delete_reason=old['delete_reason'], version=1))


def downgrade():
    raise RuntimeError("Clinical history and revisions require a reviewed restore; destructive downgrade is unavailable.")
