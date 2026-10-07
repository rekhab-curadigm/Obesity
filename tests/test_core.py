import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from target_discovery.models import (
    AgentResult,
    EvidenceRecord,
    ScoreWeights,
    TargetAssessment,
    TargetHypothesis,
)
from target_discovery.agents import (
    GeneticEvidenceAgent,
    InternalAlignmentAgent,
    ModelCoverageAgent,
    PathwayNetworkAgent,
)
from target_discovery.connectors import (
    PublicEvidenceSources,
    _matching_disease_rows,
    _matched_trial_terms,
)
from target_discovery.catalog import (
    catalog_rows,
    classify_catalog_target,
    parse_gene_symbols,
)
from target_discovery.orchestrator import TargetDiscoveryOrchestrator
from target_discovery.package_io import (
    assessments_from_package,
    evidence_csv_contents,
    evidence_workbook_bytes,
    load_latest_gene_catalog,
    save_evidence_csvs,
    save_gene_catalog,
)
from target_discovery.quality import evidence_quality_findings
from target_discovery.scoring import combine_scores
from target_discovery.prioritization import (
    rank_emerging_novel_targets,
    summarize_target_priority,
)
from app import (
    append_selected_gene_symbols,
    append_unique_target_lines,
    build_saturation_chart_spec,
    build_saturation_rows,
    category_filter_options,
    evidence_matrix_pubmed_entry,
    evidence_matrix_strength,
    filter_assessments_by_query,
    filter_rows_by_categories,
    filter_gene_rows_by_search,
    parse_targets,
    priority_evidence_type_coverage,
    pubmed_evidence_records,
)


class ScoringTests(unittest.TestCase):
    def test_provisional_weights_are_explicit(self) -> None:
        result = combine_scores(80, 60, 50)
        self.assertEqual(result.overall, 67.0)

    def test_weights_must_sum_to_one(self) -> None:
        with self.assertRaises(ValueError):
            ScoreWeights(biology=0.5, novelty=0.3, feasibility=0.3)


class TargetSelectionTests(unittest.TestCase):
    def test_selected_catalog_genes_append_to_manual_shortlist_without_duplicates(self) -> None:
        result = append_selected_gene_symbols(
            "adcy3, inhbe",
            ["INHBE", "GPR15"],
        )

        self.assertEqual(result, "ADCY3, INHBE, GPR15")

    def test_selected_catalog_genes_respect_shortlist_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceed the 20-gene shortlist limit"):
            append_selected_gene_symbols(
                ", ".join(f"GENE{i}" for i in range(20)),
                ["GENE21"],
            )

    def test_appending_selected_genes_preserves_existing_targets_without_duplicates(self) -> None:
        existing = "ADCY3 | Existing hypothesis | emerging"
        added = "\n".join([
            "ADCY3 | Replacement hypothesis | emerging",
            "INHBE | Assess INHBE in obesity | emerging",
        ])

        result = append_unique_target_lines(existing, added)

        self.assertEqual(
            result,
            "ADCY3 | Existing hypothesis | emerging\n"
            "INHBE | Assess INHBE in obesity | emerging",
        )


class CategoryFilterTests(unittest.TestCase):
    def test_category_options_include_low_cardinality_columns_only(self) -> None:
        rows = [
            {"Gene": "A", "Class": "Emerging", "Status": "Ready"},
            {"Gene": "B", "Class": "Novel", "Status": "Ready"},
            {"Gene": "C", "Class": "Emerging", "Status": "Ready"},
        ]

        self.assertEqual(
            category_filter_options(rows, max_values=2),
            {"Class": ["Emerging", "Novel"]},
        )

    def test_category_filters_combine_and_treat_missing_values_explicitly(self) -> None:
        rows = [
            {"Gene": "A", "Class": "Emerging"},
            {"Gene": "B", "Class": "Novel"},
            {"Gene": "C", "Class": "Emerging"},
        ]

        result = filter_rows_by_categories(
            rows,
            {"Class": ["Emerging"], "Gene": ["C"]},
        )
        self.assertEqual([row["Gene"] for row in result], ["C"])
        self.assertEqual(
            filter_rows_by_categories([{"Value": None}], {"Value": ["Not available"]}),
            [{"Value": None}],
        )


