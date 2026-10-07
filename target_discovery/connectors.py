from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import requests


class SourceError(RuntimeError):
    """A public evidence source could not return a usable response."""


TARGET_ALIASES = {
    "GLP1R": ("GLP-1 receptor", "glucagon-like peptide-1 receptor"),
    "GIPR": ("GIP receptor", "glucose-dependent insulinotropic polypeptide receptor"),
    "INHBE": ("activin E", "inhibin subunit beta E", "WVE-007"),
    "GPR15": ("G protein-coupled receptor 15",),
    "MTARC1": ("MOSC1", "mARC1"),
}

TRIAL_INTERVENTION_TERMS = {
    "GLP1R": ("semaglutide", "liraglutide", "dulaglutide", "exenatide", "lixisenatide"),
    "GIPR": ("tirzepatide", "retatrutide", "mazdutide", "GIP receptor agonist"),
    "INHBE": ("WVE-007", "activin E"),
}


def _search_terms(target: str) -> tuple[str, ...]:
    terms = (target, *TARGET_ALIASES.get(target.upper(), ()))
    return tuple(dict.fromkeys(terms))


def _matched_trial_terms(study: dict[str, Any], terms: tuple[str, ...]) -> list[str]:
    protocol = study.get("protocolSection", {})
    identification = protocol.get("identificationModule", {})
    description = protocol.get("descriptionModule", {})
    interventions = protocol.get("armsInterventionsModule", {}).get("interventions", [])
    searchable = " ".join([
        identification.get("briefTitle", ""),
        description.get("briefSummary", ""),
        " ".join(
            " ".join((item.get("name", ""), item.get("description", ""), " ".join(item.get("otherNames", []))))
            for item in interventions
        ),
    ]).casefold()
    return [term for term in terms if term.casefold() in searchable]


def _matching_disease_rows(
    rows: list[dict[str, Any]], phenotype: str
) -> list[dict[str, Any]]:
    phrase = phenotype.casefold().strip()
    exact = [
        row for row in rows
        if row.get("disease", {}).get("name", "").casefold().strip() in {
            phrase,
            f"{phrase} disorder",
            f"{phrase} disease",
        }
    ]
    if exact:
        return exact
    return [
        row for row in rows
        if phrase in row.get("disease", {}).get("name", "").casefold()
        and " due to " not in row.get("disease", {}).get("name", "").casefold()
        and " syndrome" not in row.get("disease", {}).get("name", "").casefold()
    ]


