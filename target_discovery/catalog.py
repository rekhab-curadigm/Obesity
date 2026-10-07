from __future__ import annotations

from typing import Any


KNOWN_EVIDENCE_THRESHOLD = 0.65
EMERGING_EVIDENCE_THRESHOLD = 0.20


def obesity_target_profile(gene_symbol: str, gene_name: str = "") -> dict[str, str]:
    symbol = str(gene_symbol or "").upper()
    label = (gene_name or symbol).strip()

    default_profile = {
        "Target_Type": "Unknown",
        "Biological_Domain": "Genetic, Epigenetic & Developmental Programming",
        "Pathway": "Not yet assigned",
        "Mechanism_of_Action": "Potential modulation of adiposity, appetite, or metabolic signaling remains to be established.",
        "Modulation_Direction": "Context dependent",
        "Human_Genetic_Evidence": "Weak",
        "Human_Clinical_Evidence": "Weak",
        "Drug_Evidence": "No curated drug evidence in current catalog",
        "Clinical_Stage": "None",
        "Drug_Target": "No",
        "Tissue_Expression": "brain, adipose, liver, gut, muscle",
        "Obesity_Phenotype": "BMI, appetite, adiposity, insulin resistance",
        "Clinical_Outcome": "weight reduction, glucose improvement, lipid improvement",
    }

    target_profiles: dict[str, dict[str, str]] = {
        "MC4R": {
            "Target_Type": "Receptor",
            "Biological_Domain": "Energy Homeostasis & Neuroendocrine Regulation",
            "Pathway": "Melanocortin appetite and energy-balance signaling",
            "Mechanism_of_Action": "Increased melanocortin signaling suppresses appetite and lowers adiposity; receptor disruption drives hyperphagia and severe obesity.",
            "Modulation_Direction": "Agonist / activator",
            "Human_Genetic_Evidence": "Strong",
            "Human_Clinical_Evidence": "Moderate",
            "Drug_Evidence": "Melanocortin pathway modulators and appetite-control compounds under active development",
            "Clinical_Stage": "Preclinical / clinical investigation",
            "Drug_Target": "Yes",
            "Tissue_Expression": "brain, hypothalamus, adipose",
            "Obesity_Phenotype": "BMI, appetite, adiposity, hyperphagia",
            "Clinical_Outcome": "weight reduction, appetite suppression",
        },
        "LEPR": {
            "Target_Type": "Receptor",
            "Biological_Domain": "Energy Homeostasis & Neuroendocrine Regulation",
            "Pathway": "Leptin signaling and satiety control",
            "Mechanism_of_Action": "Restores leptin signal transduction to reduce appetite and improve energy expenditure.",
            "Modulation_Direction": "Agonist / activator",
            "Human_Genetic_Evidence": "Strong",
            "Human_Clinical_Evidence": "Moderate",
            "Drug_Evidence": "Leptin replacement and leptin-pathway modifiers",
            "Clinical_Stage": "Approved / development",
            "Drug_Target": "Yes",
            "Tissue_Expression": "brain, adipose, liver",
            "Obesity_Phenotype": "BMI, appetite, adiposity, insulin resistance",
            "Clinical_Outcome": "weight reduction, insulin improvement",
        },
        "PCSK1": {
            "Target_Type": "Enzyme",
            "Biological_Domain": "Energy Homeostasis & Neuroendocrine Regulation",
            "Pathway": "Prohormone processing and neuropeptide maturation",
            "Mechanism_of_Action": "Alters precursor processing of satiety peptides, affecting appetite and energy balance.",
            "Modulation_Direction": "Inhibitor / activator",
            "Human_Genetic_Evidence": "Strong",
            "Human_Clinical_Evidence": "Weak",
            "Drug_Evidence": "No widely approved obesity drug direct to this target",
            "Clinical_Stage": "None",
            "Drug_Target": "No",
            "Tissue_Expression": "brain, gut, pancreas",
            "Obesity_Phenotype": "BMI, appetite, obesity, hyperphagia",
            "Clinical_Outcome": "weight reduction, appetite suppression",
        },
        "POMC": {
            "Target_Type": "Peptide precursor",
            "Biological_Domain": "Energy Homeostasis & Neuroendocrine Regulation",
            "Pathway": "Melanocortin peptide synthesis and satiety signaling",
            "Mechanism_of_Action": "Enhances satiety signaling through melanocortin-mediated pathways.",
            "Modulation_Direction": "Agonist / activator",
            "Human_Genetic_Evidence": "Strong",
            "Human_Clinical_Evidence": "Weak",
            "Drug_Evidence": "Indirect melanocortin pathway compounds",
            "Clinical_Stage": "Preclinical",
            "Drug_Target": "No",
            "Tissue_Expression": "brain, pituitary, hypothalamus",
            "Obesity_Phenotype": "BMI, appetite, adiposity",
            "Clinical_Outcome": "weight reduction, appetite suppression",
        },
        "GIPR": {
            "Target_Type": "Receptor",
            "Biological_Domain": "Gut Microbiome & Intestinal Biology",
            "Pathway": "GIP signaling and incretin biology",
            "Mechanism_of_Action": "Modifies gut hormone signaling to regulate insulin and energy balance.",
            "Modulation_Direction": "Agonist / antagonist",
            "Human_Genetic_Evidence": "Moderate",
            "Human_Clinical_Evidence": "Moderate",
            "Drug_Evidence": "GIP/GIPR dual agonists and incretin combination drugs",
            "Clinical_Stage": "Phase 2 / Phase 3",
            "Drug_Target": "Yes",
            "Tissue_Expression": "gut, pancreas, adipose",
            "Obesity_Phenotype": "BMI, glucose control, adiposity, insulin resistance",
            "Clinical_Outcome": "weight reduction, glucose improvement",
        },
        "GLP1R": {
            "Target_Type": "Receptor",
            "Biological_Domain": "Gut Microbiome & Intestinal Biology",
            "Pathway": "GLP-1 incretin signaling",
            "Mechanism_of_Action": "Enhances satiety and glucose control while reducing food intake and gastric emptying.",
            "Modulation_Direction": "Agonist / activator",
            "Human_Genetic_Evidence": "Moderate",
            "Human_Clinical_Evidence": "Strong",
            "Drug_Evidence": "GLP-1 receptor agonists such as liraglutide and semaglutide",
            "Clinical_Stage": "Approved / Phase 3",
            "Drug_Target": "Yes",
            "Tissue_Expression": "gut, pancreas, brain",
            "Obesity_Phenotype": "BMI, appetite, adiposity, insulin resistance",
            "Clinical_Outcome": "weight reduction, glucose improvement",
        },
        "PPARG": {
            "Target_Type": "Transcription factor",
            "Biological_Domain": "Lipid Metabolism & Lipid Handling",
            "Pathway": "PPAR-gamma adipocyte differentiation and lipid handling",
            "Mechanism_of_Action": "Promotes adipocyte function and lipid storage while improving insulin sensitivity.",
            "Modulation_Direction": "Agonist / activator",
            "Human_Genetic_Evidence": "Moderate",
            "Human_Clinical_Evidence": "Moderate",
            "Drug_Evidence": "TZD classes and PPAR-gamma modulators",
            "Clinical_Stage": "Approved / development",
            "Drug_Target": "Yes",
            "Tissue_Expression": "adipose, liver, muscle",
            "Obesity_Phenotype": "adiposity, insulin resistance, lipid profile",
            "Clinical_Outcome": "weight modification, glucose improvement, lipid improvement",
        },
        "FTO": {
            "Target_Type": "Enzyme",
            "Biological_Domain": "Genetic, Epigenetic & Developmental Programming",
            "Pathway": "RNA demethylation and adipocyte regulation",
            "Mechanism_of_Action": "Alters mRNA methylation and expression programs that influence adipogenesis and energy balance.",
            "Modulation_Direction": "Inhibitor / modulator",
            "Human_Genetic_Evidence": "Strong",
            "Human_Clinical_Evidence": "Weak",
            "Drug_Evidence": "No clear approved obesity therapy directly targeting FTO",
            "Clinical_Stage": "None",
            "Drug_Target": "No",
            "Tissue_Expression": "brain, adipose, liver",
            "Obesity_Phenotype": "BMI, adiposity, appetite, insulin resistance",
            "Clinical_Outcome": "weight reduction, body composition improvement",
        },
        "ADCY3": {
            "Target_Type": "Enzyme",
            "Biological_Domain": "Energy Homeostasis & Neuroendocrine Regulation",
            "Pathway": "cAMP signaling in appetite and adiposity",
            "Mechanism_of_Action": "Modulates cAMP signaling to influence feeding behavior and metabolic regulation.",
            "Modulation_Direction": "Inhibitor / activator",
            "Human_Genetic_Evidence": "Moderate",
            "Human_Clinical_Evidence": "Weak",
            "Drug_Evidence": "No direct obesity drug in the current catalog",
            "Clinical_Stage": "None",
            "Drug_Target": "No",
            "Tissue_Expression": "brain, adipose, pancreas",
            "Obesity_Phenotype": "BMI, appetite, adiposity",
            "Clinical_Outcome": "weight reduction, appetite suppression",
        },
        "SLC6A3": {
            "Target_Type": "Transporter",
            "Biological_Domain": "Energy Homeostasis & Neuroendocrine Regulation",
            "Pathway": "Dopamine reuptake and reward-driven feeding",
            "Mechanism_of_Action": "Alters dopamine signaling, especially in central feeding and reward circuits.",
            "Modulation_Direction": "Inhibitor / modulator",
            "Human_Genetic_Evidence": "Moderate",
            "Human_Clinical_Evidence": "Weak",
            "Drug_Evidence": "Dopamine transporter inhibitors and centrally acting modulators",
            "Clinical_Stage": "Approved / development",
            "Drug_Target": "Yes",
            "Tissue_Expression": "brain, hypothalamus, striatum",
            "Obesity_Phenotype": "appetite, reward-related feeding, BMI",
            "Clinical_Outcome": "weight reduction, reduced eating reward",
        },
        "SLC6A2": {
            "Target_Type": "Transporter",
            "Biological_Domain": "Energy Homeostasis & Neuroendocrine Regulation",
            "Pathway": "Norepinephrine reuptake and autonomic regulation",
            "Mechanism_of_Action": "Modifies catecholamine signaling in feeding and energy expenditure pathways.",
            "Modulation_Direction": "Inhibitor / modulator",
            "Human_Genetic_Evidence": "Moderate",
            "Human_Clinical_Evidence": "Weak",
            "Drug_Evidence": "Monoamine transport inhibitors and adrenergic modulators",
            "Clinical_Stage": "Approved / development",
            "Drug_Target": "Yes",
            "Tissue_Expression": "brain, sympathetic neurons, adipose",
            "Obesity_Phenotype": "BMI, energy expenditure, appetite",
            "Clinical_Outcome": "weight reduction, improved energy expenditure",
        },
        "BDNF": {
            "Target_Type": "Neurotrophic factor",
            "Biological_Domain": "Energy Homeostasis & Neuroendocrine Regulation",
            "Pathway": "Neurotrophic signaling and hypothalamic appetite regulation",
            "Mechanism_of_Action": "Supports neuronal survival and satiety signaling in brain circuits regulating food intake.",
            "Modulation_Direction": "Activator / replacement",
            "Human_Genetic_Evidence": "Moderate",
            "Human_Clinical_Evidence": "Weak",
            "Drug_Evidence": "Neurotrophic pathway modulators under preclinical evaluation",
            "Clinical_Stage": "Preclinical",
            "Drug_Target": "No",
            "Tissue_Expression": "brain, hypothalamus, adipose",
            "Obesity_Phenotype": "BMI, appetite, adiposity, neurobehavioral feeding",
            "Clinical_Outcome": "weight reduction, appetite control",
        },
        "OPRM1": {
            "Target_Type": "Receptor",
            "Biological_Domain": "Energy Homeostasis & Neuroendocrine Regulation",
            "Pathway": "Opioid signaling and reward-related feeding",
            "Mechanism_of_Action": "Modulates reward and hedonic feeding, affecting energy intake and eating behavior.",
            "Modulation_Direction": "Antagonist / modulator",
            "Human_Genetic_Evidence": "Weak",
            "Human_Clinical_Evidence": "Weak",
            "Drug_Evidence": "Opioid receptor antagonists and reward pathway modulators",
            "Clinical_Stage": "Approved / development",
            "Drug_Target": "Yes",
            "Tissue_Expression": "brain, reward circuits, gut",
            "Obesity_Phenotype": "appetite, hedonic feeding, BMI",
            "Clinical_Outcome": "weight reduction, reduced food reward",
        },
        "PNLIP": {
            "Target_Type": "Enzyme",
            "Biological_Domain": "Lipid Metabolism & Lipid Handling",
            "Pathway": "Pancreatic lipase-mediated digestion and fat absorption",
            "Mechanism_of_Action": "Inhibits intestinal fat digestion, reducing caloric absorption and adiposity.",
            "Modulation_Direction": "Inhibitor",
            "Human_Genetic_Evidence": "Weak",
            "Human_Clinical_Evidence": "Weak",
            "Drug_Evidence": "Pancreatic lipase inhibitors and fat-absorption modulators",
            "Clinical_Stage": "Approved / development",
            "Drug_Target": "Yes",
            "Tissue_Expression": "gut, pancreas",
            "Obesity_Phenotype": "BMI, adiposity, lipid handling",
            "Clinical_Outcome": "weight reduction, lipid improvement",
        },
        "SLC6A4": {
            "Target_Type": "Transporter",
            "Biological_Domain": "Energy Homeostasis & Neuroendocrine Regulation",
            "Pathway": "Serotonin reuptake and appetite regulation",
            "Mechanism_of_Action": "Modulates serotonergic signaling involved in satiety and meal termination.",
            "Modulation_Direction": "Inhibitor / modulator",
            "Human_Genetic_Evidence": "Moderate",
            "Human_Clinical_Evidence": "Moderate",
            "Drug_Evidence": "Serotonin reuptake inhibitors and appetite-modulating drugs",
            "Clinical_Stage": "Approved / development",
            "Drug_Target": "Yes",
            "Tissue_Expression": "brain, gut, platelets",
            "Obesity_Phenotype": "BMI, appetite, satiety, mood-related feeding",
            "Clinical_Outcome": "weight reduction, appetite control",
        },
        "HTR2C": {
            "Target_Type": "Receptor",
            "Biological_Domain": "Energy Homeostasis & Neuroendocrine Regulation",
            "Pathway": "Serotonin-mediated satiety signaling",
            "Mechanism_of_Action": "Activates satiety and reduces feeding behavior through serotonergic pathways.",
            "Modulation_Direction": "Agonist / activator",
            "Human_Genetic_Evidence": "Strong",
            "Human_Clinical_Evidence": "Moderate",
            "Drug_Evidence": "Serotonin receptor modulators and obesity therapeutics",
            "Clinical_Stage": "Approved / development",
            "Drug_Target": "Yes",
            "Tissue_Expression": "brain, hypothalamus",
            "Obesity_Phenotype": "BMI, appetite, adiposity, satiety",
            "Clinical_Outcome": "weight reduction, appetite suppression",
        },
    }

    profile = target_profiles.get(symbol, default_profile.copy())
    if symbol and symbol not in target_profiles:
        profile["Target_Type"] = profile["Target_Type"] if profile["Target_Type"] != "Unknown" else "Unclassified candidate"
        profile["Biological_Domain"] = default_profile["Biological_Domain"]
        profile["Mechanism_of_Action"] = (
            f"{label} is a candidate obesity target; mechanism and tissue specificity require manual review."
        )
    return profile


