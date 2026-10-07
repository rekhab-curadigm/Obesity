from __future__ import annotations

from typing import Protocol

from target_discovery.connectors import PublicEvidenceSources, SourceError
from target_discovery.models import AgentResult, EvidenceRecord, TargetHypothesis


class EvidenceAgent(Protocol):
    name: str

    def collect(self, target: TargetHypothesis, phenotype: str) -> AgentResult: ...


def _status(records: list[EvidenceRecord], errors: list[str]) -> str:
    if errors and records:
        return "partial"
    if errors:
        return "error"
    return "complete" if records else "no_hits"


class DiseaseProfileAgent:
    name = "Disease profile"

    def __init__(self, sources: PublicEvidenceSources) -> None:
        self.sources = sources

    def collect(self, target: TargetHypothesis, phenotype: str) -> AgentResult:
        records: list[EvidenceRecord] = []
        errors: list[str] = []
        try:
            for paper in self.sources.pubmed(target.symbol, phenotype):
                records.append(EvidenceRecord(
                    target=target.symbol,
                    domain="disease biology",
                    evidence_type="PubMed indexed paper",
                    source="PubMed",
                    title=paper["title"],
                    url=paper["url"],
                    summary=(
                        f"Journal: {paper['journal']}; publication date: {paper['date']}. "
                        f"Abstract: {paper.get('abstract') or 'Not retrieved.'}"
                    ),
                    identifiers={"PMID": paper["id"]},
                    caveats=("Title/abstract query hit; relevance requires scientific review.",),
                ))
        except SourceError as exc:
            errors.append(f"PubMed: {exc}")
        return AgentResult(
            agent=self.name,
            status=_status(records, errors),
            records=records,
            missing_sources=["PubMed"] if errors else [],
            warnings=errors,
        )


class OmicsAssemblyAgent:
    name = "Omics assembly"

    def __init__(self, sources: PublicEvidenceSources) -> None:
        self.sources = sources

    def collect(self, target: TargetHypothesis, phenotype: str) -> AgentResult:
        try:
            studies = self.sources.geo_studies(target.symbol, phenotype)
            records = [EvidenceRecord(
                target=target.symbol,
                domain="omics",
                evidence_type="GEO study metadata match",
                source="NCBI GEO",
                title=study["title"],
                url=study["url"],
                summary=study["summary"],
                identifiers={"GEO": study["id"]},
                caveats=(
                    "Metadata search is not a differential-expression result; inspect the study design and data.",
                ),
            ) for study in studies]
            return AgentResult(
                agent=self.name,
                status=_status(records, []),
                records=records,
                missing_sources=[] if studies else ["GEO processed-data analysis"],
            )
        except SourceError as exc:
            return AgentResult(
                agent=self.name,
                status="error",
                missing_sources=["NCBI GEO"],
                warnings=[str(exc)],
            )


class GeneticEvidenceAgent:
    name = "Genetic evidence"

    def __init__(self, sources: PublicEvidenceSources) -> None:
        self.sources = sources

    def collect(self, target: TargetHypothesis, phenotype: str) -> AgentResult:
        records: list[EvidenceRecord] = []
        errors: list[str] = []
        try:
            for hit in self.sources.gwas_catalog(target.symbol, phenotype):
                records.append(EvidenceRecord(
                    target=target.symbol,
                    domain="genetic association",
                    evidence_type="GWAS Catalog association",
                    source="GWAS Catalog",
                    title=hit["title"],
                    url=hit["url"],
                    summary=hit.get("summary", "GWAS Catalog result for manual validation."),
                    caveats=(
                        "GWAS Catalog results require manual review for trait specificity, locus context, and whether the locus maps to the target gene.",
                    ),
                ))
        except SourceError as exc:
            errors.append(f"GWAS Catalog: {exc}")
        try:
            for hit in self.sources.qtl_catalog(target.symbol):
                records.append(EvidenceRecord(
                    target=target.symbol,
                    domain="genetic regulation",
                    evidence_type="QTL / eQTL evidence",
                    source="GTEx / QTL",
                    title=hit["title"],
                    url=hit["url"],
                    summary=hit.get("summary", "QTL evidence page for manual validation."),
                    caveats=(
                        "QTL overlap does not establish causality for adiposity; inspect tissue and effect direction manually.",
                    ),
                ))
        except SourceError as exc:
            errors.append(f"GTEx / QTL: {exc}")
        return AgentResult(
            agent=self.name,
            status=_status(records, errors),
            records=records,
            missing_sources=[
                name for name, failed in (
                    ("GWAS Catalog", "GWAS Catalog:"),
                    ("GTEx / QTL", "GTEx / QTL:"),
                )
                if any(error.startswith(failed) for error in errors)
            ],
            warnings=errors,
        )


