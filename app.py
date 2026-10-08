from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, DataReturnMode, GridOptionsBuilder, JsCode

from target_discovery.models import ScoreWeights, TargetAssessment, TargetHypothesis
from target_discovery.orchestrator import TargetDiscoveryOrchestrator, score_assessment
from target_discovery.package_io import (
    evidence_csv_contents,
    evidence_workbook_bytes,
    load_latest_gene_catalog,
    load_latest_package,
    save_gene_catalog,
)
from target_discovery.catalog import catalog_rows, parse_gene_symbols
from target_discovery.connectors import PublicEvidenceSources, SourceError
from target_discovery.prioritization import (
    rank_emerging_novel_targets,
    summarize_target_priority,
)


st.set_page_config(
    page_title="Obesity target discovery",
    page_icon="OD",
    layout="wide",
    initial_sidebar_state="expanded",
)

FOUR_DECIMAL_SCORE_FORMATTER = JsCode(
    "params => params.value == null || params.value === '' "
    "? '' : Number(params.value).toFixed(4)"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
:root {
  --ink: #193b32;
  --muted: #62736c;
  --paper: #f6f8f5;
  --white: #ffffff;
  --green: #2c775d;
  --mint: #cce8d2;
  --gold: #e7b847;
  --line: #dce4dc;
  --rose: #b85d4b;
}
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; color: var(--ink); }
.stApp { background: var(--paper); }
.block-container { max-width: 1440px; padding-top: 2rem; padding-bottom: 3rem; }
h1, h2, h3 { font-family: 'Space Grotesk', sans-serif !important; color: var(--ink); letter-spacing: 0; }
h1 { font-size: 2.15rem !important; line-height: 1.12 !important; }
h2 { font-size: 1.35rem !important; }
h3 { font-size: 1.05rem !important; }
[data-testid="stSidebar"] { background: var(--ink); }
[data-testid="stSidebar"] * { color: #f4f7f3; }
[data-testid="stSidebar"] hr { border-color: #48665b; }
[data-testid="stMetric"] { background: var(--white); border: 1px solid var(--line); border-radius: 4px; padding: 0.8rem 1rem; }
[data-testid="stMetricLabel"] { color: var(--muted); }
[data-testid="stMetricValue"] { color: var(--ink); font-family: 'Space Grotesk', sans-serif; }
div.stButton > button[kind="primary"] { background: var(--green); border: 1px solid var(--green); }
div.stButton > button { border-radius: 4px; }
[data-testid="stDataFrame"] { border: 1px solid var(--line); }
.eyebrow { color: var(--green); font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; }
.rule { border-top: 1px solid var(--line); margin: 1rem 0 1.4rem; }
.note { color: var(--muted); font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)


def parse_targets(raw: str) -> list[TargetHypothesis]:
    targets = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|", maxsplit=2)]
        if len(parts) not in {2, 3}:
            raise ValueError(
                f"Line {line_number}: use SYMBOL | hypothesis [| known or emerging]."
            )
        symbol, hypothesis = parts[:2]
        cohort_value = parts[2].casefold() if len(parts) == 3 else "unassigned"
        if cohort_value in {"known or emerging", "emerging or known", "known/emerging", "emerging/known"}:
            starting_cohort = "emerging"
        else:
            starting_cohort = cohort_value
        if starting_cohort not in {"known", "emerging", "novel", "unassigned"}:
            raise ValueError(f"Line {line_number}: cohort must be known, emerging, or unassigned.")
        if starting_cohort == "novel":
            starting_cohort = "emerging"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", symbol):
            raise ValueError(f"Line {line_number}: '{symbol}' is not a valid target identifier.")
        if not hypothesis:
            raise ValueError(f"Line {line_number}: add a testable hypothesis for {symbol}.")
        targets.append(TargetHypothesis(
            symbol=symbol.upper(),
            hypothesis=hypothesis,
            novelty_class=starting_cohort,
        ))
    if not targets:
        raise ValueError("Enter at least one target and a testable hypothesis.")
    return targets


def build_shortlist_target_text(
    selected_symbols: list[str],
    phenotype: str,
    gene_by_symbol: dict[str, dict[str, object]],
) -> str:
    if not selected_symbols:
        return ""
    lines: list[str] = []
    for symbol in selected_symbols:
        if symbol not in gene_by_symbol:
            continue
        provisional = str(gene_by_symbol[symbol].get("Provisional class", "Emerging")).strip()
        cohort = "known" if provisional.casefold() == "known" else "emerging"
        hypothesis = (
            f"Assess whether {symbol} has a causal, directionally supported, and therapeutically "
            f"actionable role in {phenotype.strip() or 'obesity'}."
        )
        lines.append(f"{symbol} | {hypothesis} | {cohort}")
    return "\n".join(lines)


def append_selected_gene_symbols(
    current_input: str,
    selected_symbols: list[str],
    limit: int = 20,
) -> str:
    current_symbols = parse_gene_symbols(current_input, limit=limit) if current_input.strip() else []
    combined_symbols = list(dict.fromkeys([
        *current_symbols,
        *(symbol.strip().upper() for symbol in selected_symbols if symbol.strip()),
    ]))
    if len(combined_symbols) > limit:
        raise ValueError(
            f"Adding these genes would exceed the {limit}-gene shortlist limit."
        )
    return ", ".join(combined_symbols)


def add_selected_catalog_genes() -> None:
    selected_symbols = st.session_state.get("catalog_selected_symbols", [])
    shortlist = selected_symbols
    try:
        st.session_state["gene_input"] = append_selected_gene_symbols(
            st.session_state.get("gene_input", ""),
            selected_symbols,
        )
        st.session_state.pop("catalog_selection_error", None)
    except ValueError as exc:
        st.session_state["catalog_selection_error"] = str(exc)


def append_unique_target_lines(existing_text: str, additional_text: str) -> str:
    existing_lines = existing_text.splitlines()
    existing_symbols = {
        line.split("|", maxsplit=1)[0].strip().upper()
        for line in existing_lines
        if "|" in line
    }
    new_lines = [
        line
        for line in additional_text.splitlines()
        if line.split("|", maxsplit=1)[0].strip().upper() not in existing_symbols
    ]
    return "\n".join([*existing_lines, *new_lines]).strip()


def filter_gene_rows_by_search(rows: list[dict[str, object]], query: str) -> list[dict[str, object]]:
    search_text = query.strip().lower()
    if not search_text:
        return rows
    filtered: list[dict[str, object]] = []
    for row in rows:
        gene = str(row.get("Gene", "")).lower()
        gene_name = str(row.get("Gene name", "")).lower()
        if search_text in gene or search_text in gene_name:
            filtered.append(row)
    return filtered


def category_filter_options(
    rows: list[dict[str, object]],
    max_values: int = 50,
) -> dict[str, list[str]]:
    if not rows:
        return {}
    options: dict[str, list[str]] = {}
    for column in rows[0]:
        values = {
            str(row.get(column) if row.get(column) not in (None, "") else "Not available")
            for row in rows
        }
        if 1 < len(values) <= max_values:
            options[column] = sorted(values, key=str.casefold)
    return options


def filter_rows_by_categories(
    rows: list[dict[str, object]],
    selected_categories: dict[str, list[str]],
) -> list[dict[str, object]]:
    active_selections = {
        column: set(values)
        for column, values in selected_categories.items()
        if values
    }
    if not active_selections:
        return rows
    return [
        row
        for row in rows
        if all(
            str(row.get(column) if row.get(column) not in (None, "") else "Not available")
            in values
            for column, values in active_selections.items()
        )
    ]


def read_evidence_upload(upload: object | None) -> list[dict[str, str]]:
    if upload is None:
        return []
    content = upload.getvalue().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    required = {"agent", "target", "domain", "evidence_type", "source", "title", "url", "summary"}
    missing = required.difference(reader.fieldnames or [])
    if missing:
        raise ValueError("Evidence CSV is missing columns: " + ", ".join(sorted(missing)))
    return [{key: value or "" for key, value in row.items()} for row in reader]


def filter_assessments_by_query(assessments: dict[str, TargetAssessment], query: str) -> dict[str, TargetAssessment]:
    search = query.strip().lower()
    if not search:
        return assessments
    filtered = {}
    for symbol, assessment in assessments.items():
        searchable = " ".join([
            symbol,
            assessment.target.hypothesis,
            assessment.target.novelty_class,
            assessment.maturity_classification,
            assessment.review_status,
        ]).lower()
        if search in searchable:
            filtered[symbol] = assessment
    return filtered


def build_saturation_chart_spec(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "width": "container",
        "height": 360,
        "layer": [
            {
                "mark": {"type": "circle", "size": 180, "opacity": 0.9},
                "encoding": {
                    "x": {"field": "Clinical trial matches", "type": "quantitative", "axis": {"title": "Clinical trial matches"}},
                    "y": {"field": "Genetic association", "type": "quantitative", "axis": {"title": "Genetic association"}},
                    "color": {"field": "Provisional class", "type": "nominal"},
                    "size": {"field": "Open Targets score", "type": "quantitative"},
                    "tooltip": [
                        {"field": "Gene", "type": "nominal"},
                        {"field": "Clinical trial matches", "type": "quantitative"},
                        {"field": "Genetic association", "type": "quantitative"},
                        {"field": "Open Targets score", "type": "quantitative"},
                        {"field": "Provisional class", "type": "nominal"},
                    ],
                },
            },
            {
                "mark": {
                    "type": "text",
                    "align": "left",
                    "baseline": "middle",
                    "dx": 8,
                    "dy": -8,
                    "fontSize": 12,
                    "fontWeight": "bold",
                    "color": "#193b32",
                },
                "encoding": {
                    "x": {"field": "Clinical trial matches", "type": "quantitative"},
                    "y": {"field": "Genetic association", "type": "quantitative"},
                    "text": {"field": "Gene", "type": "nominal"},
                },
            },
        ],
        "config": {"view": {"stroke": None}},
        "data": {"values": rows},
    }


def build_saturation_rows(
    gene_by_symbol: dict[str, dict[str, object]],
    selected_symbols: list[str],
    assessments: dict[str, TargetAssessment],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for symbol in selected_symbols:
        gene_row = gene_by_symbol.get(symbol)
        if gene_row is None:
            continue
        assessment = assessments.get(symbol)
        competitive_agent = next(
            (
                result for result in assessment.agent_results
                if result.agent == "Competitive landscape"
            ),
            None,
        ) if assessment else None
        rows.append({
            "Gene": symbol,
            "Clinical trial matches": len(competitive_agent.records) if competitive_agent else 0,
            "Genetic association": gene_row.get("Genetic association", 0),
            "Open Targets score": gene_row.get("Open Targets score", 0),
            "Provisional class": gene_row.get("Provisional class", "Unassigned"),
        })
    return rows


def obesity_mechanism_domains(symbol: str) -> list[str]:
    gene = symbol.upper()
    domain_lookup = {
        "MC4R": ["Energy Homeostasis & Neuroendocrine Regulation", "Neuroendocrine regulation"],
        "GLP1R": ["Energy Homeostasis & Neuroendocrine Regulation", "Gut Microbiome & Intestinal Biology", "Gut hormone signaling"],
        "GIPR": ["Energy Homeostasis & Neuroendocrine Regulation", "Gut Microbiome & Intestinal Biology", "Gut hormone signaling"],
        "INHBE": ["Energy Homeostasis & Neuroendocrine Regulation", "Adipose Tissue Biology & Adiposity"],
        "LEPR": ["Energy Homeostasis & Neuroendocrine Regulation", "Adipose Tissue Biology & Adiposity"],
        "PPARG": ["Adipose Tissue Biology & Adiposity", "Lipid Metabolism & Lipid Handling"],
        "ADCY3": ["Energy Homeostasis & Neuroendocrine Regulation", "Mitochondrial & Cellular Energy Metabolism"],
        "MTARC1": ["Mitochondrial & Cellular Energy Metabolism", "Lipid Metabolism & Lipid Handling"],
        "GPR15": ["Gut Microbiome & Intestinal Biology", "Immunometabolic Inflammation"],
        "LEP": ["Energy Homeostasis & Neuroendocrine Regulation", "Adipose Tissue Biology & Adiposity"],
    }
    domains = list(domain_lookup.get(gene, []))
    if not domains:
        return ["Requires review"]
    deduped = []
    for domain in domains:
        if domain not in deduped:
            deduped.append(domain)
    return deduped


def evidence_matrix_strength(value: float | int | str | None) -> str:
    if value is None or value == "":
        return "Not scored"
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return "Not scored"
        try:
            value = float(cleaned)
        except ValueError:
            return "Not scored"
    numeric = float(value)
    if numeric == 0:
        return f"0 (measured and absent; {numeric:.4f})"
    if numeric >= 75:
        label = "High"
    elif numeric >= 45:
        label = "Moderate"
    elif numeric >= 20:
        label = "Low"
    elif numeric > 0:
        label = "Very low"
    else:
        return "Not scored"
    return f"{label} ({numeric:.4f})"


def pubmed_evidence_records(
    assessment: TargetAssessment | dict[str, object],
    target: str,
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for row in all_evidence(assessment):
        if (
            str(row.get("target", "")).strip().upper() != target.strip().upper()
            or str(row.get("source", "")).strip().casefold() != "pubmed"
        ):
            continue
        identifiers = row.get("identifiers")
        identifiers = identifiers if isinstance(identifiers, dict) else {}
        pmid_value = str(identifiers.get("PMID") or "").strip()
        raw_pmid = pmid_value if re.fullmatch(r"\d+", pmid_value) else ""
        source_url = str(row.get("url") or "").strip()
        url_match = re.search(
            r"pubmed[.]ncbi[.]nlm[.]nih[.]gov/([0-9]+)",
            source_url,
            re.IGNORECASE,
        )
        url_pmid = url_match.group(1) if url_match else ""
        pmid = raw_pmid or url_pmid
        url = (
            f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            if pmid
            else source_url
        )
        search_text = " ".join(
            str(row.get(field, ""))
            for field in ("domain", "evidence_type", "title", "summary")
        ).casefold()
        records.append({
            "Target": target.strip().upper(),
            "PMID": pmid or "Missing",
            "Title": str(row.get("title") or "Untitled PubMed record"),
            "URL": url,
            "_search_text": search_text,
        })
    return records


def evidence_matrix_pubmed_entry(
    records: list[dict[str, str]],
    keywords: tuple[str, ...],
) -> str:
    matching_records = [
        record
        for record in records
        if any(keyword.casefold() in record["_search_text"] for keyword in keywords)
    ]
    if not matching_records:
        return "No category-matched PubMed record"
    citations = [
        f"PMID: {record['PMID']} — {record['Title']}"
        for record in matching_records
    ]
    return "\n".join(citations)


def assessment_record_count(assessment: TargetAssessment | dict[str, object]) -> int:
    results = getattr(assessment, "agent_results", None)
    if results is None and isinstance(assessment, dict):
        results = assessment.get("agent_results", ())
    return sum(len(getattr(result, "records", [])) for result in results or ())


def all_evidence(assessment: TargetAssessment | dict[str, object]) -> list[dict[str, object]]:
    rows = []
    results = getattr(assessment, "agent_results", None)
    if results is None and isinstance(assessment, dict):
        results = assessment.get("agent_results", ())
    for result in results or ():
        result_records = getattr(result, "records", [])
        result_agent = getattr(result, "agent", "unknown")
        for record in result_records:
            row = asdict(record)
            row["agent"] = result_agent
            rows.append(row)
    return rows


def priority_evidence_type_coverage(
    assessment: TargetAssessment | dict[str, object],
) -> dict[str, str]:
    categories = {
        "Human genetics": (
            ("genetic", "gwas", "variant", "association", "qtl", "eqtl"),
            ("Genetic evidence",),
        ),
        "Biology / mechanism": (
            ("biology", "mechanism", "pathway", "network", "reactome", "string", "pubmed"),
            ("Disease profile", "Pathway and network"),
        ),
        "Expression / omics": (
            ("expression", "transcriptomic", "omics", "geo", "rna"),
            ("Omics assembly",),
        ),
        "Functional evidence": (
            ("functional", "perturbation", "assay", "validation", "cell"),
            (),
        ),
        "Animal evidence": (
            ("animal", "rodent", "mouse", "murine", "in vivo", "preclinical"),
            (),
        ),
        "Human evidence": (
            ("human", "clinical", "patient", "cohort", "trial"),
            ("Disease profile", "Competitive landscape"),
        ),
        "Druggability": (
            ("drug", "druggability", "tractability", "therapeutic", "intervention"),
            ("Competitive landscape",),
        ),
        "Clinical evidence": (
            ("clinical", "trial", "phase", "patient"),
            ("Competitive landscape",),
        ),
        "Safety": (
            ("safety", "toxicity", "adverse", "off-target", "risk"),
            (),
        ),
    }
    evidence_rows = all_evidence(assessment)
    results = getattr(assessment, "agent_results", None)
    if results is None and isinstance(assessment, dict):
        results = assessment.get("agent_results", ())
    result_by_agent = {
        str(result.get("agent", "") if isinstance(result, dict) else getattr(result, "agent", "")): result
        for result in results or ()
    }

    coverage: dict[str, str] = {}
    for category, (keywords, agent_names) in categories.items():
        matching_rows = []
        for row in evidence_rows:
            haystack = " ".join(
                str(row.get(field, ""))
                for field in ("domain", "evidence_type", "source", "title", "summary")
            ).casefold()
            if any(keyword in haystack for keyword in keywords):
                matching_rows.append(row)
        if matching_rows:
            evidence_types = list(dict.fromkeys(
                f"{row.get('evidence_type') or 'Evidence'} ({row.get('source') or 'source not specified'})"
                for row in matching_rows
            ))
            shown = evidence_types[:2]
            remainder = len(evidence_types) - len(shown)
            suffix = f"; +{remainder} more type(s)" if remainder else ""
            coverage[category] = f"{len(matching_rows)} record(s): " + "; ".join(shown) + suffix
            continue

        assessed = False
        for agent_name in agent_names:
            result = result_by_agent.get(agent_name)
            if result is None:
                continue
            status = result.get("status", "") if isinstance(result, dict) else getattr(result, "status", "")
            if status in {"complete", "no_hits", "partial"}:
                assessed = True
                break
        coverage[category] = "No matching records" if assessed else "Not assessed"
    return coverage


def review_count(assessments: dict[str, TargetAssessment | dict[str, object]]) -> int:
    return sum(
        str(getattr(assessment, "review_status", assessment.get("review_status", ""))).startswith("Reviewed")
        if isinstance(assessment, dict)
        else str(getattr(assessment, "review_status", "")).startswith("Reviewed")
        for assessment in assessments.values()
    )


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_obesity_catalog(phenotype: str) -> dict[str, object]:
    return PublicEvidenceSources().disease_target_catalog(
        phenotype=phenotype,
        page_size=500,
    )


if "assessments" not in st.session_state:
    loaded_assessments, loaded_path = load_latest_package(Path(__file__).resolve().parent)
    st.session_state.assessments = loaded_assessments
    st.session_state.loaded_path = str(loaded_path) if loaded_path else ""
st.session_state.setdefault("catalog", None)
if st.session_state.catalog is None:
    loaded_catalog, loaded_catalog_path = load_latest_gene_catalog(Path(__file__).resolve().parent)
    st.session_state.catalog = loaded_catalog
    st.session_state.catalog_path = str(loaded_catalog_path) if loaded_catalog_path else ""
assessments: dict[str, TargetAssessment] = st.session_state.assessments

with st.sidebar:
    st.markdown('<div class="eyebrow">Discovery controls</div>', unsafe_allow_html=True)
    st.markdown("### Evidence weighting")
    biology_weight = st.number_input("Biology (%)", 0, 100, 45, 5)
    novelty_weight = st.number_input("Novelty (%)", 0, 100, 35, 5)
    feasibility_weight = st.number_input("Feasibility (%)", 0, 100, 20, 5)
    weight_total = biology_weight + novelty_weight + feasibility_weight
    if weight_total != 100:
        st.warning(f"Weights total {weight_total}%. Adjust to 100% to score.")
        score_weights = None
    else:
        score_weights = ScoreWeights(
            biology=biology_weight / 100,
            novelty=novelty_weight / 100,
            feasibility=feasibility_weight / 100,
        )
    st.caption("Draft weights only. Component scores require human review.")

    st.markdown("### Target priority scoring")
    priority_weights = {
        "human_genetic": st.slider("Human genetic evidence (%)", 0, 100, 25, 5) / 100,
        "biological_mechanistic": st.slider("Biological/mechanistic evidence (%)", 0, 100, 20, 5) / 100,
        "disease_expression": st.slider("Disease/tissue expression (%)", 0, 100, 15, 5) / 100,
        "translational_clinical": st.slider("Translational/clinical evidence (%)", 0, 100, 15, 5) / 100,
        "druggability": st.slider("Druggability / feasibility (%)", 0, 100, 15, 5) / 100,
        "safety": st.slider("Safety / therapeutic window (%)", 0, 100, 10, 5) / 100,
    }
    priority_total = sum(priority_weights.values())
    if abs(priority_total - 1.0) > 1e-6:
        st.warning(f"Priority weights total {priority_total:.2f}; they should sum to 1.0 for the target priority score.")
    st.caption("Draft analytical targeting score. Missing components remain 'Not available' and are excluded from the weighted calculation.")
    st.divider()
    evidence_upload = st.file_uploader(
        "Internal / prediction / model evidence CSV",
        type=["csv"],
        help="Rows must identify their agent as internal, prediction, or model.",
    )
    st.warning(
        "If this dashboard is publicly hosted, do not upload confidential, "
        "internal, patient-level, or otherwise sensitive evidence."
    )

st.markdown('<div class="eyebrow">Human-in-the-loop target assessment</div>', unsafe_allow_html=True)
st.title("Obesity target discovery")
st.markdown(
    "Collect evidence by domain, inspect provenance and gaps, then record the scientific review."
)
phenotype = st.text_input("Disease or phenotype", value="obesity")
if st.session_state.loaded_path:
    st.caption(f"Loaded package: {Path(st.session_state.loaded_path).parent.name}")

TOP_OBESITY_SHORTLIST = "MC4R, GNAS, PCSK1, LEPR, POMC, GIPR, GLP1R, PPARG, LEP, FTO, SIM1, CNR1, ADCY3, SLC6A3, SLC6A2, BDNF, OPRM1, PNLIP, SLC6A4, HTR2C"

st.markdown('<div class="rule"></div>', unsafe_allow_html=True)
st.subheader("Obesity-associated gene universe")
st.markdown(
    "Discover genes from the exact Open Targets obesity disease entry, then run the full agent committee on a focused shortlist."
)
discover_catalog = st.button(
    "Fetch all Open Targets obesity-associated genes",
    type="primary",
)
if discover_catalog:
    try:
        with st.spinner("Paginating the Open Targets obesity association catalog…"):
            fetched_catalog = fetch_obesity_catalog(phenotype.strip() or "obesity")
            fetched_catalog["retrieved_at_utc"] = datetime.now(UTC).isoformat()
            catalog_path = save_gene_catalog(Path(__file__).resolve().parent, fetched_catalog)
            st.session_state.catalog = fetched_catalog
            st.session_state.catalog_path = str(catalog_path)
    except SourceError as exc:
        st.error(f"Gene catalog retrieval failed: {exc}")

shortlist: list[str] = []
catalog = st.session_state.catalog
catalog_gene_rows: list[dict[str, object]] = []
if catalog:
    catalog_gene_rows = catalog_rows(catalog)
    class_counts = Counter(str(row["Provisional class"]) for row in catalog_gene_rows)
    metric_columns = st.columns(4)
    metric_columns[0].metric("Unique gene symbols", f"{len(catalog_gene_rows):,}")
    metric_columns[1].metric("Provisional known", f"{class_counts['Known']:,}")
    metric_columns[2].metric("Provisional emerging", f"{class_counts['Emerging']:,}")
    metric_columns[3].metric("Novel / low OT evidence", f"{class_counts['Novel']:,}")
    st.caption(
        f"Disease: {catalog['disease_name']} ({catalog['disease_id']}); "
        f"Open Targets reports {catalog['total_associated_targets']:,} associated targets. "
        f"{catalog['total_associated_targets'] - len(catalog_gene_rows):,} association row(s) with missing or duplicate approved symbols are excluded from the gene table. "
        "Classes are source-bounded first-pass bins: Known >=0.65 integrated score; Emerging >=0.20 overall or genetic score; Novel below both. "
        "They are not final maturity decisions."
    )
    if st.session_state.catalog_path:
        catalog_path = Path(st.session_state.catalog_path)
        if catalog_path.parent.name == "data":
            st.caption("Using the bundled Open Targets catalog snapshot. Fetch again above to refresh it.")
        else:
            st.caption(f"Saved catalog: {catalog_path.parent.name}")
    if catalog.get("retrieved_at_utc"):
        st.caption(f"Catalog snapshot retrieved: {catalog['retrieved_at_utc']}")

    all_catalog_rows = catalog_gene_rows
    all_class_names = ["Known", "Emerging", "Novel"]
    filter_columns = st.columns([1, 1.3])
    with filter_columns[0]:
        visible_classes = st.multiselect(
            "Show provisional classes",
            all_class_names,
            default=all_class_names,
        )
    with filter_columns[1]:
        st.session_state.setdefault("gene_input", "")
        gene_input = st.text_input(
            "Genes to plot and fetch (up to 20)",
            key="gene_input",
            help="Leave blank to plot every gene in the current catalog. Enter approved gene symbols separated by commas for a manual shortlist (max 20).",
        )
    search_gene_query = st.text_input(
        "Search genes in the catalog",
        value="",
        placeholder="Type a gene symbol or name",
        help="Search the full catalog table and the plotted shortlist without altering the selected genes.",
    )
    try:
        selected_symbols = parse_gene_symbols(gene_input) if gene_input.strip() else []
        gene_input_error = ""
    except ValueError as exc:
        selected_symbols = []
        gene_input_error = str(exc)
        st.error(gene_input_error)

    gene_by_symbol = {str(row["Gene"]): row for row in all_catalog_rows}
    catalog_table_rows = [
        row for row in all_catalog_rows
        if row["Provisional class"] in visible_classes
    ]
    catalog_table_rows = filter_gene_rows_by_search(
        catalog_table_rows,
        search_gene_query,
    )
    unknown_symbols = [symbol for symbol in selected_symbols if symbol not in gene_by_symbol]
    if unknown_symbols:
        st.warning("Not found in the current Open Targets catalog: " + ", ".join(unknown_symbols))
    if selected_symbols:
        shortlist_rows = [
            gene_by_symbol[symbol]
            for symbol in selected_symbols
            if symbol in gene_by_symbol
            and gene_by_symbol[symbol]["Provisional class"] in visible_classes
        ]
    else:
        shortlist_rows = [
            row for row in all_catalog_rows
            if row["Provisional class"] in visible_classes
        ]
    filtered_catalog_rows = filter_gene_rows_by_search(shortlist_rows, search_gene_query)
    if not selected_symbols and not search_gene_query.strip():
        st.caption(f"Showing all {len(filtered_catalog_rows):,} genes in the current Open Targets catalog.")

    st.markdown("#### Biology evidence landscape")
    st.caption(
        "Selected-gene view: each point is labeled with its gene symbol. Open Targets literature evidence is plotted against genetic-association evidence; point size is the integrated score. "
        "This is not the competitive-saturation plot because the known-drug score was zero across the first 500 targets. "
        "Agent 6 trial-match saturation is shown after shortlist enrichment."
    )
    if filtered_catalog_rows:
        score_fields = {
            "x": {"field": "Literature", "type": "quantitative", "axis": {"title": "Literature evidence"}},
            "y": {"field": "Genetic association", "type": "quantitative", "axis": {"title": "Genetic association"}},
        }
        st.vega_lite_chart(
            data=filtered_catalog_rows,
            spec={
                "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
                "width": "container",
                "height": 420,
                "layer": [
                    {
                        "mark": {"type": "circle", "size": 180, "opacity": 0.85},
                        "encoding": {
                            **score_fields,
                            "color": {"field": "Provisional class", "type": "nominal"},
                            "tooltip": [
                                {"field": "Gene", "type": "nominal"},
                                {"field": "Gene name", "type": "nominal"},
                                {"field": "Provisional class", "type": "nominal"},
                                {"field": "Open Targets score", "type": "quantitative"},
                                {"field": "Genetic association", "type": "quantitative"},
                                {"field": "Literature", "type": "quantitative"},
                            ],
                        },
                    },
                    {
                        "mark": {
                            "type": "text",
                            "align": "left",
                            "baseline": "middle",
                            "dx": 8,
                            "dy": -8,
                            "fontSize": 12,
                            "fontWeight": "bold",
                            "color": "#193b32",
                        },
                        "encoding": {
                            **score_fields,
                            "text": {"field": "Gene", "type": "nominal"},
                        },
                    },
                ],
                "config": {"view": {"stroke": None}},
            },
            width="stretch",
        )
    else:
        st.info("Enter symbols present in the Open Targets catalog to plot and fetch evidence.")
    if catalog_table_rows:
        st.caption(
            f"Select up to 20 genes from the {len(catalog_table_rows):,} visible catalog rows. "
            "Selected symbols are appended to the shortlist field above; manually entered symbols are preserved."
        )
        catalog_grid_builder = GridOptionsBuilder.from_dataframe(
            pd.DataFrame(catalog_table_rows)
        )
        catalog_grid_builder.configure_default_column(
            filter=True,
            floatingFilter=True,
            sortable=True,
            resizable=True,
            minWidth=100,
            width=160,
        )
        catalog_grid_builder.configure_column("Rank", width=75, minWidth=65)
        catalog_grid_builder.configure_column("Gene", width=100, minWidth=85)
        catalog_grid_builder.configure_column("Gene name", width=220, minWidth=150)
        catalog_grid_builder.configure_column(
            "Provisional class",
            width=145,
            minWidth=120,
            filter="agTextColumnFilter",
        )
        catalog_score_columns = {
            "Open Targets score",
            "Genetic association",
            "Genetic literature",
            "Literature",
            "Animal model",
            "Clinical evidence",
            "Known drug",
        }
        for score_column in catalog_score_columns:
            catalog_grid_builder.configure_column(
                score_column,
                valueFormatter=FOUR_DECIMAL_SCORE_FORMATTER,
                filter="agNumberColumnFilter",
            )
        catalog_grid_builder.configure_pagination(
            enabled=True,
            paginationAutoPageSize=False,
            paginationPageSize=50,
        )
        catalog_grid_builder.configure_selection(
            selection_mode="multiple",
            use_checkbox=True,
            header_checkbox=False,
            rowMultiSelectWithClick=False,
        )
        catalog_grid_options = catalog_grid_builder.build()
        catalog_grid_options["paginationPageSizeSelector"] = [25, 50, 100]
        catalog_grid_options["suppressColumnVirtualisation"] = False
        catalog_grid_response = AgGrid(
            pd.DataFrame(catalog_table_rows),
            gridOptions=catalog_grid_options,
            data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
            update_on=["filterChanged", "sortChanged", "selectionChanged"],
            allow_unsafe_jscode=True,
            height=520,
            theme="streamlit",
            key="obesity-gene-catalog-selection-grid",
        )
        selected_catalog_rows = catalog_grid_response.get("selected_rows")
        if isinstance(selected_catalog_rows, pd.DataFrame):
            selected_catalog_records = selected_catalog_rows.to_dict(orient="records")
        elif isinstance(selected_catalog_rows, list):
            selected_catalog_records = selected_catalog_rows
        else:
            selected_catalog_records = []
        selected_catalog_symbols = list(dict.fromkeys(
            str(row.get("Gene", "")).strip().upper()
            for row in selected_catalog_records
            if str(row.get("Gene", "")).strip()
        ))
        st.session_state["catalog_selected_symbols"] = selected_catalog_symbols
        selection_count = len(set(selected_symbols).union(selected_catalog_symbols))
        selection_columns = st.columns([1, 3])
        selection_columns[0].button(
            "Add selected genes to shortlist",
            key="add-catalog-genes-to-shortlist",
            disabled=not selected_catalog_symbols,
            on_click=add_selected_catalog_genes,
        )
        selection_columns[1].caption(
            f"{len(selected_catalog_symbols)} selected; "
            f"{selection_count} of 20 genes would be in the shortlist."
        )
        if "catalog_selection_error" in st.session_state:
            st.error(st.session_state.pop("catalog_selection_error"))
    else:
        st.info("No catalog genes match the current class filters and search.")

    st.subheader("Emerging & novel target ranking")
    st.caption(
        "Ranks emerging and potentially novel candidates in the current shortlist by the weighted score from available evidence categories. "
        "Missing categories are excluded rather than treated as zero; category coverage breaks ties. Open Targets component scores are source-level signals, not independent validation."
    )
    emerging_novel_rows = rank_emerging_novel_targets(
        filtered_catalog_rows,
        st.session_state.assessments,
        priority_weights,
    )
    if emerging_novel_rows:
        for row in emerging_novel_rows:
            assessment = st.session_state.assessments.get(str(row["Gene"]), {})
            row["Evidence records"] = assessment_record_count(assessment)
            row.update(priority_evidence_type_coverage(assessment))
        grid_builder = GridOptionsBuilder.from_dataframe(
            pd.DataFrame(emerging_novel_rows)
        )
        grid_builder.configure_default_column(
            filter=True,
            floatingFilter=True,
            sortable=True,
            resizable=True,
            suppressMovable=False,
            suppressSizeToFit=True,
            minWidth=110,
            width=175,
        )
        column_widths = {
            "Rank": 75,
            "Gene": 100,
            "Gene name": 200,
            "Provisional class": 145,
            "Target Priority Score": 165,
            "Priority category": 205,
            "Available score categories": 175,
            "Evidence categories scored": 260,
            "Human genetics score": 175,
            "Biology / mechanism score": 200,
            "Disease / tissue expression score": 225,
            "Clinical / translational score": 210,
            "Druggability score": 160,
            "Safety score": 125,
            "Major evidence gap": 285,
            "Evidence records": 145,
            "Human genetics": 250,
            "Biology / mechanism": 270,
            "Expression / omics": 250,
            "Functional evidence": 255,
            "Animal evidence": 230,
            "Human evidence": 240,
            "Druggability": 230,
            "Clinical evidence": 230,
            "Safety": 220,
        }
        numeric_columns = {
            "Rank",
            "Target Priority Score",
            "Available score categories",
            "Human genetics score",
            "Biology / mechanism score",
            "Disease / tissue expression score",
            "Clinical / translational score",
            "Druggability score",
            "Safety score",
            "Evidence records",
        }
        score_columns = {
            "Target Priority Score",
            "Human genetics score",
            "Biology / mechanism score",
            "Disease / tissue expression score",
            "Clinical / translational score",
            "Druggability score",
            "Safety score",
        }
        for column_name, column_width in column_widths.items():
            if column_name in emerging_novel_rows[0]:
                column_options: dict[str, object] = {
                    "width": column_width,
                    "minWidth": 80,
                    "filter": (
                        "agNumberColumnFilter"
                        if column_name in numeric_columns
                        else "agTextColumnFilter"
                    ),
                }
                if column_name in score_columns:
                    column_options["valueFormatter"] = FOUR_DECIMAL_SCORE_FORMATTER
                grid_builder.configure_column(column_name, **column_options)
        grid_builder.configure_pagination(
            enabled=True,
            paginationAutoPageSize=False,
            paginationPageSize=50,
        )
        grid_builder.configure_selection(
            selection_mode="multiple",
            use_checkbox=True,
            header_checkbox=True,
            header_checkbox_filtered_only=True,
            rowMultiSelectWithClick=False,
        )
        grid_options = grid_builder.build()
        grid_options["paginationPageSizeSelector"] = [25, 50, 100]
        grid_options["suppressColumnVirtualisation"] = False
        grid_options["suppressDragLeaveHidesColumns"] = True
        category_options = category_filter_options(emerging_novel_rows)
        selected_categories: dict[str, list[str]] = {}
        if category_options:
            with st.expander("Filter by available categories", expanded=True):
                st.caption(
                    "Select one or more values per column. Selections combine across columns; "
                    "columns with more than 50 distinct values remain searchable from their table-header filters."
                )
                category_columns = st.columns(3)
                for index, (column_name, options) in enumerate(category_options.items()):
                    with category_columns[index % len(category_columns)]:
                        selected_categories[column_name] = st.multiselect(
                            column_name,
                            options,
                            key=f"ranking-category-{column_name}",
                            help=f"{len(options)} distinct values are available in this column.",
                        )
        category_filtered_rows = filter_rows_by_categories(
            emerging_novel_rows,
            selected_categories,
        )
        st.caption(
            "Select one or more rows using the checkboxes, then add up to 20 genes to the discovery question below."
        )
        grid_response = AgGrid(
            pd.DataFrame(category_filtered_rows),
            gridOptions=grid_options,
            data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
            update_on=["filterChanged", "sortChanged", "selectionChanged"],
            allow_unsafe_jscode=True,
            height=520,
            theme="streamlit",
            custom_css={
                ".ag-header-cell": {"cursor": "grab"},
                ".ag-header-cell:active": {"cursor": "grabbing"},
            },
            key="emerging-novel-ranking-grid",
        )
        selected_grid_rows = grid_response.get("selected_rows")
        if isinstance(selected_grid_rows, pd.DataFrame):
            selected_grid_records = selected_grid_rows.to_dict(orient="records")
        elif isinstance(selected_grid_rows, list):
            selected_grid_records = selected_grid_rows
        else:
            selected_grid_records = []
        selected_grid_symbols = list(dict.fromkeys(
            str(row.get("Gene", "")).strip().upper()
            for row in selected_grid_records
            if str(row.get("Gene", "")).strip()
        ))
        selection_columns = st.columns([1, 3])
        add_selected_clicked = selection_columns[0].button(
            "Add selected genes to discovery",
            key="add-ranked-genes-to-discovery",
            disabled=not selected_grid_symbols,
        )
        if selected_grid_symbols:
            selection_preview = ", ".join(selected_grid_symbols[:10])
            if len(selected_grid_symbols) > 10:
                selection_preview += f", +{len(selected_grid_symbols) - 10} more"
            selection_columns[1].caption(
                f"{len(selected_grid_symbols)} gene(s) selected: "
                + selection_preview
            )
        if add_selected_clicked:
            if len(selected_grid_symbols) > 20:
                st.error("Select no more than 20 genes at a time.")
            else:
                existing_target_text = st.session_state.get(
                    "candidate_target_text",
                    "",
                )
                updated_target_text = append_unique_target_lines(
                    existing_target_text,
                    build_shortlist_target_text(
                        selected_grid_symbols,
                        phenotype,
                        gene_by_symbol,
                    ),
                )
                if updated_target_text == existing_target_text:
                    st.info("All selected genes are already in the Discovery question.")
                else:
                    st.session_state["candidate_target_text"] = updated_target_text
                    st.success(
                        "Added selected genes to the Discovery question below. "
                        "Review the hypotheses and click “Gather evidence” to run their analysis."
                    )
        returned_rows = grid_response["data"]
        if isinstance(returned_rows, pd.DataFrame):
            visible_ranking_rows = returned_rows.to_dict(orient="records")
        else:
            visible_ranking_rows = list(returned_rows)
        st.caption(
            f"Showing {len(visible_ranking_rows):,} of {len(category_filtered_rows):,} candidates "
            f"after category selection ({len(emerging_novel_rows):,} total ranked)."
        )
        ranking_csv = io.StringIO()
        ranking_writer = csv.DictWriter(
            ranking_csv,
            fieldnames=list(emerging_novel_rows[0]),
        )
        ranking_writer.writeheader()
        ranking_writer.writerows(visible_ranking_rows)
        st.download_button(
            "Export emerging & novel ranking",
            ranking_csv.getvalue(),
            file_name="emerging_novel_target_ranking.csv",
            mime="text/csv",
        )
    else:
        st.info(
            "No emerging or novel targets are in the current "
            ". "
            "Include Emerging or Novel in “Show provisional classes” or select candidates from the catalog."
        )

    st.subheader("Evidence matrix")
    evidence_categories = [
        (
            "Human genetics",
            "human_genetic",
            ("genetic", "gwas", "variant", "association", "human genetics"),
        ),
        (
            "Biology/mechanism",
            "biological_mechanistic",
            ("biology", "mechanism", "pathway", "network", "reactome", "signaling"),
        ),
        (
            "Expression",
            "disease_expression",
            ("expression", "transcriptomics", "rna", "tissue", "omics"),
        ),
        (
            "Functional evidence",
            None,
            ("functional", "cell", "assay", "perturbation", "validation"),
        ),
        (
            "Animal evidence",
            None,
            ("animal", "rodent", "mouse", "in vivo", "preclinical"),
        ),
        (
            "Human evidence",
            None,
            ("human", "clinical", "trial", "patient", "cohort"),
        ),
        (
            "Druggability",
            "druggability",
            ("drug", "druggability", "tractability", "approved", "therapeutic"),
        ),
        (
            "Clinical evidence",
            "translational_clinical",
            ("clinical", "trial", "phase", "study", "patient"),
        ),
        (
            "Safety",
            "safety",
            ("safety", "toxicity", "adverse", "off-target", "risk"),
        ),
    ]
    matrix_rows = []
    pubmed_table_records: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in filtered_catalog_rows:
        gene_symbol = str(row.get("Gene", ""))
        assessment = st.session_state.assessments.get(gene_symbol, {})
        summary = summarize_target_priority(
            gene_symbol,
            row,
            assessment,
            priority_weights,
        )
        matrix_row: dict[str, str] = {"Gene": gene_symbol}
        pubmed_records = pubmed_evidence_records(assessment, gene_symbol)
        for category, score_key, keywords in evidence_categories:
            matrix_row[f"{category} strength"] = evidence_matrix_strength(
                summary["component_scores"].get(score_key)
                if score_key
                else None
            )
            matrix_row[f"{category} PubMed"] = evidence_matrix_pubmed_entry(
                pubmed_records,
                keywords,
            )
        matrix_rows.append(matrix_row)

        for record in pubmed_records:
            matching_categories = [
                category
                for category, _, keywords in evidence_categories
                if evidence_matrix_pubmed_entry([record], keywords)
                != "No category-matched PubMed record"
            ]
            if not matching_categories:
                matching_categories = ["Unclassified"]
            key = (record["Target"], record["PMID"], record["Title"])
            existing = pubmed_table_records.get(key)
            if existing is None:
                pubmed_table_records[key] = {
                    "Target": record["Target"],
                    "PMID": (
                        record["URL"]
                        if record["PMID"] != "Missing"
                        else ""
                    ),
                    "Title": record["Title"],
                    "Evidence categories": ", ".join(matching_categories),
                    "Validation": st.session_state.get(
                        "pubmed_record_validations",
                        {},
                    ).get(
                        f"{record['Target']}|{record['PMID']}|{record['Title']}",
                        "Not reviewed",
                    ),
                }
            else:
                categories = set(str(existing["Evidence categories"]).split(", "))
                categories.update(matching_categories)
                existing["Evidence categories"] = ", ".join(sorted(categories))
    st.dataframe(matrix_rows, hide_index=True, use_container_width=True, height=300)
    st.caption(
        "Strength uses the available source-derived category score (0–100, with four decimals), not a score inferred from article counts. "
        "Functional, animal, and human evidence are not separately scored. PubMed columns show matching target-specific citations only; "
        "a missing score does not remove a citation."
    )
    if pubmed_table_records:
        st.subheader("Cross-check pulled PubMed records")
        pubmed_review_key = "pubmed-record-review-editor"
        reviewed_records = st.data_editor(
            pd.DataFrame(list(pubmed_table_records.values())),
            hide_index=True,
            use_container_width=True,
            key=pubmed_review_key,
            disabled=["Target", "PMID", "Title", "Evidence categories"],
            column_order=[
                "Target",
                "PMID",
                "Title",
                "Evidence categories",
                "Validation",
            ],
            column_config={
                "PMID": st.column_config.LinkColumn(
                    "PMID",
                    help="Click the PMID to open its PubMed record in a new tab.",
                    display_text=(
                        r"https://pubmed\.ncbi\.nlm\.nih\.gov/(\d+)/"
                    ),
                ),
                "Validation": st.column_config.SelectboxColumn(
                    "Validation",
                    help="Review the article and tag it as valid or invalid.",
                    options=["Not reviewed", "Valid", "Invalid"],
                ),
            },
        )
        validation_tags = st.session_state.setdefault(
            "pubmed_record_validations",
            {},
        )
        for record in reviewed_records.to_dict(orient="records"):
            pmid_match = re.search(
                r"pubmed[.]ncbi[.]nlm[.]nih[.]gov/([0-9]+)/",
                str(record.get("PMID", "")),
            )
            if pmid_match:
                record_key = "|".join((
                    str(record.get("Target", "")),
                    pmid_match.group(1),
                    str(record.get("Title", "")),
                ))
                validation_tags[record_key] = str(
                    record.get("Validation", "Not reviewed")
                )

    catalog_csv = io.StringIO()
    catalog_writer = csv.DictWriter(catalog_csv, fieldnames=list(catalog_gene_rows[0]))
    catalog_writer.writeheader()
    catalog_writer.writerows(catalog_gene_rows)
    st.download_button(
        "Export complete gene catalog",
        catalog_csv.getvalue(),
        file_name="obesity_associated_gene_catalog.csv",
        mime="text/csv",
    )

    with st.expander("Agent coverage for the full catalog"):
        st.dataframe([
            {"Agent": "Disease / profile", "Status": "Catalog fetched", "Data fetched": "Exact Open Targets disease entry and target-level association scores; PubMed not run for every gene."},
            {"Agent": "Corporate / internal", "Status": "Not assessed", "Data fetched": "No internal target-alignment source attached."},
            {"Agent": "Omics assembly", "Status": "Shortlist only", "Data fetched": "GEO metadata is queried per enriched target; raw expression is not analyzed."},
            {"Agent": "Pathway / network", "Status": "Partial catalog", "Data fetched": "Open Targets integrated association for all catalog genes; Reactome and STRING run per enriched target."},
            {"Agent": "Prediction / validation", "Status": "Not assessed", "Data fetched": "No prediction/model output supplied."},
            {"Agent": "Competitive landscape", "Status": "Shortlist only", "Data fetched": "Open Targets known-drug score in catalog; ClinicalTrials.gov intervention matching per enriched target."},
            {"Agent": "Model coverage", "Status": "Not assessed", "Data fetched": "No cell-model coverage source attached."},
        ], hide_index=True, use_container_width=True)

    shortlist = selected_symbols
    enrich_clicked = st.button("Fetch evidence from seven agents for these genes")
    if enrich_clicked:
        if not shortlist:
            st.warning("Enter at least one gene symbol to fetch evidence.")
        elif gene_input_error:
            st.error(gene_input_error)
        elif unknown_symbols:
            st.error("Not found in the current catalog: " + ", ".join(unknown_symbols))
        elif len(shortlist) > 20:
            st.error("Enrich at most 20 genes per run.")
        else:
            try:
                uploaded_rows = read_evidence_upload(evidence_upload)
            except (ValueError, UnicodeDecodeError) as exc:
                st.error(str(exc))
            else:
                orchestrator = TargetDiscoveryOrchestrator(uploaded_rows=uploaded_rows)
                progress = st.progress(0, text="Starting target enrichment")
                enriched_assessments = dict(st.session_state.assessments)
                for index, symbol in enumerate(shortlist, start=1):
                    row = gene_by_symbol[symbol]
                    provisional_class = str(row["Provisional class"]).casefold()
                    target = TargetHypothesis(
                        symbol=symbol,
                        hypothesis=(
                            f"Assess whether {symbol} has a causal, directionally supported, "
                            f"and therapeutically actionable role in {phenotype.strip() or 'obesity'}."
                        ),
                        novelty_class=provisional_class,
                    )
                    progress.progress(
                        (index - 1) / len(shortlist),
                        text=f"Running seven agents for {symbol}",
                    )
                    with st.spinner(f"Collecting source evidence for {symbol}…"):
                        enriched_assessments[symbol] = orchestrator.assess(
                            target,
                            phenotype.strip() or "obesity",
                        )
                progress.progress(1.0, text="Shortlist enrichment complete")
                st.session_state.assessments = enriched_assessments
                assessments = enriched_assessments
                st.success(
                    "Evidence collection is complete. CSV exports are available below "
                    "for this session; they are not saved to the server."
                )

    if shortlist:
        saturation_rows = build_saturation_rows(
            gene_by_symbol,
            shortlist,
            st.session_state.assessments,
        )
        if saturation_rows:
            st.markdown("#### Shortlist: biology vs clinical-trial saturation")
            st.caption("Clinical-trial matches are target/intervention text matches, not unique active assets or proof of mechanism.")
            st.vega_lite_chart(
                spec=build_saturation_chart_spec(saturation_rows),
                width="stretch",
            )

left, right = st.columns([1.25, 0.75], gap="large")
with left:
    st.subheader("Discovery question")
    if "candidate_target_text" not in st.session_state:
        st.session_state.candidate_target_text = ""
    if not st.session_state.candidate_target_text.strip() and shortlist:
        st.session_state.candidate_target_text = build_shortlist_target_text(shortlist, phenotype, gene_by_symbol)
    target_text = st.text_area(
        "Candidate target and hypothesis",
        key="candidate_target_text",
        height=160,
        placeholder="One per line: SYMBOL | testable hypothesis [| known or emerging]\nExample: INHBE | INHBE signaling affects adiposity | emerging",
    )
    run_clicked = st.button("Gather evidence", type="primary", use_container_width=True)
with right:
    st.subheader("Agent committee")
    agent_names = [
        "Disease profile",
        "Corporate/internal alignment",
        "Omics assembly",
        "Pathway and network",
        "Prediction and validation",
        "Competitive landscape",
        "Model coverage",
    ]
    st.write("  ·  ".join(agent_names))
    st.markdown(
        '<p class="note">Public sources are queried live. Private, prediction, and model evidence is marked unassessed until supplied.</p>',
        unsafe_allow_html=True,
    )

if run_clicked:
    try:
        if not target_text.strip() and shortlist:
            target_text = build_shortlist_target_text(shortlist, phenotype, gene_by_symbol)
        targets = parse_targets(target_text)
        uploaded_rows = read_evidence_upload(evidence_upload)
    except (ValueError, UnicodeDecodeError) as exc:
        st.error(str(exc))
    else:
        orchestrator = TargetDiscoveryOrchestrator(uploaded_rows=uploaded_rows)
        progress = st.progress(0, text="Starting independent evidence agents")
        new_assessments = {}
        for index, target in enumerate(targets, start=1):
            progress.progress(
                (index - 1) / len(targets),
                text=f"Collecting evidence for {target.symbol}",
            )
            with st.spinner(f"Reviewing public sources for {target.symbol}…"):
                new_assessments[target.symbol] = orchestrator.assess(target, phenotype)
        progress.progress(1.0, text="Evidence collection complete")
        st.session_state.assessments = new_assessments
        st.session_state.loaded_path = ""
        assessments = st.session_state.assessments
        st.success(
            "Evidence collection is complete. CSV exports are available below "
            "for this session; they are not saved to the server."
        )

if assessments:
    search_query = st.text_input(
        "Search targets",
        value="",
        help="Filter by gene symbol, hypothesis text, cohort, maturity, or review status.",
        placeholder="Search by gene or hypothesis",
    )
    filtered_assessments = filter_assessments_by_query(assessments, search_query)
    if not filtered_assessments:
        st.info("No targets match the current search. Try a different gene symbol or hypothesis term.")
    else:
        st.markdown('<div class="rule"></div>', unsafe_allow_html=True)
        total_records = sum(assessment_record_count(item) for item in filtered_assessments.values())
        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("Targets evaluated", len(filtered_assessments))
        col_b.metric("Evidence records", total_records)
        col_c.metric("Human-reviewed", review_count(filtered_assessments))
        col_d.metric(
            "Open QC findings",
            sum(len(item.qc_findings) for item in filtered_assessments.values()),
        )

        summary_rows = []
        for symbol, assessment in filtered_assessments.items():
            records = all_evidence(assessment)
            summary_rows.append({
                "Target": symbol,
                "Hypothesis": assessment.target.hypothesis,
                "Starting cohort": assessment.target.novelty_class,
                "Maturity": assessment.maturity_classification,
                "Evidence": len(records),
                "Domains": len({str(row["domain"]) for row in records}),
                "Overall score": assessment.overall_score if assessment.overall_score is not None else "Not scored",
                "Review status": assessment.review_status,
                "QC findings": len(assessment.qc_findings),
            })

        st.subheader("Target portfolio")
        st.dataframe(summary_rows, hide_index=True, use_container_width=True)

        catalog_map = {str(row["Gene"]): row for row in catalog_rows(st.session_state.catalog) if st.session_state.catalog}
        prioritization_rows = []
        for symbol, assessment in filtered_assessments.items():
            gene_row = catalog_map.get(symbol, {})
            summary = summarize_target_priority(
                symbol,
                gene_row,
                assessment,
                priority_weights,
            )
            prioritization_rows.append({
                "Rank": 0,
                "Gene": symbol,
                "Target Priority Score": summary["target_priority_score"],
                "Human Genetics": summary["component_scores"].get("human_genetic"),
                "Biology": summary["component_scores"].get("biological_mechanistic"),
                "Expression": summary["component_scores"].get("disease_expression"),
                "Clinical/Translational": summary["component_scores"].get("translational_clinical"),
                "Druggability": summary["component_scores"].get("druggability"),
                "Safety": summary["component_scores"].get("safety"),
                "Novelty": summary["target_novelty"],
                "Evidence Strength": summary["evidence_strength"],
                "Major Evidence Gap": summary["evidence_gaps"][0],
                "Priority Category": summary["priority_category"],
            })
        prioritization_rows = sorted(prioritization_rows, key=lambda row: (row["Target Priority Score"] if row["Target Priority Score"] is not None else -1, row["Gene"]), reverse=True)
        for index, row in enumerate(prioritization_rows, start=1):
            row["Rank"] = index
        st.subheader("Final prioritization table")
        st.caption("Provisional analytical ranking only; it requires human review before scientific interpretation or target nomination.")
        st.dataframe(prioritization_rows, hide_index=True, use_container_width=True)

        selected_symbol = st.selectbox("Open target package", list(filtered_assessments))
        selected = filtered_assessments[selected_symbol]
        st.markdown('<div class="rule"></div>', unsafe_allow_html=True)
    st.markdown(f"### {selected.target.symbol}")
    st.write(selected.target.hypothesis)

    gene_row = catalog_map.get(selected.target.symbol, {}) if 'catalog_map' in locals() else {}
    priority_summary = summarize_target_priority(
        selected.target.symbol,
        gene_row,
        selected,
        priority_weights,
    )
    st.subheader("Target deep dive")
    st.caption("Evidence summary only — not an automatic claim of target validation.")
    score_cols = st.columns(4)
    score_cols[0].metric("Overall Target Priority Score", f"{priority_summary['target_priority_score']}/100" if priority_summary['target_priority_score'] is not None else "Not available")
    score_cols[1].metric("Evidence Strength", priority_summary["evidence_strength"])
    score_cols[2].metric("Novelty", priority_summary["target_novelty"])
    score_cols[3].metric("Therapeutic Opportunity", priority_summary["therapeutic_opportunity"])
    mechanism_domains = obesity_mechanism_domains(selected.target.symbol)
    st.markdown("**Obesity biology / mechanism**")
    st.write(" • ".join(mechanism_domains))
    st.markdown("**Evidence chain**")
    evidence_chain = [
        selected.target.symbol,
        "| ".join(mechanism_domains),
        "obesity-relevant process",
        "phenotype",
        "therapeutic hypothesis",
    ]
    st.write(" ↓ ".join(evidence_chain))
    st.markdown("**Evidence gaps**")
    for gap in priority_summary["evidence_gaps"]:
        st.write(f"- {gap}")
    st.markdown("**Therapeutic opportunity**")
    therapy_row = [{
        "Target": selected.target.symbol,
        "Existing drug": "No known drug evidence in the currently loaded source." if gene_row.get("Known drug", 0) in (0, None, "") else "Known drug evidence available",
        "Approved": "Not available",
        "Clinical trial": "Not available",
        "Phase I": "Not available",
        "Phase II": "Not available",
        "Phase III": "Not available",
        "Preclinical": "Not available",
        "Discontinued/failed": "Not available",
        "No known development": "Not available",
    }]
    st.dataframe(therapy_row, hide_index=True, use_container_width=True)

    evidence_tab, coverage_tab, review_tab = st.tabs(["Evidence", "Agent coverage & QC", "Human review"])
    with evidence_tab:
        rows = all_evidence(selected)
        if rows:
            st.dataframe(
                rows,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "url": st.column_config.LinkColumn("Source link", display_text="Open source"),
                    "retrieved_at": st.column_config.DatetimeColumn("Retrieved at"),
                },
            )
        else:
            st.info("No evidence records returned. Check agent coverage for query and source gaps.")

    with coverage_tab:
        coverage_rows = [{
            "Agent": result.agent,
            "Status": result.status,
            "Records": len(result.records),
            "Sources not assessed": ", ".join(result.missing_sources) or "None",
            "Warnings": "; ".join(result.warnings) or "None",
        } for result in selected.agent_results]
        st.table(coverage_rows)
        for result in selected.agent_results:
            record_count = len(result.records)
            with st.expander(
                f"{result.agent} · {result.status} · {record_count} record(s)",
                expanded=False,
            ):
                if result.missing_sources:
                    st.caption("Not assessed: " + ", ".join(result.missing_sources))
                if result.warnings:
                    for warning in result.warnings:
                        st.warning(warning)
                if not result.records and result.status == "no_hits":
                    st.info("Source query completed; no matching records were returned.")
                for record in result.records:
                    st.markdown(f"**[{record.title}]({record.url})**")
                    st.caption(
                        f"{record.source} · {record.domain} · {record.evidence_type} · "
                        f"Retrieved {record.retrieved_at}"
                    )
                    st.write(record.summary)
                    if record.identifiers:
                        st.caption("Identifiers: " + "; ".join(
                            f"{key}={value}" for key, value in record.identifiers.items()
                        ))
                    for caveat in record.caveats:
                        st.caption("Caveat: " + caveat)
        if selected.qc_findings:
            st.markdown("#### Quality-control findings")
            for finding in selected.qc_findings:
                st.warning(finding)
        else:
            st.success("No automated QC findings. Scientific relevance still needs human review.")
        caveats = sorted({
            caveat
            for row in rows
            for caveat in row.get("caveats", [])
        })
        if caveats:
            st.markdown("#### Evidence caveats")
            for caveat in caveats:
                st.write(f"- {caveat}")

    with review_tab:
        st.caption(
            "Known / established: convergent human biology plus therapeutic or clinical validation. "
            "Novel / emerging: supported hypothesis with meaningful cross-domain gaps or limited clinical validation."
        )
        with st.form(f"review-{selected_symbol}"):
            classifications = [
                "Needs classification",
                "Known / established",
                "Novel / emerging",
            ]
            maturity_classification = st.selectbox(
                "Target maturity",
                classifications,
                index=classifications.index(selected.maturity_classification),
            )
            maturity_rationale = st.text_area(
                "Evidence rationale for classification",
                value=selected.metadata.get("maturity_rationale", ""),
                height=90,
            )
            review_status = st.selectbox(
                "Review decision",
                ["Needs scientific review", "In review", "Reviewed - advance", "Reviewed - hold"],
                index=["Needs scientific review", "In review", "Reviewed - advance", "Reviewed - hold"].index(selected.review_status),
            )
            review_note = st.text_area(
                "Reviewer rationale / missing experiment",
                value=selected.metadata.get("review_note", ""),
                height=100,
            )
            score_components = st.checkbox(
                "Record reviewed component scores",
                value=selected.overall_score is not None,
            )
            score_cols = st.columns(3)
            previous_scores = (
                selected.biology_score,
                selected.novelty_score,
                selected.feasibility_score,
            )
            with score_cols[0]:
                biology_score = st.number_input(
                    "Biology / 100", 0.0, 100.0,
                    float(previous_scores[0] or 0), 1.0,
                    disabled=not score_components,
                )
            with score_cols[1]:
                novelty_score = st.number_input(
                    "Novelty / 100", 0.0, 100.0,
                    float(previous_scores[1] or 0), 1.0,
                    disabled=not score_components,
                )
            with score_cols[2]:
                feasibility_score = st.number_input(
                    "Feasibility / 100", 0.0, 100.0,
                    float(previous_scores[2] or 0), 1.0,
                    disabled=not score_components,
                )
            saved = st.form_submit_button("Save human review", type="primary")
        if saved:
            metadata = {
                **selected.metadata,
                "review_note": review_note,
                "maturity_rationale": maturity_rationale,
            }
            updated = replace(
                selected,
                review_status=review_status,
                maturity_classification=maturity_classification,
                metadata=metadata,
            )
            if (
                maturity_classification != "Needs classification"
                and not maturity_rationale.strip()
            ):
                st.error("Add an evidence rationale before assigning a target maturity.")
                updated = None
            if updated is None:
                pass
            elif score_components and score_weights is None:
                st.error("Set weights totaling 100% before saving reviewed scores.")
            elif score_components:
                updated = score_assessment(
                    updated,
                    biology_score,
                    novelty_score,
                    feasibility_score,
                    score_weights,
                )
                st.session_state.assessments[selected_symbol] = updated
                st.success(f"Review saved. Weighted score: {updated.overall_score}/100.")
            else:
                st.session_state.assessments[selected_symbol] = updated
                st.success("Review saved.")

    st.markdown('<div class="rule"></div>', unsafe_allow_html=True)
    export_left, export_middle, export_right = st.columns([1, 1, 1])
    with export_left:
        portfolio_rows = [{
            "target": item.target.symbol,
            "hypothesis": item.target.hypothesis,
            "maturity_classification": item.maturity_classification,
            "maturity_rationale": item.metadata.get("maturity_rationale", ""),
            "biology_score": item.biology_score,
            "novelty_score": item.novelty_score,
            "feasibility_score": item.feasibility_score,
            "overall_score": item.overall_score,
            "review_status": item.review_status,
            "evidence_count": assessment_record_count(item),
            "qc_findings": "; ".join(item.qc_findings),
        } for item in assessments.values()]
        csv_buffer = io.StringIO()
        writer = csv.DictWriter(csv_buffer, fieldnames=list(portfolio_rows[0]))
        writer.writeheader()
        writer.writerows(portfolio_rows)
        st.download_button(
            "Export portfolio CSV",
            csv_buffer.getvalue(),
            file_name="obesity_target_portfolio.csv",
            mime="text/csv",
        )
    with export_middle:
        st.download_button(
            "Download evidence workbook",
            evidence_workbook_bytes(assessments, phenotype),
            file_name="obesity_target_evidence.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            help="One row per evidence record, including identifiers, source links, agent coverage, and caveats.",
        )
    with export_right:
        package = {
            "phenotype": phenotype,
            "score_weights": asdict(score_weights) if score_weights else None,
            "targets": [asdict(item) for item in assessments.values()],
        }
        st.download_button(
            "Export target package JSON",
            json.dumps(package, indent=2),
            file_name="obesity_target_packages.json",
            mime="application/json",
        )

    evidence_csv, coverage_csv = evidence_csv_contents(
        assessments,
        phenotype.strip() or "obesity",
    )
    with st.expander("Download current-session CSV results"):
        st.caption("These files are generated for this session and are not stored on the server.")
        csv_columns = st.columns(2)
        csv_columns[0].download_button(
            "Download evidence records CSV",
            evidence_csv.encode("utf-8-sig"),
            file_name="evidence_records.csv",
            mime="text/csv",
        )
        csv_columns[1].download_button(
            "Download agent coverage CSV",
            coverage_csv.encode("utf-8-sig"),
            file_name="agent_coverage.csv",
            mime="text/csv",
        )