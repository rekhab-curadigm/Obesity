from dataclasses import dataclass

from target_discovery.models import ScoreWeights


@dataclass(frozen=True)
class ScoreBreakdown:
    biology: float
    novelty: float
    feasibility: float
    overall: float


def combine_scores(
    biology: float,
    novelty: float,
    feasibility: float,
    weights: ScoreWeights = ScoreWeights(),
) -> ScoreBreakdown:
    components = (biology, novelty, feasibility)
    if any(not 0 <= score <= 100 for score in components):
        raise ValueError("Component scores must be between 0 and 100.")

    overall = (
        biology * weights.biology
        + novelty * weights.novelty
        + feasibility * weights.feasibility
    )
    return ScoreBreakdown(
        biology=biology,
        novelty=novelty,
        feasibility=feasibility,
        overall=round(overall, 1),
    )