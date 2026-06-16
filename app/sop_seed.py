from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import SOP


DEMO_SOPS = [
    ("PV-SOP-001", "Processing of Individual Case Safety Reports", "ICSR"),
    ("PV-SOP-002", "ICSR Validity and Seriousness Assessment", "ICSR"),
    ("PV-SOP-003", "Management of Pharmacovigilance Agreements", "PV Agreements"),
    ("PV-SOP-004", "Safety Data Reconciliation with Partners", "Reconciliation"),
    ("PV-SOP-005", "Handling of Incoming Safety Requests", "Incoming Requests"),
    ("PV-SOP-006", "Pharmacovigilance System Master File Maintenance", "PSMF"),
    ("PV-SOP-007", "PSUR/PBRER Preparation and Submission", "PSUR/PBRER"),
    ("PV-SOP-008", "Literature Monitoring", "Literature Monitoring"),
    ("PV-SOP-009", "Signal Management", "Signal Management"),
    ("PV-SOP-010", "Pharmacovigilance Training", "Training"),
]


def ensure_sop_demo_data(db: Session) -> None:
    if db.query(SOP).filter(SOP.is_deleted.is_(False)).first():
        return

    today = date.today()
    for index, (code, title, process_area) in enumerate(DEMO_SOPS, start=1):
        status = "Effective"
        if index in {2, 8}:
            status = "Requires Review"
        elif index == 10:
            status = "Under Approval"
        db.add(
            SOP(
                sop_code=code,
                title=title,
                document_type="SOP",
                version="1.0",
                status=status,
                process_area=process_area,
                owner="PV Responsible",
                reviewer="QA Reviewer",
                approver="PV Responsible",
                approval_date=today - timedelta(days=30 + index),
                effective_date=today - timedelta(days=30),
                next_review_date=today + timedelta(days=30 * index),
                revision_reason="Initial MVP demo record.",
                description="Demo SOP record for the MVP SOP register.",
                training_required=index in {1, 2, 4, 5, 10},
            )
        )
    db.commit()