class PathwayNetworkAgent:
    name = "Pathway and network"

    def __init__(self, sources: PublicEvidenceSources) -> None:
        self.sources = sources

    def collect(self, target: TargetHypothesis, phenotype: str) -> AgentResult:
        records: list[EvidenceRecord] = []
        errors: list[str] = []
        try:
            for association in self.sources.open_targets(target.symbol, phenotype):
                records.append(EvidenceRecord(
                    target=target.symbol,
                    domain="target-disease biology",
                    evidence_type="integrated target-disease association",
                    source="Open Targets",
                    title=f"{association['target_name']} — {association['name']}",
                    url=association["url"],
                    summary=(
                        f"Integrated association score: {association['score']}; "
                        f"target ID: {association['target_id']}."
                    ),
                    identifiers={
                        "Ensembl target": association["target_id"],
                        "Disease": association["id"],
                    },
                    caveats=(
                        "Open Targets aggregates multiple evidence types; this is not independent replication or proof of causality.",
                    ),
                ))
        except SourceError as exc:
            errors.append(f"Open Targets: {exc}")
        try:
            for pathway in self.sources.reactome(target.symbol):
                records.append(EvidenceRecord(
                    target=target.symbol,
                    domain="pathway context",
                    evidence_type="Reactome name match",
                    source="Reactome",
                    title=pathway["title"],
                    url=pathway["url"],
                    summary="Pathway search result matched the target symbol.",
                    identifiers={"Reactome": pathway["id"]},
                    caveats=("A name match does not establish causal involvement in obesity.",),
                ))
        except SourceError as exc:
            errors.append(f"Reactome: {exc}")
        try:
            network = self.sources.string_network(target.symbol)
            if network:
                partners = ", ".join(
                    f"{row['partner']} (score {row['score']})" for row in network
                )
                records.append(EvidenceRecord(
                    target=target.symbol,
                    domain="network context",
                    evidence_type="protein interaction neighborhood",
                    source="STRING",
                    title=f"STRING network for {target.symbol}",
                    url=network[0]["url"],
                    summary=f"Returned network partners: {partners}.",
                    caveats=(
                        "Network association is not target-disease causality; scores are not effect sizes.",
                    ),
                ))
        except SourceError as exc:
            errors.append(f"STRING: {exc}")
        return AgentResult(
            agent=self.name,
            status=_status(records, errors),
            records=records,
            missing_sources=[
                name for name, failed in (
                    ("Open Targets", "Open Targets:"),
                    ("Reactome", "Reactome:"),
                    ("STRING", "STRING:"),
                )
                if any(error.startswith(failed) for error in errors)
            ],
            warnings=errors,
        )


class CompetitiveLandscapeAgent:
    name = "Competitive landscape"

    def __init__(self, sources: PublicEvidenceSources) -> None:
        self.sources = sources

    def collect(self, target: TargetHypothesis, phenotype: str) -> AgentResult:
        try:
            trials = self.sources.clinical_trials(target.symbol, phenotype)
            records = [EvidenceRecord(
                target=target.symbol,
                domain="competitive landscape",
                evidence_type="Clinical trial registry mention",
                source="ClinicalTrials.gov",
                title=trial["title"],
                url=trial["url"],
                summary=(
                    f"Status: {trial['status']}; phase: {trial['phase']}; "
                    f"matched target/intervention terms: {trial['matched_terms']}; "
                    f"interventions: {trial['interventions'] or 'not reported'}.") ,
                identifiers={"NCT": trial["id"]},
                caveats=(
                    "Registry search mention does not confirm the target is the intervention mechanism.",
                ),
            ) for trial in trials]
            return AgentResult(
                agent=self.name,
                status=_status(records, []),
                records=records,
                missing_sources=[] if trials else ["Clinical pipeline and patent landscape"],
            )
        except SourceError as exc:
            return AgentResult(
                agent=self.name,
                status="error",
                missing_sources=["ClinicalTrials.gov"],
                warnings=[str(exc)],
            )


class _UploadedEvidenceAgent:
    name = "Uploaded evidence"
    domain = "internal"
    source_label = "Uploaded evidence CSV"
    agent_tags: tuple[str, ...] = ()

    def __init__(self, rows: list[dict[str, str]] | None = None) -> None:
        self.rows = rows or []

    def collect(self, target: TargetHypothesis, phenotype: str) -> AgentResult:
        del phenotype
        records = []
        for row in self.rows:
            row_agent = row.get("agent", "").strip().casefold()
            row_domain = row.get("domain", "").strip().casefold()
            if row_agent:
                if row_agent not in self.agent_tags:
                    continue
            elif not any(tag in row_domain for tag in self.agent_tags):
                continue
            if row.get("target", "").strip().casefold() != target.symbol.casefold():
                continue
            records.append(EvidenceRecord(
                target=target.symbol,
                domain=row.get("domain", self.domain).strip() or self.domain,
                evidence_type=row.get("evidence_type", "uploaded evidence").strip(),
                source=row.get("source", self.source_label).strip(),
                title=row.get("title", "Untitled evidence").strip(),
                url=row.get("url", "").strip(),
                summary=row.get("summary", "").strip(),
                evidence_level=_optional_float(row.get("evidence_level", "")),
                identifiers={
                    key: value.strip()
                    for key, value in row.items()
                    if key.startswith("id_") and value.strip()
                },
            ))
        return AgentResult(
            agent=self.name,
            status=_status(records, []),
            records=records,
            missing_sources=(
                [] if records else [self.source_label]
            ),
        )


class InternalAlignmentAgent(_UploadedEvidenceAgent):
    name = "Corporate/internal alignment"
    domain = "internal alignment"
    source_label = "Internal evidence file"
    agent_tags = ("internal", "internal alignment", "corporate/internal alignment")


class PredictionValidationAgent(_UploadedEvidenceAgent):
    name = "Prediction and validation"
    domain = "prediction and validation"
    source_label = "Prediction evidence file"
    agent_tags = ("prediction", "prediction and validation")


class ModelCoverageAgent(_UploadedEvidenceAgent):
    name = "Model coverage"
    domain = "model coverage"
    source_label = "Model coverage evidence file"
    agent_tags = ("model", "model coverage")


def _optional_float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None