"""PubMed efetch: abstracts + MeSH + chemicals + keywords.

esummary carries no abstract or MeSH, so we fetch the full PubMed XML in
batches and parse out the fields Track B needs to link diseases / chemicals /
genes to topics. One batched call covers ~200 papers.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .. import config
from .http_cache import get_text


def _common_params() -> dict[str, Any]:
    params: dict[str, Any] = {"tool": config.NCBI_TOOL, "email": config.NCBI_EMAIL}
    if config.NCBI_API_KEY:
        params["api_key"] = config.NCBI_API_KEY
    return params


def _parse_article(art: ET.Element) -> dict[str, Any] | None:
    pmid_el = art.find(".//MedlineCitation/PMID")
    if pmid_el is None or not pmid_el.text:
        return None
    pmid = pmid_el.text.strip()

    # Abstract: may be split into labelled sections; join them in order.
    parts = []
    for ab in art.findall(".//Abstract/AbstractText"):
        label = ab.get("Label")
        text = "".join(ab.itertext()).strip()
        if text:
            parts.append(f"{label}: {text}" if label else text)
    abstract = "\n".join(parts) or None

    # MeSH descriptors (disease / anatomy / concept terms) with UI + major flag.
    mesh = []
    for mh in art.findall(".//MeshHeadingList/MeshHeading/DescriptorName"):
        if mh.text:
            mesh.append(
                {
                    "term": mh.text.strip(),
                    "ui": mh.get("UI"),
                    "major": mh.get("MajorTopicYN") == "Y",
                }
            )

    # Chemicals / substances (carry drug + gene-product descriptors).
    chemicals = []
    for ch in art.findall(".//ChemicalList/Chemical/NameOfSubstance"):
        if ch.text:
            chemicals.append({"term": ch.text.strip(), "ui": ch.get("UI")})

    keywords = [
        kw.text.strip()
        for kw in art.findall(".//KeywordList/Keyword")
        if kw.text and kw.text.strip()
    ]

    return {
        "pmid": pmid,
        "abstract": abstract,
        "mesh": mesh,
        "chemicals": chemicals,
        "keywords": keywords,
    }


def get_details(pmids: list[str], log=print) -> dict[str, dict[str, Any]]:
    """Return {pmid: {abstract, mesh, chemicals, keywords}} for the given PMIDs."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(pmids), 200):
        batch = pmids[i : i + 200]
        params = _common_params()
        params.update({"db": "pubmed", "id": ",".join(batch), "retmode": "xml"})
        xml = get_text(
            f"{config.EUTILS_BASE}/efetch.fcgi",
            params=params,
            min_interval=config.NCBI_MIN_INTERVAL,
            label="efetch",
        )
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            log(f"[efetch] parse error on batch {i}-{i + len(batch)}; skipping")
            continue
        for art in root.findall(".//PubmedArticle"):
            rec = _parse_article(art)
            if rec:
                out[rec["pmid"]] = rec
        if (i // 200) % 5 == 0:
            log(f"[efetch] details {min(i + 200, len(pmids))}/{len(pmids)}")
    return out
