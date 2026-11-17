"""Propagate ORM child changes into the version seen on its parent's form."""
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError


def propagate_versions(db, flush_context, instances):
    from app import models
    created = db.info.setdefault('pv_records_created_in_transaction', set())
    created.update(db.new)
    links = {
        'Patient': [('case_id', 'Case')],
        'CaseProduct': [('case_id', 'Case')],
        'Reaction': [('case_id', 'Case')],
        'FollowUp': [('case_id', 'Case')],
        'CasePartnerLink': [('case_id', 'Case')],
        'CaseIdentifier': [('case_id', 'Case')],
        'CaseNarrative': [('case_id', 'Case')],
        'CaseLiteratureReference': [('case_id', 'Case')],
        'CaseProductReactionAssessment': [('case_product_id', 'CaseProduct')],
        'CaseDrugSubstance': [('case_product_id', 'CaseProduct')],
        'ProductSubstance': [('product_id', 'Product')],
        'ProductRegistrationCertificate': [('product_id', 'Product')],
        'ContractProduct': [('contract_id', 'Contract'), ('product_id', 'Product')],
        'PartnerProductRegistrationLink': [('partner_id', 'Partner'), ('product_id', 'Product')],
        'ContractContact': [('partner_id', 'Partner')],
        'ContractAccountingTariff': [('contract_id', 'Contract')],
        'PartnerAccountingInfo': [('partner_id', 'Partner')],
        'AccountingDataLine': [('accounting_period_id', 'AccountingDataPeriod')],
        'PartnerReconciliationItem': [('reconciliation_id', 'PartnerReconciliation')],
        'SafetyReportProduct': [('safety_report_id', 'SafetyReport')],
        'IncomingRequestProduct': [('incoming_request_id', 'IncomingRequest')],
        'IncomingRequestCaseLink': [('incoming_request_id', 'IncomingRequest')],
        'E2BImportItem': [('batch_id', 'E2BImportBatch')],
        'E2BImportIssue': [('batch_id', 'E2BImportBatch')],
        'PSURProduct': [('psur_plan_id', 'PSURPlan')],
        'PSURCase': [('psur_plan_id', 'PSURPlan')],
        'PSURPartnerRequest': [('psur_plan_id', 'PSURPlan')],
        'PSURSection': [('psur_plan_id', 'PSURPlan')],
        'PSURDocument': [('psur_plan_id', 'PSURPlan')],
        'LiteratureMonitoringPlanProduct': [('plan_id', 'LiteratureMonitoringPlan')],
        'LiteratureSearchLog': [('plan_id', 'LiteratureMonitoringPlan')],
        'LiteratureResult': [('plan_id', 'LiteratureMonitoringPlan'), ('search_log_id', 'LiteratureSearchLog')],
        'LiteratureSearchLogProduct': [('search_log_id', 'LiteratureSearchLog')],
        'LiteratureResultProduct': [('result_id', 'LiteratureResult')],
        'DocumentVersion': [('attachment_id', 'Attachment')],
        'PSMFDocumentLink': [('attachment_id', 'Attachment')],
        'PSMFComponentVersion': [('component_id', 'PSMFComponent')],
        'PSMFBlockVersion': [('block_id', 'PSMFBlock')],
        'PSMFPartnerBuildItem': [('build_id', 'PSMFPartnerBuild')],
        'PSMFBuildDocument': [('build_id', 'PSMFPartnerBuild')],
        'UserRole': [('user_id', 'User')],
        'RolePermission': [('role_id', 'Role')],
    }
    pending = list(db.new) + list(db.deleted) + [row for row in db.dirty if db.is_modified(row, include_collections=False)]
    visited = set()
    for child in pending:
        if id(child) in visited:
            continue
        visited.add(id(child))
        child_links = list(links.get(type(child).__name__, []))
        if isinstance(child, models.PSMFComment):
            target = {'component': 'PSMFComponent', 'PSMFComponent': 'PSMFComponent',
                      'block': 'PSMFBlock', 'PSMFBlock': 'PSMFBlock',
                      'component_version': 'PSMFComponentVersion', 'PSMFComponentVersion': 'PSMFComponentVersion',
                      'block_version': 'PSMFBlockVersion', 'PSMFBlockVersion': 'PSMFBlockVersion',
                      'build': 'PSMFPartnerBuild', 'PSMFPartnerBuild': 'PSMFPartnerBuild'}.get(child.object_type)
            if target:
                child_links.append(('object_id', target))
        if isinstance(child, models.Attachment):
            target = {'Case': 'Case', 'PSMFBlock': 'PSMFBlock', 'PSMFComponent': 'PSMFComponent',
                      'LiteratureResult': 'LiteratureResult', 'Partner': 'Partner', 'Contract': 'Contract',
                      'Product': 'Product', 'Employee': 'User', 'PartnerResponsiblePerson': 'ContractContact',
                      'SOP': 'SOP', 'PSMFBuild': 'PSMFPartnerBuild', 'SafetySource': 'SafetyReport'}.get(child.related_object_type)
            if target:
                child_links.append(('related_object_id', target))
            child_links.extend([('case_id', 'Case'), ('safety_report_id', 'SafetyReport')])
        if isinstance(child, models.Task):
            target = {'Case': 'Case', 'Partner': 'Partner', 'PSUR': 'PSURPlan',
                      'PartnerReconciliation': 'PartnerReconciliation'}.get(child.related_entity_type)
            if target:
                child_links.append(('related_entity_id', target))
        parent_keys = []
        for foreign_key, model_name in child_links:
            values = [getattr(child, foreign_key, None)]
            values.extend(inspect(child).attrs[foreign_key].history.deleted)
            parent_keys.extend((value, model_name) for value in set(values) if value)
        for value, model_name in parent_keys:
            parent = db.get(getattr(models, model_name), value)
            if parent is None or inspect(parent).pending:
                continue
            if parent.is_deleted and not getattr(child, 'is_deleted', False):
                raise StaleDataError('Parent was deleted. Reload before saving.')
            if parent not in created and model_name == 'Case' and (parent.is_locked or (parent.workflow_status or '').lower() in {'closed', 'submitted'}):
                raise StaleDataError('Case is locked. Reload before saving.')
            column = inspect(type(parent)).version_id_col
            if not inspect(parent).attrs[column.key].history.has_changes():
                setattr(parent, column.key, getattr(parent, column.key) + 1)
            pending.append(parent)


def clear_transaction_records(db, transaction):
    if transaction.parent is None:
        db.info.pop('pv_records_created_in_transaction', None)


def install():
    # pytest reloads the app for every isolated database. Keep a single listener
    # on SQLAlchemy's Session; it resolves current models at flush time.
    if not getattr(Session, '_pv_aggregate_versions_installed', False):
        event.listen(Session, 'before_flush', propagate_versions)
        event.listen(Session, 'after_transaction_end', clear_transaction_records)
        Session._pv_aggregate_versions_installed = True
