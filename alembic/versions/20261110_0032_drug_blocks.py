"""Repeatable drug blocks; preserve legacy scalar evidence without inference."""
import json
import uuid
from datetime import datetime, timezone
import sqlalchemy as sa
from alembic import op

revision = "20261110_0032"
down_revision = "20261110_0031"
branch_labels = None
depends_on = None


def common():
    return [sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("is_active",sa.Boolean(),nullable=False), sa.Column("is_deleted",sa.Boolean(),nullable=False),
        sa.Column("deleted_at",sa.DateTime(timezone=True)),sa.Column("deleted_by",sa.String(36)),
        sa.Column("delete_reason",sa.Text()),sa.Column("version",sa.Integer(),nullable=False)]


def upgrade():
    op.add_column("tblCaseProducts", sa.Column("coded_identifiers_json",sa.Text(),nullable=True))
    op.add_column("tblCaseProducts", sa.Column("interpretation_profile",sa.String(64),nullable=True))
    op.add_column("tblCaseProducts", sa.Column("source_facts_json",sa.Text(),nullable=True))
    op.add_column("tblCaseProducts", sa.Column("reviewed_fields_json",sa.Text(),nullable=True))
    op.add_column("tblCaseDrugSubstances", sa.Column("identifier_code",sa.Text(),nullable=True))
    op.add_column("tblCaseDrugSubstances", sa.Column("identifier_system",sa.Text(),nullable=True))
    op.add_column("tblCaseDrugSubstances", sa.Column("identifier_version",sa.Text(),nullable=True))
    op.add_column("tblCaseDrugSubstances", sa.Column("denominator_value",sa.Text(),nullable=True))
    op.add_column("tblCaseDrugSubstances", sa.Column("denominator_unit",sa.Text(),nullable=True))
    op.add_column("tblCaseDrugSubstances", sa.Column("quantity_null_flavor",sa.Text(),nullable=True))
    op.add_column("tblCaseDrugSubstances", sa.Column("interpretation_profile",sa.String(64),nullable=True))
    op.add_column("tblCaseDrugSubstances", sa.Column("source_facts_json",sa.Text(),nullable=True))
    op.add_column("tblCaseDrugSubstances", sa.Column("reviewed_fields_json",sa.Text(),nullable=True))
    op.add_column("tblCaseProductReactionAssessments", sa.Column("assessment_source",sa.Text(),nullable=True))
    op.add_column("tblCaseProductReactionAssessments", sa.Column("assessment_method_text",sa.Text(),nullable=True))
    op.add_column("tblCaseProductReactionAssessments", sa.Column("interpretation_profile",sa.String(64),nullable=True))
    op.add_column("tblCaseProductReactionAssessments", sa.Column("source_facts_json",sa.Text(),nullable=True))
    op.add_column("tblCaseProductReactionAssessments", sa.Column("reviewed_fields_json",sa.Text(),nullable=True))
    op.create_table("tblCaseDrugDosages", *common(),
        sa.Column("case_product_id",sa.String(36),sa.ForeignKey("tblCaseProducts.id"),nullable=False),
        sa.Column("dose_value",sa.Text(),nullable=True),
        sa.Column("dose_unit",sa.Text(),nullable=True),
        sa.Column("dose_numerator_value",sa.Text(),nullable=True),
        sa.Column("dose_numerator_unit",sa.Text(),nullable=True),
        sa.Column("dose_denominator_value",sa.Text(),nullable=True),
        sa.Column("dose_denominator_unit",sa.Text(),nullable=True),
        sa.Column("interval_value",sa.Text(),nullable=True),
        sa.Column("interval_unit",sa.Text(),nullable=True),
        sa.Column("frequency_text",sa.Text(),nullable=True),
        sa.Column("route_text",sa.Text(),nullable=True),
        sa.Column("route_code",sa.Text(),nullable=True),
        sa.Column("route_system",sa.Text(),nullable=True),
        sa.Column("route_version",sa.Text(),nullable=True),
        sa.Column("route_null_flavor",sa.Text(),nullable=True),
        sa.Column("start_raw",sa.Text(),nullable=True),
        sa.Column("start_null_flavor",sa.Text(),nullable=True),
        sa.Column("end_raw",sa.Text(),nullable=True),
        sa.Column("end_null_flavor",sa.Text(),nullable=True),
        sa.Column("duration_value",sa.Text(),nullable=True),
        sa.Column("duration_unit",sa.Text(),nullable=True),
        sa.Column("dosage_text",sa.Text(),nullable=True),
        sa.Column("dosage_form_text",sa.Text(),nullable=True),
        sa.Column("dosage_form_code",sa.Text(),nullable=True),
        sa.Column("dosage_form_system",sa.Text(),nullable=True),
        sa.Column("dosage_form_version",sa.Text(),nullable=True),
        sa.Column("batch_lot_number",sa.Text(),nullable=True),
        sa.Column("parent_route_text",sa.Text(),nullable=True),
        sa.Column("parent_route_code",sa.Text(),nullable=True),
        sa.Column("parent_route_system",sa.Text(),nullable=True),
        sa.Column("parent_route_version",sa.Text(),nullable=True),
        sa.Column("interpretation_profile",sa.String(64),nullable=True),
        sa.Column("source_facts_json",sa.Text(),nullable=True),
        sa.Column("reviewed_fields_json",sa.Text(),nullable=True),
    )
    op.create_index("ix_tblCaseDrugDosages_case_product_id","tblCaseDrugDosages",["case_product_id"])
    op.create_table("tblCaseDrugIndications", *common(),
        sa.Column("case_product_id",sa.String(36),sa.ForeignKey("tblCaseProducts.id"),nullable=False),
        sa.Column("reported_text",sa.Text(),nullable=True),
        sa.Column("meddra_llt_code",sa.Text(),nullable=True),
        sa.Column("meddra_version",sa.Text(),nullable=True),
        sa.Column("legacy_meddra_pt_code",sa.Text(),nullable=True),
        sa.Column("null_flavor",sa.Text(),nullable=True),
        sa.Column("interpretation_profile",sa.String(64),nullable=True),
        sa.Column("source_facts_json",sa.Text(),nullable=True),
        sa.Column("reviewed_fields_json",sa.Text(),nullable=True),
    )
    op.create_index("ix_tblCaseDrugIndications_case_product_id","tblCaseDrugIndications",["case_product_id"])
    op.create_table("tblCaseDrugRechallenges", *common(),
        sa.Column("case_product_id",sa.String(36),sa.ForeignKey("tblCaseProducts.id"),nullable=False),
        sa.Column("reaction_id",sa.String(36),sa.ForeignKey("tblReactions.id"),nullable=False),
        sa.Column("rechallenge_code",sa.Text(),nullable=True),
        sa.Column("code_system",sa.Text(),nullable=True),
        sa.Column("code_version",sa.Text(),nullable=True),
        sa.Column("null_flavor",sa.Text(),nullable=True),
        sa.Column("reported_text",sa.Text(),nullable=True),
        sa.Column("interpretation_profile",sa.String(64),nullable=True),
        sa.Column("source_facts_json",sa.Text(),nullable=True),
        sa.Column("reviewed_fields_json",sa.Text(),nullable=True),
    )
    op.create_index("ix_tblCaseDrugRechallenges_case_product_id","tblCaseDrugRechallenges",["case_product_id"])
    op.create_index("ix_tblCaseDrugRechallenges_reaction_id","tblCaseDrugRechallenges",["reaction_id"])
    connection = op.get_bind()
    metadata = sa.MetaData()
    products = sa.Table("tblCaseProducts", metadata, autoload_with=connection)
    doses = sa.Table("tblCaseDrugDosages", metadata, autoload_with=connection)
    indications = sa.Table("tblCaseDrugIndications", metadata, autoload_with=connection)
    dose_map = dict(dose_value="dose_value",dose_unit="dose_unit",route="route_text",frequency="frequency_text",
        duration_text="dosage_text",dosage_form="dosage_form_text",batch_lot_number="batch_lot_number")
    old_fields = list(dose_map) + ["therapy_start_date","therapy_start_date_raw","therapy_start_date_precision",
        "therapy_end_date","therapy_end_date_raw","therapy_end_date_precision"]
    for row in connection.execute(sa.select(products)).mappings():
        common_values = dict(id=str(uuid.uuid4()),case_product_id=row["id"],created_at=row["created_at"],
            updated_at=row["updated_at"],is_active=row["is_active"],is_deleted=row["is_deleted"],version=1,
            interpretation_profile="legacy_single_unverified")
        if any(row[k] is not None and row[k] != "" for k in old_fields):
            source = {k:row[k] for k in old_fields}
            values = {new:row[old] for old,new in dose_map.items()}
            for side in ("start","end"):
                raw = row[f"therapy_{side}_date_raw"]
                value = row[f"therapy_{side}_date"]
                values[side+"_raw"] = raw if raw else (value.strftime("%Y%m%d") if value else None)
            connection.execute(doses.insert().values(**common_values,**values,
                source_facts_json=json.dumps(dict(origin="legacy_columns",values=source),default=str,ensure_ascii=False)))
        if any(row[k] is not None and row[k] != "" for k in ("indication_text","indication_meddra_pt_code")):
            common_values["id"] = str(uuid.uuid4())
            connection.execute(indications.insert().values(**common_values,reported_text=row["indication_text"],
                legacy_meddra_pt_code=row["indication_meddra_pt_code"],source_facts_json=json.dumps(
                    dict(origin="legacy_columns",indication_text=row["indication_text"],
                         indication_meddra_pt_code=row["indication_meddra_pt_code"]),ensure_ascii=False)))
    # Parent scalars are retained as read-only legacy snapshots. Rechallenge is NOT copied to pairs.


def downgrade():
    raise RuntimeError("Repeatable drug data cannot be folded into one scalar without loss; use a reviewed restore.")
