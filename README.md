# Obesity Target Discovery

A clean, human-in-the-loop workspace for collecting and reviewing target evidence. It starts with a target hypothesis, gathers independently attributed evidence, surfaces unavailable sources and QC findings, and exports a reviewable target package.

## Agent workflow

The Orchestrator runs seven independent roles: disease profile (PubMed), corporate/internal alignment (uploaded evidence), omics assembly (NCBI GEO metadata), pathway and network (Open Targets, Reactome, STRING), prediction and validation (uploaded evidence), competitive landscape (ClinicalTrials.gov), and model coverage (uploaded evidence). Each record retains source, URL, retrieval time, identifiers, and caveats. Agent errors and unavailable inputs remain visible in QC.

The first-pass distinction is indication-specific: a target can be established in a neighboring disease and still be novel for obesity. Starting cohorts organize the search; final maturity classification is a separate human-reviewed decision.

The gene-universe view paginates the exact Open Targets `obesity disorder` association catalog (MONDO_0011122). Its Known/Emerging/Novel bins are provisional score thresholds for triage only; trial saturation, target-specific development, internal alignment, prediction, and model coverage require additional agent evidence. Each fetch saves `gene_catalog.json` and a classed `gene_catalog.csv` under `runs/`; the full catalog can be enriched in batches of up to 20 selected symbols.

The Emerging & novel target ranking sorts the current shortlist by the weighted priority score from available evidence categories. Header filters are available for all columns. Distinct-value multi-selects are also shown for columns with up to 50 current values; selections combine, while high-cardinality columns remain searchable in the header. Columns can be resized and reordered. Missing categories are excluded rather than scored as zero, and broader score-category coverage breaks ties. Open Targets literature, genetic association, genetic-literature, clinical, animal-model, and known-drug scores are shown as source-level signals; they are not independent validation or proof of causality.

Use the ranking table's row checkboxes to select up to 20 genes, then choose "Add selected genes to discovery." The generated hypotheses are added to the Discovery question without duplicating genes already listed; review them and choose "Gather evidence" to run the analysis.

The full obesity gene catalog also has row checkboxes. Select genes there and choose "Add selected genes to shortlist" to append them to "Genes to plot and fetch"; any symbols entered manually are retained, duplicates are removed, and the combined shortlist remains capped at 20 genes.

The Evidence matrix separates each category's source-derived strength score from target-specific PubMed citations. Strength values use a 0–100 scale and display four decimals; categories without a score are labeled "Not scored" rather than "Not available." PubMed-only citations remain visible independently of scoring. In the PubMed review table, each PMID is clickable and opens its article in a new tab; use the Validation dropdown to tag the record as Not reviewed, Valid, or Invalid. Those tags are retained for the current Streamlit session. Non-PubMed sources are not shown as PubMed evidence.

## Run locally

From this directory:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
streamlit run app.py
```

The app queries PubMed, ClinicalTrials.gov, NCBI GEO, Open Targets, Reactome, and STRING. Public evidence is fetched on demand. Internal alignment, prediction-validation, and model-coverage evidence can be supplied as CSV.

## Deploy a public dashboard

The project is prepared for Streamlit Community Cloud. The deployed app can be publicly accessible even if its connected GitHub repository is private.

1. Create a GitHub repository and add this project, including `app.py`, `target_discovery/`, `pyproject.toml`, `requirements.txt`, and `.streamlit/config.toml`.
2. Review the files before pushing. `.gitignore` excludes `.venv/`, `runs/`, `results/`, environment files, and Streamlit secrets. Do not force-add generated packages, saved CSVs, uploaded evidence, credentials, or other sensitive data.
3. In Streamlit Community Cloud, select **Create app**, choose the repository and branch, and set the app file to `app.py`.
4. Deploy, then use the app's **Share** control to copy its public `*.streamlit.app` URL.

The first deployment starts without a bundled local gene catalog or saved assessment package. Visitors can fetch the public Open Targets catalog and public evidence from within the app. Assessments and CSV exports are session-specific; CSV downloads are generated in memory rather than written to a shared server directory.

Before sharing, test the public URL in a private/incognito browser window. Public access means anyone with the URL can use the dashboard. Do not upload confidential, internal, patient-level, or otherwise sensitive evidence.

For a local server endpoint that is reachable by collaborators on the same network:

```powershell
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

> This dashboard is for reviewable evidence and should not be exposed with private, internal-only evidence unless the host is secured and access is controlled.

## Reproduce a CLI run

Run the seven-agent committee against the initial comparator/emerging set:

```powershell
python -m target_discovery.cli --output runs/obesity_run \
	--target "GLP1R | GLP1R activation reduces body weight in obesity | known" \
	--target "GIPR | GIPR contributes to incretin-based weight reduction | known" \
	--target "INHBE | INHBE signaling affects adiposity and metabolic outcomes | emerging" \
	--target "GPR15 | GPR15 immune signaling contributes to obesity biology | emerging" \
	--target "MTARC1 | MTARC1 alters obesity-associated liver lipid biology | emerging"
```

Each run writes `target_packages.json`, `target_summary.csv`, and `integrated_report.md`. The dashboard loads the most recently generated package automatically.

## Candidate input

Enter one target per line using `SYMBOL | testable hypothesis` or `SYMBOL | testable hypothesis | known/emerging`. A starting cohort is only an organizing label; the final maturity classification still requires human review. A hypothesis should state the proposed target-to-phenotype mechanism, not just a target name.

Uploaded evidence CSVs require these columns:

`agent,target,domain,evidence_type,source,title,url,summary`

Use `internal`, `prediction`, or `model` in `agent`. Optional columns include `evidence_level` and identifiers named `id_*`. Source references should be stable and reviewable.

## Interpretation

- Search hits and network relationships are leads, not proof of causality.
- GEO metadata matches are not differential-expression results.
- Open Targets scores are integrated evidence and should not be treated as an independent replication.
- Clinical-trial text matches do not prove that a target is the intervention mechanism.
- Draft score weights (45% biology, 35% novelty, 20% feasibility) are configurable and provisional. Reviewers enter the component scores; the app never infers them from publication or trial counts.
- Final target decisions require scientific review.

Assessment state is held in the running Streamlit session. The app does not include authentication or persistent storage. Public hosting makes the app and its session processing available to the hosting provider; do not upload confidential, internal, patient-level, or otherwise sensitive evidence.

## Tests

```powershell
python -m unittest discover -s tests
```