class QualityTests(unittest.TestCase):
    def test_reports_missing_provenance_and_unassessed_sources(self) -> None:
        record = EvidenceRecord(
            target="GENE1",
            domain="biology",
            evidence_type="genetic association",
            source="",
            title="Study",
            url="",
            summary="",
        )
        result = AgentResult(
            agent="omics",
            status="partial",
            missing_sources=["GEO"],
        )

        findings = evidence_quality_findings("GENE1", [record], [result])

        self.assertTrue(any("lack source or URL" in finding for finding in findings))
        self.assertTrue(any("GEO" in finding for finding in findings))

    def test_broad_obesity_query_excludes_subtype_specific_diseases(self) -> None:
        rows = [
            {"disease": {"name": "obesity disorder"}},
            {"disease": {"name": "obesity due to melanocortin 4 receptor deficiency"}},
        ]

        matches = _matching_disease_rows(rows, "obesity")

        self.assertEqual([row["disease"]["name"] for row in matches], ["obesity disorder"])

    def test_generic_obesity_trial_does_not_count_as_target_evidence(self) -> None:
        study = {
            "protocolSection": {
                "identificationModule": {"briefTitle": "Semaglutide use in obesity"},
                "armsInterventionsModule": {
                    "interventions": [{"name": "Semaglutide"}],
                },
            },
        }

        self.assertEqual(_matched_trial_terms(study, ("GIPR", "tirzepatide")), [])
        self.assertEqual(_matched_trial_terms(study, ("semaglutide",)), ["semaglutide"])

    def test_catalog_classes_are_explicit_and_source_bounded(self) -> None:
        known, known_reason = classify_catalog_target({
            "overall_score": 0.8,
            "datatype_scores": {"known_drug": 0, "genetic_association": 0.7},
        })
        novel, novel_reason = classify_catalog_target({
            "overall_score": 0.1,
            "datatype_scores": {"known_drug": 0, "genetic_association": 0.05},
        })

        self.assertEqual(known, "Known")
        self.assertIn("clinical validation still requires review", known_reason)
        self.assertEqual(novel, "Novel")
        self.assertIn("not proof of no prior work", novel_reason)

    def test_catalog_rows_include_obesity_target_profile_columns(self) -> None:
        rows = catalog_rows({
            "genes": [{
                "gene": "MC4R",
                "gene_name": "melanocortin 4 receptor",
                "overall_score": 0.86,
                "datatype_scores": {
                    "genetic_association": 0.9,
                    "genetic_literature": 0.75,
                    "literature": 0.8,
                    "animal_model": 0.7,
                    "clinical": 0.6,
                    "known_drug": 0.2,
                },
            }]
        })

        self.assertEqual(rows[0]["Target_Type"], "Receptor")
        self.assertIn("Energy Homeostasis", rows[0]["Biological_Domain"])
        self.assertIn("appetite", rows[0]["Obesity_Phenotype"])
        self.assertIn("weight reduction", rows[0]["Clinical_Outcome"])
        self.assertEqual(rows[0]["Genetic literature"], 0.75)
        self.assertEqual(rows[0]["Animal model"], 0.7)
        self.assertEqual(rows[0]["Clinical evidence"], 0.6)
        self.assertEqual(rows[0]["Known drug"], 0.2)

    def test_emerging_novel_ranking_sorts_score_then_evidence_coverage(self) -> None:
        rows = rank_emerging_novel_targets(
            [
                {
                    "Gene": "LOWER",
                    "Provisional class": "Emerging",
                    "Genetic association": 0.4,
                },
                {
                    "Gene": "COVERED",
                    "Provisional class": "Novel",
                    "Genetic association": 0.8,
                    "Literature": 0.8,
                    "Animal model": 0.8,
                },
                {
                    "Gene": "SAME_SCORE",
                    "Provisional class": "Emerging",
                    "Genetic association": 0.8,
                },
                {
                    "Gene": "KNOWN",
                    "Provisional class": "Known",
                    "Genetic association": 1.0,
                },
            ],
            {},
        )

        self.assertEqual(
            [row["Gene"] for row in rows],
            ["COVERED", "SAME_SCORE", "LOWER"],
        )
        self.assertEqual(rows[0]["Rank"], 1)
        self.assertEqual(rows[0]["Available score categories"], 2)
        self.assertEqual(rows[0]["Target Priority Score"], rows[1]["Target Priority Score"])


