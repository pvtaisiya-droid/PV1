"""Supply current versions in existing tests of successful business workflows.

Concurrency tests deliberately use the raw client and keep the token obtained
when their form was opened. This helper is only for the older positive tests.
"""
import re


def post_current(client, url, **kwargs):
    from app import models
    from app.database import SessionLocal
    from app.concurrency import snapshot_token, ONBOARDING_INPUTS, IMPORT_INPUTS, PSMF_INPUTS, CASE_INPUTS, PSUR_INPUTS, LITERATURE_INPUTS
    api = str(url).startswith('/api/')
    channel = 'json' if api and kwargs.get('json') is not None else 'data'
    data = dict(kwargs.get(channel) or {})
    params = dict(kwargs.get('params') or {})
    path = str(url).split('?')[0]
    with SessionLocal() as db:
        def token(model, identifier, name='expected_version', query=False):
            if not identifier:
                return
            row = db.get(model, identifier)
            if row is not None:
                (params if query else data).setdefault(name, getattr(row, model.__mapper__.version_id_col.key))

        match = re.fullmatch(r'/(?:api/)?cases/([^/]+)/(.+)', path)
        if match:
            identifier, action = match.groups()
            if action in {'numbering-partner', 'numbering-partner/correct', 'partners', 'identifiers', 'assessments', 'patients', 'products', 'reactions', 'followups', 'delete'} or action.startswith('followups/'):
                token(models.Case, identifier, query=api)
                if action.startswith('followups/'):
                    token(models.FollowUp, action.split('/')[1], 'expected_followup_version')
        match = re.fullmatch(r'/tasks/([^/]+)/status', path)
        if match:
            token(models.Task, match[1])
        match = re.fullmatch(r'/users-roles/users/([^/]+)/.+', path)
        if match:
            token(models.User, match[1])
        match = re.fullmatch(r'/users-roles/roles/([^/]+)/permissions', path)
        if match:
            token(models.Role, match[1])
        if path == '/accounting-data':
            token(models.Contract, data.get('contract_id'), 'expected_contract_version')
        if path in {'/contract-contacts', '/api/contract-contacts'}:
            token(models.Partner, data.get('partner_id'), 'expected_partner_version', query=api)
        if path in {'/product-substances', '/api/product-substances'}:
            token(models.Product, data.get('product_id'), 'expected_product_version', query=api)
        match = re.fullmatch(r'/products/([^/]+)/registrations', path)
        if match:
            token(models.Product, match[1])
        match = re.fullmatch(r'/partner-onboarding/([^/]+)/step/\d+', path)
        if match:
            token(models.PartnerOnboardingWizard, match[1])
            data.setdefault('expected_snapshot', snapshot_token(db, ONBOARDING_INPUTS))
        if path.startswith('/contract-imports/confirm'):
            data.setdefault('expected_snapshot', snapshot_token(db, IMPORT_INPUTS))
        match = re.fullmatch(r'/literature-monitoring/(plans|logs|results)/([^/]+)(?:/.*)?', path)
        if match:
            token({'plans': models.LiteratureMonitoringPlan, 'logs': models.LiteratureSearchLog,
                   'results': models.LiteratureResult}[match[1]], match[2])
        if path.startswith('/literature-monitoring/logs') or path.startswith('/literature-monitoring/results'):
            token(models.LiteratureMonitoringPlan, data.get('plan_id'), 'expected_plan_version')
        if re.fullmatch(r'/literature-monitoring/results/[^/]+/(?:create-icsr|link)', path):
            data.setdefault('expected_inputs', snapshot_token(db, LITERATURE_INPUTS))
        match = re.fullmatch(r'/psmf/blocks/([^/]+)/attachments', path)
        if match:
            token(models.PSMFBlock, match[1])
        match = re.fullmatch(r'/psmf/builds/([^/]+)/approve', path)
        if match:
            token(models.PSMFPartnerBuild, match[1])
        if path == '/psmf/settings':
            row = db.query(models.PSMFSettings).first()
            token(models.PSMFSettings, row.id)
        if path == '/psmf/partner-build':
            data.setdefault('expected_snapshot', snapshot_token(db, PSMF_INPUTS))
        if re.fullmatch(r'/(?:api/)?(?:incoming-requests|safety-reports)/[^/]+/create-case', path):
            (params if path.startswith('/api/safety-reports') else data).setdefault('expected_inputs', snapshot_token(db, ONBOARDING_INPUTS))
        if re.fullmatch(r'/safety-reports/e2b-imports/[^/]+/confirm', path):
            data.setdefault('expected_inputs', snapshot_token(db, CASE_INPUTS))
        if re.fullmatch(r'/psur/[^/]+/(?:find-cases|partner-requests|sections/[^/]+/generate)', path):
            data.setdefault('expected_inputs', snapshot_token(db, PSUR_INPUTS))
        if path in {'/cases', '/cases/manual', '/api/cases'}:
            token(models.SafetyReport, data.get('safety_report_id'), 'expected_report_version', query=api)
        if path == '/documents':
            mapping = {'Case': models.Case, 'Partner': models.Partner, 'Product': models.Product,
                'Contract': models.Contract, 'Employee': models.User, 'PartnerResponsiblePerson': models.ContractContact,
                'SafetySource': models.SafetyReport, 'PSMFBlock': models.PSMFBlock, 'PSMFBuild': models.PSMFPartnerBuild,
                'PSMFComponent': models.PSMFComponent, 'SOP': models.SOP}
            model = mapping.get(data.get('related_object_type'))
            if model:
                token(model, data.get('related_object_id'), 'expected_related_version')
            for field, model, name in (('case_id', models.Case, 'expected_related_version'),
                ('safety_report_id', models.SafetyReport, 'expected_related_version'),
                ('partner_id', models.Partner, 'expected_partner_version'), ('product_id', models.Product, 'expected_product_version')):
                token(model, data.get(field), name)
    if data:
        kwargs[channel] = data
    if params:
        kwargs['params'] = params
    return client.post(url, **kwargs)
