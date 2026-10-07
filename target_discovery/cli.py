from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from target_discovery.models import TargetHypothesis
from target_discovery.orchestrator import TargetDiscoveryOrchestrator


def parse_target_spec(value: str) -> TargetHypothesis:
    parts = [part.strip() for part in value.split("|", maxsplit=2)]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            "Use SYMBOL | testable hypothesis | known or emerging."
        )
    symbol, hypothesis, cohort = parts
    cohort = cohort.casefold()
    if cohort in {"known or emerging", "emerging or known", "known/emerging", "emerging/known"}:
        cohort = "emerging"
    if cohort not in {"known", "emerging", "novel"}:
        raise argparse.ArgumentTypeError("Cohort must be known or emerging.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", symbol):
        raise argparse.ArgumentTypeError(f"Invalid target identifier: {symbol}")
    if not hypothesis:
        raise argparse.ArgumentTypeError(f"A testable hypothesis is required for {symbol}.")
    return TargetHypothesis(
        symbol=symbol.upper(),
        hypothesis=hypothesis,
        novelty_class="emerging" if cohort == "novel" else cohort,
    )


def read_evidence_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"agent", "target", "domain", "evidence_type", "source", "title", "url", "summary"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError("Evidence CSV is missing columns: " + ", ".join(sorted(missing)))
        return [{key: value or "" for key, value in row.items()} for row in reader]


def build_report(phenotype: str, assessments: list[dict[str, object]]) -> str:
    lines = [
        "# Obesity target discovery: integrated evidence report",
        "",
        f"- Phenotype query: {phenotype}",
        f"- Generated (UTC): {datetime.now(UTC).isoformat()}",
        "- Final target maturity: pending human scientific review",
        "- Starting cohort labels are hypotheses, not final known/novel decisions.",
        "",
    ]
    for assessment in assessments:
        target = assessment["target"]
        symbol = target["symbol"]
        results = assessment["agent_results"]
        record_count = sum(len(result["records"]) for result in results)
        lines.extend([
            f"## {symbol}",
            "",
            f"- Starting cohort: {target['novelty_class']}",
            f"- Hypothesis: {target['hypothesis']}",
            f"- Maturity classification: {assessment['maturity_classification']}",
            f"- Evidence records: {record_count}",
            "",
            "| Agent | Status | Records | Sources not assessed |",
            "|---|---|---:|---|",
        ])
        for result in results:
            lines.append(
                f"| {result['agent']} | {result['status']} | {len(result['records'])} "
                f"| {', '.join(result['missing_sources']) or 'None'} |"
            )
        lines.extend(["", "### Evidence records", ""])
        for result in results:
            for record in result["records"]:
                lines.append(
                    f"- [{record['title']}]({record['url']}) ({record['source']}; "
                    f"{record['domain']}): {record['summary']}"
                )
                for caveat in record["caveats"]:
                    lines.append(f"  - Caveat: {caveat}")
        lines.extend(["", "### QC findings", ""])
        if assessment["qc_findings"]:
            lines.extend(f"- {finding}" for finding in assessment["qc_findings"])
        else:
            lines.append("- No automated QC findings; relevance and causal interpretation still require review.")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the obesity target evidence agent committee.")
    parser.add_argument(
        "--target",
        action="append",
        required=True,
        type=parse_target_spec,
        metavar='"SYMBOL | HYPOTHESIS | COHORT"',
    )
    parser.add_argument("--phenotype", default="obesity")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--evidence-csv", type=Path)
    args = parser.parse_args()

    run_id = datetime.now(UTC).strftime("obesity_%Y%m%dT%H%M%SZ")
    output_dir = args.output or Path("runs") / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    orchestrator = TargetDiscoveryOrchestrator(
        uploaded_rows=read_evidence_csv(args.evidence_csv)
    )

    assessments = []
    for index, target in enumerate(args.target, start=1):
        print(f"[{index}/{len(args.target)}] Gathering evidence for {target.symbol}...")
        assessments.append(asdict(orchestrator.assess(target, args.phenotype)))

    package = {
        "phenotype": args.phenotype,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "score_weights": {"biology": 0.45, "novelty": 0.35, "feasibility": 0.20},
        "score_weights_status": "provisional; component scores not assigned",
        "targets": assessments,
    }
    (output_dir / "target_packages.json").write_text(
        json.dumps(package, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "integrated_report.md").write_text(
        build_report(args.phenotype, assessments), encoding="utf-8"
    )

    with (output_dir / "target_summary.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        fields = [
            "Target", "Starting_Cohort", "Hypothesis", "Maturity_Classification",
            "Evidence_Count", "Domains", "Agent_Gaps", "QC_Findings",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for assessment in assessments:
            target = assessment["target"]
            results = assessment["agent_results"]
            records = [record for result in results for record in result["records"]]
            writer.writerow({
                "Target": target["symbol"],
                "Starting_Cohort": target["novelty_class"],
                "Hypothesis": target["hypothesis"],
                "Maturity_Classification": assessment["maturity_classification"],
                "Evidence_Count": len(records),
                "Domains": "; ".join(sorted({record["domain"] for record in records})),
                "Agent_Gaps": "; ".join(sorted({
                    source for result in results for source in result["missing_sources"]
                })),
                "QC_Findings": "; ".join(assessment["qc_findings"]),
            })
    print(f"Evidence packages written to {output_dir.resolve()}")


if __name__ == "__main__":
    main()