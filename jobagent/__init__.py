"""JobAgent — LinkedIn Easy Apply pipeline."""

from .classify import CachingClassifier, mock_llm
from .detect import detect_from_html, detect_from_page
from .policy import Policy, PolicyConfig
from .schema import (
    ClassificationResponse,
    Decision,
    FieldClassification,
    FormField,
    ResumeProfile,
    ResumeSection,
)

__all__ = [
    "CachingClassifier",
    "ClassificationResponse",
    "Decision",
    "FieldClassification",
    "FormField",
    "Policy",
    "PolicyConfig",
    "ResumeProfile",
    "ResumeSection",
    "detect_from_html",
    "detect_from_page",
    "mock_llm",
]
