SAFETY_REPORT_EXTRACTION_PROMPT = """
Extract structured pharmacovigilance data from the source text.

Return JSON only with these keys:
- patient_initials
- patient_age
- patient_sex
- country
- reporter_type
- reporter_name
- suspect_product
- active_substance
- adverse_event_term
- event_onset_date
- seriousness
- seriousness_criteria
- outcome
- case_narrative
- missing_information
- validity_assessment

Do not mark the extraction as final. Human review is required before saving.
"""
