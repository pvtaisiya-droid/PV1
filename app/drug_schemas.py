"""Write contracts for the bounded case drug block; no dictionary validation claims."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from decimal import Decimal, InvalidOperation


class DrugWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_reason: str | None = Field(default=None,max_length=2000)

    @model_validator(mode="after")
    def lexical_numbers(self):
        for key in self.model_fields_set:
            value = getattr(self,key)
            if value is not None and key in {"dose_value","dose_numerator_value","dose_denominator_value",
                    "interval_value","duration_value","strength_value","denominator_value"}:
                try:
                    if not Decimal(value).is_finite(): raise InvalidOperation()
                except InvalidOperation:
                    raise ValueError("Enter a numeric value separately from the unit/text")
        return self


class DrugIdentifier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str | None = None
    code_system: str | None = None
    version: str | None = None
    null_flavor: str | None = None
    display_name: str | None = None


class DrugProductWrite(DrugWrite):
    product_id: str | None = None
    reported_product_name: str | None = None
    drug_role: Literal["unknown","suspect","concomitant","interacting","not_administered"] | None = None
    authorization_number: str | None = None
    authorization_country_code: str | None = None
    authorization_holder: str | None = None
    action_taken: str | None = None
    coded_identifiers: list[DrugIdentifier] | None = None


class DrugDosageWrite(DrugWrite):
    dose_value: str | None = None
    dose_unit: str | None = None
    dose_numerator_value: str | None = None
    dose_numerator_unit: str | None = None
    dose_denominator_value: str | None = None
    dose_denominator_unit: str | None = None
    interval_value: str | None = None
    interval_unit: str | None = None
    frequency_text: str | None = None
    route_text: str | None = None
    route_code: str | None = None
    route_system: str | None = None
    route_version: str | None = None
    route_null_flavor: str | None = None
    start_raw: str | None = None
    start_null_flavor: str | None = None
    end_raw: str | None = None
    end_null_flavor: str | None = None
    duration_value: str | None = None
    duration_unit: str | None = None
    dosage_text: str | None = None
    dosage_form_text: str | None = None
    dosage_form_code: str | None = None
    dosage_form_system: str | None = None
    dosage_form_version: str | None = None
    batch_lot_number: str | None = None
    parent_route_text: str | None = None
    parent_route_code: str | None = None
    parent_route_system: str | None = None
    parent_route_version: str | None = None


class DrugIndicationWrite(DrugWrite):
    reported_text: str | None = None
    meddra_llt_code: str | None = None
    meddra_version: str | None = None
    null_flavor: str | None = None


class DrugSubstanceWrite(DrugWrite):
    substance_id: str | None = None
    reported_substance_name: str | None = None
    strength_value: str | None = None
    strength_unit: str | None = None
    identifier_code: str | None = None
    identifier_system: str | None = None
    identifier_version: str | None = None
    denominator_value: str | None = None
    denominator_unit: str | None = None
    quantity_null_flavor: str | None = None


class DrugAssessmentWrite(DrugWrite):
    reaction_id: str | None = None
    assessment_source: str | None = None
    assessment_method_text: str | None = None
    relatedness: str | None = None
    assessment_comment: str | None = None


class DrugRechallengeWrite(DrugWrite):
    reaction_id: str | None = None
    rechallenge_code: str | None = None
    code_system: str | None = None
    code_version: str | None = None
    null_flavor: str | None = None
    reported_text: str | None = None


SCHEMAS = {'product': DrugProductWrite, 'dosage': DrugDosageWrite, 'indication': DrugIndicationWrite, 'substance': DrugSubstanceWrite, 'assessment': DrugAssessmentWrite, 'rechallenge': DrugRechallengeWrite}