class OrchestratorTests(unittest.TestCase):
    def test_agent_failures_are_retained_as_qc_findings(self) -> None:
        class FailingAgent:
            name = "test source"

            def collect(self, target: TargetHypothesis, phenotype: str) -> AgentResult:
                raise RuntimeError("offline")

        assessment = TargetDiscoveryOrchestrator([FailingAgent()]).assess(
            TargetHypothesis("GENE1", "Test hypothesis")
        )

        self.assertEqual(assessment.agent_results[0].status, "error")
        self.assertTrue(any("Agent errors" in finding for finding in assessment.qc_findings))

    def test_uploaded_rows_are_scoped_to_their_agent(self) -> None:
        rows = [{
            "agent": "internal",
            "target": "GENE1",
            "domain": "alignment",
            "evidence_type": "strategy fit",
            "source": "Internal review",
            "title": "Internal note",
            "url": "https://example.org/internal/1",
            "summary": "",
        }]
        target = TargetHypothesis("GENE1", "Test hypothesis")

        internal = InternalAlignmentAgent(rows).collect(target, "obesity")
        model = ModelCoverageAgent(rows).collect(target, "obesity")

        self.assertEqual(len(internal.records), 1)
        self.assertEqual(model.records, [])

    def test_open_targets_association_is_marked_as_integrated_evidence(self) -> None:
        class Sources:
            def open_targets(self, target: str, phenotype: str) -> list[dict[str, str]]:
                return [{
                    "target_name": target,
                    "name": phenotype,
                    "score": "0.7",
                    "target_id": "ENSG00000000001",
                    "id": "EFO_0000001",
                    "url": "https://platform.opentargets.org/target/ENSG00000000001",
                }]

            def reactome(self, target: str) -> list[dict[str, str]]:
                return []

            def string_network(self, target: str) -> list[dict[str, str]]:
                return []

        result = PathwayNetworkAgent(Sources()).collect(
            TargetHypothesis("GENE1", "Test hypothesis"), "obesity"
        )

        self.assertEqual(result.records[0].source, "Open Targets")
        self.assertIn("aggregates", result.records[0].caveats[0])

    def test_package_round_trip_preserves_cohort_and_review_defaults(self) -> None:
        payload = {
            "targets": [{
                "target": {
                    "symbol": "GENE1",
                    "hypothesis": "Test hypothesis",
                    "novelty_class": "emerging",
                },
                "agent_results": [{
                    "agent": "test",
                    "status": "complete",
                    "records": [{
                        "target": "GENE1",
                        "domain": "biology",
                        "evidence_type": "paper",
                        "source": "PubMed",
                        "title": "Study",
                        "url": "https://example.org/study",
                        "summary": "Evidence",
                        "retrieved_at": "2026-10-02T00:00:00+00:00",
                        "evidence_level": None,
                        "identifiers": {"PMID": "1"},
                        "caveats": ["Review needed"],
                    }],
                    "missing_sources": [],
                    "warnings": [],
                }],
                "qc_findings": [],
            }]
        }

        loaded = assessments_from_package(payload)["GENE1"]

        self.assertEqual(loaded.target.novelty_class, "emerging")
        self.assertEqual(loaded.agent_results[0].records[0].caveats, ("Review needed",))
        self.assertEqual(loaded.maturity_classification, "Needs classification")

    def test_evidence_csvs_are_written_under_results(self) -> None:
        assessment = TargetAssessment(
            target=TargetHypothesis("GENE1", "Test hypothesis"),
            agent_results=(
                AgentResult(
                    agent="test agent",
                    status="complete",
                    records=[EvidenceRecord(
                        target="GENE1",
                        domain="biology",
                        evidence_type="paper",
                        source="PubMed",
                        title="Study",
                        url="https://example.org/study",
                        summary="Evidence",
                        identifiers={"PMID": "1"},
                        caveats=("Review needed",),
                    )],
                    missing_sources=["GEO"],
                    warnings=["Manual review required"],
                ),
            ),
        )
        evidence_csv, coverage_csv = evidence_csv_contents(
            {"GENE1": assessment},
            "obesity",
        )
        self.assertIn("GENE1", evidence_csv)
        self.assertIn("test agent", coverage_csv)
        self.assertIn("Manual review required", coverage_csv)

        with tempfile.TemporaryDirectory() as temp_dir:
            result_dir = Path(temp_dir) / "test_csv_export"
            saved = save_evidence_csvs(result_dir, {"GENE1": assessment}, "obesity")

            self.assertEqual(saved[0].name, "evidence_records.csv")
            self.assertEqual(saved[1].name, "agent_coverage.csv")
            self.assertTrue(saved[0].exists())
            self.assertTrue(saved[1].exists())
            self.assertEqual(saved[0].parent, result_dir / "results")
            self.assertIn("GENE1", saved[0].read_text(encoding="utf-8"))
            self.assertIn("test agent", saved[1].read_text(encoding="utf-8"))


