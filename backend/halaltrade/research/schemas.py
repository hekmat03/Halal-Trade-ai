"""Research-engine schemas — recommendations, never orders."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..models import PipelineResult, Side, Signal

__all__ = ["ResearchRecommendation", "ResearchReport"]


class ResearchRecommendation(BaseModel):
    """One ranked research candidate: advisory only, never executable.

    ``signal`` is the candidate recommendation (what the strategy suggested).
    ``pipeline`` is the gate outcome that signal WOULD face — the only route
    to any trade. ``executable`` is always False: research output can never
    bypass the gates and this object carries no order, no placement, no
    authorization.
    """

    strategy: str
    regime: str
    direction: Side
    signal: Signal
    pipeline: PipelineResult
    gate_passed: bool
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    rationale: str = ""
    risk_notes: list[str] = Field(default_factory=list)
    llm_summary: str = ""
    executable: bool = False  # ALWAYS False — research never executes.

    model_config = {"frozen": False}


class ResearchReport(BaseModel):
    """The ranked recommendation list from one research pass."""

    regime: str
    regime_confidence: float = 0.0
    regime_evidence: list[str] = Field(default_factory=list)
    recommendations: list[ResearchRecommendation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    llm_used: bool = False
    llm_model: str = ""

    def top(self, n: int = 1) -> list[ResearchRecommendation]:
        return list(self.recommendations[: max(0, n)])

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        return super().model_dump(**kwargs)
