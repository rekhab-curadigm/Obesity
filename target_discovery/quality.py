from collections import Counter

from target_discovery.models import AgentResult, EvidenceRecord


def evidence_quality_findings(
    target: str,
    records: list[EvidenceRecord],
    agent_results: list[AgentResult],
) -> list[str]:
    findings: list[str] = []
    incomplete = [
        record.title or f"Untitled {record.evidence_type} record"
        for record in records
        if not record.source.strip() or not record.url.strip()
    ]
    if incomplete:
        findings.append(
            f"{len(incomplete)} evidence record(s) lack source or URL provenance."
        )

    duplicates = Counter(
        (record.source, record.url) for record in records if record.url
    )
    duplicate_count = sum(count - 1 for count in duplicates.values() if count > 1)
    if duplicate_count:
        findings.append(f"{duplicate_count} duplicate source record(s) detected.")

    mismatched = [
        record.title or record.url
        for record in records
        if record.target.casefold() != target.casefold()
    ]
    if mismatched:
        findings.append(
            f"{len(mismatched)} record(s) are not explicitly linked to {target}."
        )

    unavailable = sorted({
        source
        for result in agent_results
        for source in result.missing_sources
    })
    if unavailable:
        findings.append("Sources not assessed: " + ", ".join(unavailable) + ".")

    failed = [result.agent for result in agent_results if result.status == "error"]
    if failed:
        findings.append("Agent errors: " + ", ".join(sorted(failed)) + ".")

    return findings