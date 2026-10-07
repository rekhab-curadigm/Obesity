from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class EvidenceRecord:
    target: str
    domain: str
    evidence_type: str
    source: str
    title: str
    url: str
    summary: str
    retrieved_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    evidence_level: float | None = None
    identifiers: dict[str, str] = field(default_factory=dict)
    caveats: tuple[str, ...] = ()


@dataclass
class AgentResult:
    agent: str
    status: str
    records: list[EvidenceRecord] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TargetHypothesis:
    symbol: str
    hypothesis: str
    novelty_class: str = "unassigned"


@dataclass(frozen=True)
class ScoreWeights:
    biology: float = 0.45
    novelty: float = 0.35
    feasibility: float = 0.20

    def __post_init__(self) -> None:
        values = (self.biology, self.novelty, self.feasibility)
        if any(value < 0 or value > 1 for value in values):
            raise ValueError("Score weights must be between 0 and 1.")
        if abs(sum(values) - 1.0) > 1e-9:
            raise ValueError("Score weights must sum to 1.0.")


@dataclass(frozen=True)
class TargetAssessment:
    target: TargetHypothesis
    agent_results: tuple[AgentResult, ...]
    biology_score: float | None = None
    novelty_score: float | None = None
    feasibility_score: float | None = None
    overall_score: float | None = None
    maturity_classification: str = "Needs classification"
    qc_findings: tuple[str, ...] = ()
    review_status: str = "Needs scientific review"
    metadata: dict[str, Any] = field(default_factory=dict)