def classify_catalog_target(gene: dict[str, Any]) -> tuple[str, str]:
    scores = gene.get("datatype_scores", {})
    overall = float(gene.get("overall_score", 0) or 0)
    genetic = float(scores.get("genetic_association", 0) or 0)
    if overall >= KNOWN_EVIDENCE_THRESHOLD:
        return (
            "Known",
            f"Open Targets integrated target-disease score is at least {KNOWN_EVIDENCE_THRESHOLD:.2f}; clinical validation still requires review.",
        )
    if overall >= EMERGING_EVIDENCE_THRESHOLD or genetic >= EMERGING_EVIDENCE_THRESHOLD:
        return (
            "Emerging",
            f"Open Targets integrated or genetic association score is at least {EMERGING_EVIDENCE_THRESHOLD:.2f}, below the provisional known-evidence threshold.",
        )
    return (
        "Novel",
        "Low Open Targets integrated and genetic-association scores in this source-bounded first pass; not proof of no prior work.",
    )


def catalog_rows(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for rank, gene in enumerate(catalog.get("genes", []), start=1):
        scores = gene.get("datatype_scores", {})
        maturity, rationale = classify_catalog_target(gene)
        profile = obesity_target_profile(gene.get("gene", ""), gene.get("gene_name", ""))
        row = {
            "Rank": rank,
            "Gene": gene["gene"],
            "Gene name": gene.get("gene_name", ""),
            "Open Targets score": gene.get("overall_score"),
            "Genetic association": scores.get("genetic_association"),
            "Genetic literature": scores.get("genetic_literature"),
            "Literature": scores.get("literature"),
            "Animal model": scores.get("animal_model"),
            "Clinical evidence": scores.get("clinical"),
            "Known drug": scores.get("known_drug"),
            "Provisional class": maturity,
            "Classification basis": rationale,
            "Target_Type": profile["Target_Type"],
            "Biological_Domain": profile["Biological_Domain"],
            "Pathway": profile["Pathway"],
            "Mechanism_of_Action": profile["Mechanism_of_Action"],
            "Modulation_Direction": profile["Modulation_Direction"],
            "Human_Genetic_Evidence": profile["Human_Genetic_Evidence"],
            "Human_Clinical_Evidence": profile["Human_Clinical_Evidence"],
            "Drug_Evidence": profile["Drug_Evidence"],
            "Clinical_Stage": profile["Clinical_Stage"],
            "Drug_Target": profile["Drug_Target"],
            "Tissue_Expression": profile["Tissue_Expression"],
            "Obesity_Phenotype": profile["Obesity_Phenotype"],
            "Clinical_Outcome": profile["Clinical_Outcome"],
        }
        rows.append(row)
    return rows


def parse_gene_symbols(raw: str, limit: int = 20) -> list[str]:
    symbols = list(dict.fromkeys(
        symbol.strip().upper()
        for symbol in raw.replace(";", ",").replace("\n", ",").split(",")
        if symbol.strip()
    ))
    if len(symbols) > limit:
        raise ValueError(f"Enter no more than {limit} gene symbols per run.")
    invalid = [
        symbol for symbol in symbols
        if not symbol.replace("-", "").replace(".", "").replace("_", "").isalnum()
    ]
    if invalid:
        raise ValueError("Invalid gene symbol(s): " + ", ".join(invalid))
    return symbols