class PrioritizationTests(unittest.TestCase):
    def test_priority_evidence_coverage_distinguishes_found_missing_and_unassessed(self) -> None:
        assessment = TargetAssessment(
            target=TargetHypothesis("MC4R", "Test hypothesis"),
            agent_results=(
                AgentResult(
                    agent="Genetic evidence",
                    status="complete",
                    records=[EvidenceRecord(
                        target="MC4R",
                        domain="genetic association",
                        evidence_type="GWAS Catalog association",
                        source="GWAS Catalog",
                        title="MC4R obesity association",
                        url="https://example.org/gwas",
                        summary="Association record",
                    )],
                ),
                AgentResult(agent="Omics assembly", status="no_hits"),
            ),
        )

        coverage = priority_evidence_type_coverage(assessment)

        self.assertIn("GWAS Catalog association (GWAS Catalog)", coverage["Human genetics"])
        self.assertEqual(coverage["Expression / omics"], "No matching records")
        self.assertEqual(coverage["Safety"], "Not assessed")

    def test_target_priority_handles_missing_data_without_zeroing(self) -> None:
        priority = summarize_target_priority(
            "ADCY3",
            {
                "Gene": "ADCY3",
                "Gene name": "adenylate cyclase 3",
                "Genetic association": 0.72,
                "Literature": 0.86,
                "Open Targets score": 0.62,
                "Provisional class": "Emerging",
            },
            {},
            {
                "human_genetic": 0.25,
                "biological_mechanistic": 0.20,
                "disease_expression": 0.15,
                "translational_clinical": 0.15,
                "druggability": 0.15,
                "safety": 0.10,
            },
        )

        self.assertEqual(priority["evidence_strength"], "High")
        self.assertEqual(priority["target_novelty"], "Emerging")
        self.assertGreater(priority["target_priority_score"], 0)
        self.assertIn("Not available", priority["evidence_gaps"][0])

    def test_saturation_chart_labels_each_gene(self) -> None:
        spec = build_saturation_chart_spec([
            {
                "Gene": "MC4R",
                "Clinical trial matches": 2,
                "Genetic association": 0.85,
                "Open Targets score": 0.9,
                "Provisional class": "Known",
            },
        ])

        self.assertEqual(spec["layer"][1]["encoding"]["text"]["field"], "Gene")
        self.assertEqual(spec["layer"][1]["mark"]["type"], "text")

    def test_saturation_rows_include_all_shortlisted_genes(self) -> None:
        rows = build_saturation_rows(
            {
                "MC4R": {"Genetic association": 0.85, "Open Targets score": 0.9, "Provisional class": "Known"},
                "GIPR": {"Genetic association": 0.72, "Open Targets score": 0.8, "Provisional class": "Emerging"},
            },
            ["MC4R", "GIPR"],
            {},
        )

        self.assertEqual([row["Gene"] for row in rows], ["MC4R", "GIPR"])
        self.assertEqual(rows[1]["Clinical trial matches"], 0)

    def test_search_filter_matches_gene_and_hypothesis(self) -> None:
        assessments = {
            "MC4R": TargetAssessment(
                target=TargetHypothesis("MC4R", "Melanocortin signaling in appetite"),
                agent_results=(),
                qc_findings=(),
                review_status="Needs scientific review",
                maturity_classification="Needs classification",
            ),
            "GIPR": TargetAssessment(
                target=TargetHypothesis("GIPR", "Gastric peptide signaling in energy homeostasis"),
                agent_results=(),
                qc_findings=(),
                review_status="Needs scientific review",
                maturity_classification="Needs classification",
            ),
        }

        filtered = filter_assessments_by_query(assessments, "appetite")
        self.assertEqual(list(filtered), ["MC4R"])

    def test_parse_targets_accepts_documented_known_or_emerging_placeholder(self) -> None:
        targets = parse_targets("MC4R | appetite regulation | known or emerging\nGIPR | gut hormone signaling | known")

        self.assertEqual([target.symbol for target in targets], ["MC4R", "GIPR"])
        self.assertEqual(targets[0].novelty_class, "emerging")
        self.assertEqual(targets[1].novelty_class, "known")

    def test_search_gene_rows_match_symbol_and_name(self) -> None:
        rows = [
            {"Gene": "MC4R", "Gene name": "melanocortin 4 receptor"},
            {"Gene": "LEPR", "Gene name": "leptin receptor"},
        ]

        filtered = filter_gene_rows_by_search(rows, "leptin")
        self.assertEqual([row["Gene"] for row in filtered], ["LEPR"])

    def test_genetic_evidence_agent_collects_gwas_and_qtl_links(self) -> None:
        class FakeSources:
            def gwas_catalog(self, target: str, phenotype: str, limit: int = 5) -> list[dict[str, str]]:
                return [{
                    "id": "gwas-1",
                    "title": "MC4R obesity locus study",
                    "summary": "Obesity locus scan.",
                    "url": "https://www.ebi.ac.uk/gwas/study/GCST000001",
                }]

            def qtl_catalog(self, target: str, limit: int = 5) -> list[dict[str, str]]:
                return [{
                    "id": "qtl-1",
                    "title": "MC4R GTEx eQTL",
                    "summary": "Tissue-specific regulatory signal.",
                    "url": "https://gtexportal.org/home/gene/MC4R",
                }]

        result = GeneticEvidenceAgent(FakeSources()).collect(
            TargetHypothesis("MC4R", "Melanocortin obesity hypothesis"),
            "obesity",
        )

        self.assertTrue(any(record.source == "GWAS Catalog" for record in result.records))
        self.assertTrue(any(record.source == "GTEx / QTL" for record in result.records))
        self.assertTrue(any("https://www.ebi.ac.uk/gwas/study/GCST000001" in record.url for record in result.records))

    def test_evidence_matrix_keeps_pubmed_evidence_when_score_is_missing(self) -> None:
        assessment = TargetAssessment(
            target=TargetHypothesis("MC4R", "Melanocortin signaling in appetite"),
            agent_results=(
                AgentResult(
                    agent="Disease profile",
                    status="complete",
                    records=[
                        EvidenceRecord(
                            target="MC4R",
                            domain="disease biology",
                            evidence_type="PubMed indexed paper",
                            source="PubMed",
                            title="MC4R signaling and obesity",
                            url="https://pubmed.ncbi.nlm.nih.gov/12345678/",
                            summary="Mechanistic biology in obesity.",
                            identifiers={"PMID": "12345678"},
                        )
                    ],
                ),
            ),
        )

        records = pubmed_evidence_records(assessment, "MC4R")

        self.assertEqual(evidence_matrix_strength(None), "Not scored")
        citation = evidence_matrix_pubmed_entry(records, ("biology", "mechanism"))
        self.assertIn("PMID: 12345678", citation)
        self.assertIn("MC4R signaling and obesity", citation)
        self.assertNotIn("Not available", citation)
        self.assertEqual(
            records[0]["URL"],
            "https://pubmed.ncbi.nlm.nih.gov/12345678/",
        )

    def test_evidence_matrix_score_is_shown_without_pubmed_evidence(self) -> None:
        assessment = TargetAssessment(
            target=TargetHypothesis("MC4R", "Melanocortin signaling in appetite"),
            agent_results=(
                AgentResult(
                    agent="Pathway and network",
                    status="complete",
                    records=[
                        EvidenceRecord(
                            target="MC4R",
                            domain="genetic association",
                            evidence_type="GWAS Catalog association",
                            source="GWAS Catalog",
                            title="MC4R obesity association",
                            url="https://example.org/gwas",
                            summary="Association result.",
                        )
                    ],
                ),
            ),
        )

        records = pubmed_evidence_records(assessment, "MC4R")

        self.assertEqual(evidence_matrix_strength(82), "High (82.0000)")
        self.assertEqual(
            evidence_matrix_pubmed_entry(records, ("genetic", "association")),
            "No category-matched PubMed record",
        )

    def test_pubmed_matrix_matches_target_and_category_and_uses_canonical_pmid_url(self) -> None:
        assessment = TargetAssessment(
            target=TargetHypothesis("MC4R", "Melanocortin signaling in appetite"),
            agent_results=(
                AgentResult(
                    agent="Disease profile",
                    status="complete",
                    records=[
                        EvidenceRecord(
                            target="MC4R",
                            domain="biology",
                            evidence_type="PubMed indexed paper",
                            source="PubMed",
                            title="MC4R mechanism in obesity",
                            url="https://pubmed.ncbi.nlm.nih.gov/99999999/",
                            summary="Mechanism and signaling.",
                            identifiers={"PMID": "12345678"},
                        ),
                        EvidenceRecord(
                            target="ADCY3",
                            domain="biology",
                            evidence_type="PubMed indexed paper",
                            source="PubMed",
                            title="ADCY3 mechanism in obesity",
                            url="https://pubmed.ncbi.nlm.nih.gov/23456789/",
                            summary="Mechanism and signaling.",
                            identifiers={"PMID": "23456789"},
                        ),
                        EvidenceRecord(
                            target="MC4R",
                            domain="biology",
                            evidence_type="Pathway match",
                            source="Reactome",
                            title="MC4R signaling",
                            url="https://reactome.org/example",
                            summary="Pathway context.",
                        ),
                    ],
                ),
            ),
        )

        records = pubmed_evidence_records(assessment, "MC4R")

        self.assertEqual(len(records), 1)
        self.assertIn(
            "PMID: 12345678",
            evidence_matrix_pubmed_entry(records, ("mechanism",)),
        )
        self.assertEqual(
            evidence_matrix_pubmed_entry(records, ("genetic",)),
            "No category-matched PubMed record",
        )
        self.assertEqual(
            records[0]["URL"],
            "https://pubmed.ncbi.nlm.nih.gov/12345678/",
        )
        self.assertEqual(records[0]["PMID"], "12345678")


