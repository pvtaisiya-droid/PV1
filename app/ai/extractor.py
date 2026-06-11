from dataclasses import dataclass, field
from typing import Any

from app.ai.prompts import SAFETY_REPORT_EXTRACTION_PROMPT


@dataclass
class ExtractionDraft:
    source_text: str
    gpt_json_output: dict[str, Any] = field(default_factory=dict)
    extraction_status: str = "not_configured"
    gpt_extracted: bool = False
    human_confirmed: bool = False
    prompt: str = SAFETY_REPORT_EXTRACTION_PROMPT


def draft_extraction(source_text: str) -> ExtractionDraft:
    return ExtractionDraft(
        source_text=source_text,
        gpt_json_output={
            "missing_information": ["AI extraction provider is not configured"],
            "validity_assessment": "human_review_required",
        },
    )
