from dataclasses import replace

from target_discovery.agents import (
    CompetitiveLandscapeAgent,
    DiseaseProfileAgent,
    EvidenceAgent,
    GeneticEvidenceAgent,
    InternalAlignmentAgent,
    ModelCoverageAgent,
    OmicsAssemblyAgent,
    PathwayNetworkAgent,
    PredictionValidationAgent,
)
from target_discovery.connectors import PublicEvidenceSources
from target_discovery.models import (
    AgentResult,
    ScoreWeights,
    TargetAssessment,
    TargetHypothesis,
)
from target_discovery.quality import evidence_quality_findings
from target_discovery.scoring import combine_scores


class TargetDiscoveryOrchestrator:
    def __init__(
        self,
        agents: list[EvidenceAgent] | None = None,
        uploaded_rows: list[dict[str, str]] | None = None,
    ) -> None:
        if agents is None:
            sources = PublicEvidenceSources()
            agents = [
                DiseaseProfileAgent(sources),
                InternalAlignmentAgent(uploaded_rows),
                OmicsAssemblyAgent(sources),
                GeneticEvidenceAgent(sources),
                PathwayNetworkAgent(sources),
                PredictionValidationAgent(uploaded_rows),
                CompetitiveLandscapeAgent(sources),
                ModelCoverageAgent(uploaded_rows),
            ]
        self.agents = agents

    def assess(
        self,
        target: TargetHypothesis,
        phenotype: str = "obesity",
    ) -> TargetAssessment:
        results = []
        for agent in self.agents:
            try:
                results.append(agent.collect(target, phenotype))
            except Exception as exc:
                results.append(AgentResult(
                    agent=getattr(agent, "name", type(agent).__name__),
                    status="error",
                    missing_sources=["Agent output"],
                    warnings=[f"Unexpected agent error: {exc}"],
                ))

        records = [record for result in results for record in result.records]
        findings = evidence_quality_findings(target.symbol, records, results)
        return TargetAssessment(
            target=target,
            agent_results=tuple(results),
            qc_findings=tuple(findings),
        )


def score_assessment(
    assessment: TargetAssessment,
    biology: float,
    novelty: float,
    feasibility: float,
    weights: ScoreWeights,
) -> TargetAssessment:
    breakdown = combine_scores(biology, novelty, feasibility, weights)
    return replace(
        assessment,
        biology_score=breakdown.biology,
        novelty_score=breakdown.novelty,
        feasibility_score=breakdown.feasibility,
        overall_score=breakdown.overall,
    )