class CatalogTests(unittest.TestCase):
    def test_gene_input_deduplicates_symbols_and_limits_batch_size(self) -> None:
        self.assertEqual(
            parse_gene_symbols("inhbe, GPR15\nINHBE; MTARC1"),
            ["INHBE", "GPR15", "MTARC1"],
        )
        with self.assertRaises(ValueError):
            parse_gene_symbols(",".join(f"GENE{index}" for index in range(21)))

    def test_catalog_query_paginates_and_returns_unique_genes(self) -> None:
        class FakeSources(PublicEvidenceSources):
            def _post(self, url: str, payload: dict) -> dict:
                if "DiseaseSearch" in payload["query"]:
                    return {"search": {"hits": [{
                        "id": "MONDO_0011122",
                        "name": "obesity disorder",
                    }]}}
                page_index = payload["variables"]["pageIndex"]
                page_genes = [["GENE1", "GENE2"], ["GENE3"]][page_index]
                return {"disease": {
                    "id": "MONDO_0011122",
                    "name": "obesity disorder",
                    "associatedTargets": {
                        "count": 3,
                        "rows": [{
                            "target": {
                                "id": f"ENSG_{gene}",
                                "approvedSymbol": gene,
                                "approvedName": gene,
                            },
                            "score": 0.5,
                            "datatypeScores": [{"id": "genetic_association", "score": 0.4}],
                        } for gene in page_genes],
                    },
                }}

        catalog = FakeSources().disease_target_catalog(page_size=2)

        self.assertEqual(catalog["total_associated_targets"], 3)
        self.assertEqual([gene["gene"] for gene in catalog["genes"]], ["GENE1", "GENE2", "GENE3"])

    def test_saved_catalog_loads_after_a_dashboard_restart(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            expected = {"disease_id": "MONDO_0011122", "genes": [{"gene": "GENE1"}]}
            saved = save_gene_catalog(Path(directory), expected)

            loaded, path = load_latest_gene_catalog(Path(directory))

            self.assertEqual(loaded, expected)
            self.assertEqual(path, saved)
            self.assertTrue(saved.with_name("gene_catalog.csv").exists())


class EvidenceWorkbookTests(unittest.TestCase):
    def test_workbook_includes_identifiers_links_and_agent_coverage(self) -> None:
        record = EvidenceRecord(
            target="GENE1",
            domain="clinical",
            evidence_type="Clinical trial registry mention",
            source="ClinicalTrials.gov",
            title="Weight management study",
            url="https://clinicaltrials.gov/study/NCT01234567",
            summary="Trial record summary",
            identifiers={"NCT": "NCT01234567", "PMID": "12345678", "GEO": "GSE123"},
            caveats=("Manual review required",),
        )
        assessment = TargetAssessment(
            target=TargetHypothesis("GENE1", "Test hypothesis", "emerging"),
            agent_results=(AgentResult(
                agent="Competitive landscape",
                status="complete",
                records=[record],
                missing_sources=["Patent landscape"],
            ),),
        )

        workbook = load_workbook(BytesIO(evidence_workbook_bytes({"GENE1": assessment})))
        evidence = workbook["Evidence records"]
        coverage = workbook["Agent coverage"]

        self.assertEqual(evidence[1][9].value, "PMID")
        self.assertEqual(evidence[2][9].value, "12345678")
        self.assertEqual(evidence[2][10].value, "NCT01234567")
        self.assertIn("GEO: GSE123", evidence[2][11].value)
        self.assertEqual(evidence[2][12].hyperlink.target, record.url)
        self.assertEqual(coverage[2][3].value, 1)
        self.assertEqual(coverage[2][4].value, "Patent landscape")
        self.assertEqual(workbook["Read me"][2][1].value, "obesity")


if __name__ == "__main__":
    unittest.main()