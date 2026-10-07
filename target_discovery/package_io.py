from __future__ import annotations

import json
import csv
from io import BytesIO, StringIO
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from target_discovery.models import (
    AgentResult,
    EvidenceRecord,
    TargetAssessment,
    TargetHypothesis,
)
from target_discovery.catalog import catalog_rows


def _excel_safe_value(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t")):
        return "'" + value
    return value


def _format_worksheet(worksheet: Any) -> None:
    header_fill = PatternFill("solid", fgColor="193B32")
    for cell in worksheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    for column in worksheet.columns:
        values = [len(str(cell.value or "")) for cell in column[:200]]
        width = min(max(max(values, default=10) + 2, 12), 60)
        worksheet.column_dimensions[column[0].column_letter].width = width
    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def evidence_workbook_bytes(
    assessments: Mapping[str, TargetAssessment], phenotype: str = "obesity",
) -> bytes:
    workbook = Workbook()
    evidence_sheet = workbook.active
    evidence_sheet.title = "Evidence records"
    evidence_headers = [
        "Gene", "Starting cohort", "Hypothesis", "Agent", "Agent status",
        "Evidence type", "Domain", "Source", "Title", "PMID",
        "ClinicalTrials.gov ID", "Other identifiers", "Source link", "Summary",
        "Evidence level", "Retrieved at (UTC)", "Caveats",
    ]
    evidence_sheet.append(evidence_headers)

    coverage_sheet = workbook.create_sheet("Agent coverage")
    coverage_sheet.append([
        "Gene", "Agent", "Status", "Evidence records", "Sources not assessed", "Warnings",
    ])

    for symbol, assessment in assessments.items():
        for result in assessment.agent_results:
            coverage_sheet.append([
                symbol,
                result.agent,
                result.status,
                len(result.records),
                "; ".join(result.missing_sources),
                "; ".join(result.warnings),
            ])
            for record in result.records:
                identifiers = record.identifiers or {}
                identifier_values = "; ".join(
                    f"{key}: {value}" for key, value in identifiers.items()
                    if key.casefold() not in {"pmid", "nct"}
                )
                evidence_sheet.append([
                    symbol,
                    assessment.target.novelty_class,
                    assessment.target.hypothesis,
                    result.agent,
                    result.status,
                    record.evidence_type,
                    record.domain,
                    record.source,
                    record.title,
                    identifiers.get("PMID", identifiers.get("pmid", "")),
                    identifiers.get("NCT", identifiers.get("nct", "")),
                    identifier_values,
                    record.url,
                    record.summary,
                    record.evidence_level,
                    record.retrieved_at,
                    "; ".join(record.caveats),
                ])

    for row in evidence_sheet.iter_rows(min_row=2):
        for cell in row:
            cell.value = _excel_safe_value(cell.value)
        link_cell = row[12]
        if isinstance(link_cell.value, str) and link_cell.value.startswith(("http://", "https://")):
            link_cell.hyperlink = link_cell.value
            link_cell.style = "Hyperlink"
    for row in coverage_sheet.iter_rows(min_row=2):
        for cell in row:
            cell.value = _excel_safe_value(cell.value)

    readme = workbook.create_sheet("Read me")
    readme.append(["Field", "Description"])
    readme_rows = [
        ("Phenotype", phenotype),
        ("Generated at (UTC)", datetime.now(UTC).isoformat()),
        ("Evidence records", "One row per returned record. Gene, source, evidence type, identifiers, and source URL are preserved for review."),
        ("Identifiers", "PMID and ClinicalTrials.gov NCT IDs have dedicated columns. Other record identifiers are retained in Other identifiers."),
        ("Coverage", "Agent coverage reports sources queried, record counts, missing sources, and warnings for each gene."),
        ("Interpretation", "Search results require manual validation; a mention or portal link does not establish causality or confirm an association."),
        ("Unavailable evidence", "No-hit and unassessed sources are not represented as negative biological findings."),
    ]
    for row in readme_rows:
        readme.append([_excel_safe_value(value) for value in row])

    for worksheet in workbook.worksheets:
        _format_worksheet(worksheet)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def assessments_from_package(payload: dict[str, Any]) -> dict[str, TargetAssessment]:
    assessments = {}
    for raw in payload.get("targets", []):
        target_data = raw["target"]
        target = TargetHypothesis(**target_data)
        agent_results = []
        for result in raw.get("agent_results", []):
            records = [
                EvidenceRecord(
                    **{
                        **record,
                        "caveats": tuple(record.get("caveats", ())),
                    }
                )
                for record in result.get("records", [])
            ]
            agent_results.append(AgentResult(
                agent=result["agent"],
                status=result["status"],
                records=records,
                missing_sources=result.get("missing_sources", []),
                warnings=result.get("warnings", []),
            ))
        assessment = TargetAssessment(
            target=target,
            agent_results=tuple(agent_results),
            biology_score=raw.get("biology_score"),
            novelty_score=raw.get("novelty_score"),
            feasibility_score=raw.get("feasibility_score"),
            overall_score=raw.get("overall_score"),
            maturity_classification=raw.get(
                "maturity_classification", "Needs classification"
            ),
            qc_findings=tuple(raw.get("qc_findings", ())),
            review_status=raw.get("review_status", "Needs scientific review"),
            metadata=raw.get("metadata", {}),
        )
        assessments[target.symbol] = assessment
    return assessments


def evidence_csv_contents(
    assessments: Mapping[str, TargetAssessment],
    phenotype: str = "obesity",
) -> tuple[str, str]:
    evidence_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    for symbol, assessment in assessments.items():
        for result in assessment.agent_results:
            coverage_rows.append({
                "Phenotype": phenotype,
                "Target": symbol,
                "Hypothesis": assessment.target.hypothesis,
                "Starting cohort": assessment.target.novelty_class,
                "Agent": result.agent,
                "Agent status": result.status,
                "Evidence records": len(result.records),
                "Sources not assessed": "; ".join(result.missing_sources),
                "Warnings": "; ".join(result.warnings),
            })
            for record in result.records:
                identifiers = record.identifiers or {}
                evidence_rows.append({
                    "Phenotype": phenotype,
                    "Target": symbol,
                    "Hypothesis": assessment.target.hypothesis,
                    "Starting cohort": assessment.target.novelty_class,
                    "Agent": result.agent,
                    "Agent status": result.status,
                    "Evidence type": record.evidence_type,
                    "Domain": record.domain,
                    "Source": record.source,
                    "Title": record.title,
                    "PMID": identifiers.get("PMID", identifiers.get("pmid", "")),
                    "ClinicalTrials.gov ID": identifiers.get("NCT", identifiers.get("nct", "")),
                    "Other identifiers": "; ".join(
                        f"{key}: {value}"
                        for key, value in identifiers.items()
                        if key.casefold() not in {"pmid", "nct"}
                    ),
                    "Source link": record.url,
                    "Summary": record.summary,
                    "Evidence level": record.evidence_level,
                    "Retrieved at (UTC)": record.retrieved_at,
                    "Caveats": "; ".join(record.caveats),
                })

    evidence_fieldnames = list(evidence_rows[0].keys()) if evidence_rows else [
        "Phenotype", "Target", "Hypothesis", "Starting cohort", "Agent",
        "Agent status", "Evidence type", "Domain", "Source", "Title",
        "PMID", "ClinicalTrials.gov ID", "Other identifiers", "Source link",
        "Summary", "Evidence level", "Retrieved at (UTC)", "Caveats",
    ]
    coverage_fieldnames = list(coverage_rows[0].keys()) if coverage_rows else [
        "Phenotype", "Target", "Hypothesis", "Starting cohort", "Agent",
        "Agent status", "Evidence records", "Sources not assessed", "Warnings",
    ]
    evidence_stream = StringIO(newline="")
    writer = csv.DictWriter(evidence_stream, fieldnames=evidence_fieldnames)
    writer.writeheader()
    writer.writerows(evidence_rows)
    coverage_stream = StringIO(newline="")
    writer = csv.DictWriter(coverage_stream, fieldnames=coverage_fieldnames)
    writer.writeheader()
    writer.writerows(coverage_rows)
    return evidence_stream.getvalue(), coverage_stream.getvalue()


def save_evidence_csvs(
    base_dir: Path,
    assessments: Mapping[str, TargetAssessment],
    phenotype: str = "obesity",
) -> tuple[Path, Path]:
    output_dir = base_dir if base_dir.name == "results" else base_dir / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_csv, coverage_csv = evidence_csv_contents(assessments, phenotype)
    evidence_path = output_dir / "evidence_records.csv"
    coverage_path = output_dir / "agent_coverage.csv"
    with evidence_path.open("w", encoding="utf-8-sig", newline="") as stream:
        stream.write(evidence_csv)
    with coverage_path.open("w", encoding="utf-8-sig", newline="") as stream:
        stream.write(coverage_csv)
    return evidence_path, coverage_path


def load_latest_package(base_dir: Path) -> tuple[dict[str, TargetAssessment], Path | None]:
    package_paths = list((base_dir / "runs").glob("*/target_packages.json"))
    if not package_paths:
        return {}, None
    latest_path = max(package_paths, key=lambda path: path.stat().st_mtime)
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    return assessments_from_package(payload), latest_path


def load_latest_gene_catalog(base_dir: Path) -> tuple[dict[str, Any] | None, Path | None]:
    catalog_paths = list((base_dir / "runs").glob("*/gene_catalog.json"))
    if not catalog_paths:
        return None, None
    latest_path = max(catalog_paths, key=lambda path: path.stat().st_mtime)
    return json.loads(latest_path.read_text(encoding="utf-8")), latest_path


def save_gene_catalog(base_dir: Path, catalog: dict[str, Any]) -> Path:
    run_id = datetime.now(UTC).strftime("obesity_gene_catalog_%Y%m%dT%H%M%SZ")
    output_dir = base_dir / "runs" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "gene_catalog.json"
    output_path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    rows = catalog_rows(catalog)
    csv_path = output_dir / "gene_catalog.csv"
    fieldnames = [
        "Rank",
        "Gene",
        "Gene name",
        "Open Targets score",
        "Genetic association",
        "Genetic literature",
        "Literature",
        "Animal model",
        "Clinical evidence",
        "Known drug",
        "Provisional class",
        "Classification basis",
        "Target_Type",
        "Biological_Domain",
        "Pathway",
        "Mechanism_of_Action",
        "Modulation_Direction",
        "Human_Genetic_Evidence",
        "Human_Clinical_Evidence",
        "Drug_Evidence",
        "Clinical_Stage",
        "Drug_Target",
        "Tissue_Expression",
        "Obesity_Phenotype",
        "Clinical_Outcome",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return output_path