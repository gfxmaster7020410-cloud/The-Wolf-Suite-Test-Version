"""
Enterprise Governance In-Flight PII Redaction Engine.
Uses Microsoft Presidio when available; falls back to regex masking otherwise.
"""

import re
from typing import Optional


class PIIRedactor:
    """
    Sanitizes text prompts and contextual payloads before LLM dispatch
    or vector embedding persistence.
    """

    EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b")
    CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

    def __init__(self):
        self._analyzer = None
        self._anonymizer = None
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine

            self._analyzer = AnalyzerEngine()
            self._anonymizer = AnonymizerEngine()
        except ImportError:
            # Lightweight regex fallback for local/dev without Presidio deps
            pass

    def sanitize_prompt(self, raw_text: str) -> str:
        if not raw_text:
            return raw_text

        if self._analyzer and self._anonymizer:
            return self._sanitize_presidio(raw_text)
        return self._sanitize_regex(raw_text)

    def _sanitize_presidio(self, raw_text: str) -> str:
        from presidio_anonymizer import OperatorConfig

        results = self._analyzer.analyze(
            text=raw_text,
            entities=["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD"],
            language="en",
        )
        anonymized = self._anonymizer.anonymize(
            text=raw_text,
            analyzer_results=results,
            operators={
                "DEFAULT": OperatorConfig("replace", {"new_value": "<REDACTED>"}),
                "EMAIL_ADDRESS": OperatorConfig(
                    "mask",
                    {"chars_to_mask": 6, "masking_char": "*", "from_end": False},
                ),
            },
        )
        return anonymized.text

    def _sanitize_regex(self, raw_text: str) -> str:
        text = self.EMAIL_RE.sub(lambda m: m.group(0)[:2] + "******" + m.group(0)[m.group(0).rfind("@"):], raw_text)
        text = self.PHONE_RE.sub("<PHONE_REDACTED>", text)
        text = self.CARD_RE.sub("<CARD_REDACTED>", text)
        return text