class PublicEvidenceSources:
    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "ObesityTargetDiscovery/0.1 (research evidence integration)"
        })

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError(str(exc)) from exc

    def _post(self, url: str, payload: dict[str, Any]) -> Any:
        try:
            response = self.session.post(url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError(str(exc)) from exc
        if body.get("errors"):
            raise SourceError("GraphQL returned errors: " + str(body["errors"]))
        return body.get("data", {})

    def pubmed(self, target: str, phenotype: str, limit: int = 10) -> list[dict[str, str]]:
        target_query = " OR ".join(
            f'"{term}"[Title/Abstract]' for term in _search_terms(target)
        )
        phenotype_terms = tuple(dict.fromkeys((phenotype, "obesity", "adiposity", "body weight")))
        phenotype_query = " OR ".join(
            f'"{term}"[Title/Abstract]' for term in phenotype_terms
        )
        query = f"({target_query}) AND ({phenotype_query})"
        search = self._get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            {
                "db": "pubmed",
                "term": query,
                "retmax": limit,
                "retmode": "json",
                "sort": "relevance",
            },
        )
        ids = search.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        summary = self._get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            {"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
        ).get("result", {})
        abstracts: dict[str, str] = {}
        try:
            response = self.session.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for article in root.findall(".//PubmedArticle"):
                pmid = article.findtext(".//PMID", default="")
                abstract = " ".join(
                    "".join(element.itertext()).strip()
                    for element in article.findall(".//Abstract/AbstractText")
                )
                if pmid and abstract:
                    abstracts[pmid] = abstract
        except (requests.RequestException, ET.ParseError):
            pass
        output = []
        for pmid in ids:
            article = summary.get(pmid, {})
            if article:
                output.append({
                    "id": pmid,
                    "title": article.get("title", "Untitled PubMed record").rstrip("."),
                    "date": article.get("pubdate", ""),
                    "journal": article.get("fulljournalname", ""),
                    "abstract": abstracts.get(pmid, ""),
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                })
        return output

    def clinical_trials(
        self, target: str, phenotype: str, limit: int = 20
    ) -> list[dict[str, str]]:
        terms = tuple(dict.fromkeys((
            *_search_terms(target),
            *TRIAL_INTERVENTION_TERMS.get(target.upper(), ()),
        )))
        query = " OR ".join(f'"{term}"' for term in terms)
        data = self._get(
            "https://clinicaltrials.gov/api/v2/studies",
            {
                "format": "json",
                "query.intr": query,
                "query.cond": phenotype,
                "pageSize": min(limit * 5, 100),
                "countTotal": "true",
            },
        )
        output = []
        for study in data.get("studies", []):
            matched_terms = _matched_trial_terms(study, terms)
            if not matched_terms:
                continue
            protocol = study.get("protocolSection", {})
            identification = protocol.get("identificationModule", {})
            status = protocol.get("statusModule", {})
            design = protocol.get("designModule", {})
            nct_id = identification.get("nctId", "")
            if nct_id:
                output.append({
                    "id": nct_id,
                    "title": identification.get("briefTitle", "Untitled trial"),
                    "status": status.get("overallStatus", "Unknown"),
                    "phase": ", ".join(design.get("phases", [])) or "Not applicable",
                    "matched_terms": ", ".join(matched_terms),
                    "interventions": ", ".join(
                        item.get("name", "")
                        for item in protocol.get("armsInterventionsModule", {}).get("interventions", [])
                        if item.get("name")
                    ),
                    "url": f"https://clinicaltrials.gov/study/{nct_id}",
                })
            if len(output) >= limit:
                break
        return output

    def geo_studies(self, target: str, phenotype: str, limit: int = 10) -> list[dict[str, str]]:
        query = f'"{target}" AND "{phenotype}"'
        search = self._get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            {"db": "gds", "term": query, "retmax": limit, "retmode": "json"},
        )
        ids = search.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        result = self._get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            {"db": "gds", "id": ",".join(ids), "retmode": "json"},
        ).get("result", {})
        output = []
        for geo_id in ids:
            study = result.get(geo_id, {})
            if study:
                accession = study.get("accession", geo_id)
                output.append({
                    "id": accession,
                    "title": study.get("title", "Untitled GEO study"),
                    "summary": study.get("summary", ""),
                    "url": f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={quote(accession)}",
                })
        return output

    def gwas_catalog(self, target: str, phenotype: str = "obesity", limit: int = 5) -> list[dict[str, str]]:
        search_url = f"https://www.ebi.ac.uk/gwas/search?query={quote(f'{target} {phenotype}') }"
        try:
            response = self.session.get(search_url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SourceError(str(exc)) from exc

        hrefs: list[str] = []
        for match in re.finditer(r'href=["\']([^"\']+)["\']', response.text, flags=re.IGNORECASE):
            href = match.group(1)
            if "study" not in href and "trait" not in href and "association" not in href:
                continue
            if href.startswith("http"):
                final_url = href
            elif href.startswith("/"):
                final_url = "https://www.ebi.ac.uk" + href
            else:
                final_url = f"https://www.ebi.ac.uk/gwas/{href.lstrip('./')}"
            if final_url not in hrefs:
                hrefs.append(final_url)

        if not hrefs:
            return [{
                "id": f"gwas-{target.lower()}",
                "title": f"{target} GWAS Catalog search results",
                "summary": f"Manual validation for {target} in {phenotype} via the GWAS Catalog search page.",
                "url": search_url,
            }]

        results = []
        for index, href in enumerate(hrefs[:limit], start=1):
            results.append({
                "id": f"gwas-{index}",
                "title": f"{target} GWAS Catalog record {index}",
                "summary": f"Manual validation needed: GWAS Catalog evidence page for {target} and {phenotype}.",
                "url": href,
            })
        return results

    def qtl_catalog(self, target: str, limit: int = 5) -> list[dict[str, str]]:
        candidate_urls = [
            f"https://gtexportal.org/home/gene/{target}",
            f"https://www.gtexportal.org/home/gene/{target}",
            f"https://www.gtexportal.org/home/eqtls/byGene?geneName={quote(target)}",
        ]
        results: list[dict[str, str]] = []
        seen: set[str] = set()
        for url in candidate_urls:
            if url in seen:
                continue
            seen.add(url)
            results.append({
                "id": f"qtl-{target.lower()}",
                "title": f"{target} GTEx / QTL evidence page",
                "summary": "Expression quantitative trait locus (eQTL) evidence for manual review of tissue-specific regulatory signals.",
                "url": url,
            })
        return results[:limit]

    def open_targets(self, target: str, phenotype: str) -> list[dict[str, str]]:
        search_query = """
        query TargetSearch($queryString: String!) {
          search(queryString: $queryString, entityNames: ["target"]) {
            hits { id name entity }
          }
        }
        """
        matches = self._post(
            "https://api.platform.opentargets.org/api/v4/graphql",
            {"query": search_query, "variables": {"queryString": target}},
        ).get("search", {}).get("hits", [])
        target_match = next(
            (
                hit for hit in matches
                if hit.get("id", "").startswith("ENSG")
                and hit.get("name", "").casefold() == target.casefold()
            ),
            None,
        )
        if not target_match:
            return []

        association_query = """
        query TargetDiseases($ensemblId: String!) {
          target(ensemblId: $ensemblId) {
            associatedDiseases(page: {index: 0, size: 100}) {
              rows { disease { id name } score }
            }
          }
        }
        """
        target_data = self._post(
            "https://api.platform.opentargets.org/api/v4/graphql",
            {
                "query": association_query,
                "variables": {"ensemblId": target_match["id"]},
            },
        ).get("target") or {}
        rows = target_data.get("associatedDiseases", {}).get("rows", [])
        selected = _matching_disease_rows(rows, phenotype)
        return [{
            "id": row["disease"]["id"],
            "name": row["disease"]["name"],
            "score": str(row.get("score", "")),
            "target_id": target_match["id"],
            "target_name": target_match.get("name", target),
            "url": (
                "https://platform.opentargets.org/target/"
                f"{target_match['id']}/associations?disease={row['disease']['id']}"
            ),
        } for row in selected]

    def disease_target_catalog(
        self,
        phenotype: str = "obesity",
        page_size: int = 100,
        max_targets: int | None = None,
    ) -> dict[str, Any]:
        disease_search = """
        query DiseaseSearch($queryString: String!) {
          search(queryString: $queryString, entityNames: ["disease"], page: {index: 0, size: 25}) {
            hits { id name entity }
          }
        }
        """
        hits = self._post(
            "https://api.platform.opentargets.org/api/v4/graphql",
            {"query": disease_search, "variables": {"queryString": phenotype}},
        ).get("search", {}).get("hits", [])
        phrase = phenotype.casefold().strip()
        disease = next((
            hit for hit in hits
            if hit.get("name", "").casefold().strip() in {
                phrase,
                f"{phrase} disorder",
                f"{phrase} disease",
            }
        ), None)
        if not disease:
            raise SourceError(f"Could not resolve an exact disease entry for '{phenotype}'.")

        targets_query = """
        query DiseaseTargets($diseaseId: String!, $pageIndex: Int!, $pageSize: Int!) {
          disease(efoId: $diseaseId) {
            id
            name
            associatedTargets(
              page: {index: $pageIndex, size: $pageSize}
              orderByScore: "score"
            ) {
              count
              rows {
                target { id approvedSymbol approvedName }
                score
                datatypeScores { id score }
              }
            }
          }
        }
        """
        page_size = min(max(page_size, 1), 500)
        first_page = self._post(
            "https://api.platform.opentargets.org/api/v4/graphql",
            {
                "query": targets_query,
                "variables": {
                    "diseaseId": disease["id"],
                    "pageIndex": 0,
                    "pageSize": page_size,
                },
            },
        ).get("disease") or {}
        association = first_page.get("associatedTargets", {})
        total = association.get("count", 0)
        limit = min(total, max_targets) if max_targets else total
        rows = list(association.get("rows", []))[:limit]
        page_count = (limit + page_size - 1) // page_size
        for page_index in range(1, page_count):
            page = self._post(
                "https://api.platform.opentargets.org/api/v4/graphql",
                {
                    "query": targets_query,
                    "variables": {
                        "diseaseId": disease["id"],
                        "pageIndex": page_index,
                        "pageSize": page_size,
                    },
                },
            ).get("disease") or {}
            rows.extend(page.get("associatedTargets", {}).get("rows", []))

        genes = []
        seen = set()
        for row in rows[:limit]:
            target = row.get("target") or {}
            symbol = target.get("approvedSymbol", "").strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            genes.append({
                "target_id": target.get("id", ""),
                "gene": symbol,
                "gene_name": target.get("approvedName", ""),
                "overall_score": float(row.get("score", 0) or 0),
                "datatype_scores": {
                    item["id"]: float(item.get("score", 0) or 0)
                    for item in row.get("datatypeScores", [])
                },
            })
        return {
            "disease_id": disease["id"],
            "disease_name": disease["name"],
            "total_associated_targets": total,
            "returned_targets": len(genes),
            "page_size": page_size,
            "genes": genes,
        }

    def reactome(self, target: str, limit: int = 10) -> list[dict[str, str]]:
        data = self._get(
            "https://reactome.org/ContentService/search/query",
            {"query": target, "types": "Pathway", "pageSize": limit},
        )
        return [{
            "id": row.get("stId", row.get("dbId", "")),
            "title": row.get("name", "Reactome pathway match"),
            "url": "https://reactome.org/content/detail/" + str(row.get("stId", "")),
        } for row in data.get("results", []) if row.get("stId")]

    def string_network(self, target: str, limit: int = 10) -> list[dict[str, str]]:
        data = self._get(
            "https://string-db.org/api/json/network",
            {
                "identifiers": target,
                "species": 9606,
                "limit": limit,
                "caller_identity": "obesity_target_discovery",
            },
        )
        best_scores: dict[str, float] = {}
        for row in data:
            partner = row.get("preferredName_B", "")
            if not partner or partner.casefold() == target.casefold():
                continue
            best_scores[partner] = max(best_scores.get(partner, 0.0), float(row.get("score", 0)))
        return [{
            "partner": row.get("preferredName_B", ""),
            "score": str(row.get("score", "")),
            "url": "https://string-db.org/cgi/network?identifiers=" + quote(target),
        } for row in (
            {"preferredName_B": partner, "score": score}
            for partner, score in sorted(best_scores.items(), key=lambda item: item[1], reverse=True)[:limit]
        )]