"""Verification and correction system for task completion detection."""

from soren.verification.baseline import BaselineManager
from soren.verification.correction import (
    CorrectionStrategy,
    apply_correction,
    escalate_corrections,
)
from soren.verification.verifier import ActionVerifier, VerificationResult

__all__ = [
    "ActionVerifier",
    "BaselineManager",
    "CorrectionStrategy",
    "VerificationResult",
    "apply_correction",
    "escalate_corrections",
]
