from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Protocol


class LiteratureClient(Protocol):
    name: str

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        ...


class HttpJsonClient:
    def __init__(self, *, timeout_seconds: int = 20) -> None:
        self.timeout_seconds = timeout_seconds

    def get_json(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_text(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> str:
        request = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return response.read().decode("utf-8")


def _encoded(params: dict[str, Any]) -> str:
    return urllib.parse.urlencode(params, doseq=True)


def _normalize_authors(authors: Any) -> list[str]:
    if not authors:
        return []
    normalized: list[str] = []
    for author in authors:
        if isinstance(author, str):
            normalized.append(author)
        elif isinstance(author, dict):
            name = author.get("name") or " ".join(
                part
                for part in [author.get("given"), author.get("family")]
                if part
            )
            if name:
                normalized.append(name)
    return normalized


def _source_record(
    *,
    title: str,
    authors: list[str],
    year: int,
    source: str,
    doi: str = "",
    url: str = "",
    literature_type: str = "research_article",
    abstract: str = "",
    database: str,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "title": title.strip(),
        "authors": authors,
        "year": int(year or 0),
        "source": source.strip(),
        "doi": doi.strip(),
        "url": url.strip(),
        "literature_type": literature_type,
        "abstract": abstract.strip(),
        "database": database,
        "raw": raw or {},
    }


class CrossrefClient:
    name = "crossref"

    def __init__(self, http: HttpJsonClient | None = None) -> None:
        self.http = http or HttpJsonClient()

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        url = "https://api.crossref.org/works?" + _encoded(
            {"query": query, "rows": limit, "select": "DOI,title,author,published-print,published-online,container-title,type,URL,abstract"}
        )
        data = self.http.get_json(url)
        records = []
        for item in data.get("message", {}).get("items", []):
            title = (item.get("title") or [""])[0]
            if not title:
                continue
            date_parts = (
                item.get("published-print", {}).get("date-parts")
                or item.get("published-online", {}).get("date-parts")
                or [[0]]
            )
            records.append(
                _source_record(
                    title=title,
                    authors=_normalize_authors(item.get("author")),
                    year=date_parts[0][0],
                    source=(item.get("container-title") or ["Crossref"])[0],
                    doi=item.get("DOI", ""),
                    url=item.get("URL", ""),
                    literature_type=item.get("type", "research_article"),
                    abstract=item.get("abstract", ""),
                    database=self.name,
                    raw=item,
                )
            )
        return records


class SemanticScholarClient:
    name = "semantic_scholar"

    def __init__(self, http: HttpJsonClient | None = None) -> None:
        self.http = http or HttpJsonClient()

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        params = _encoded(
            {
                "query": query,
                "limit": limit,
                "fields": "title,authors,year,venue,externalIds,url,abstract,publicationTypes",
            }
        )
        data = self.http.get_json(f"https://api.semanticscholar.org/graph/v1/paper/search?{params}")
        records = []
        for item in data.get("data", []):
            external_ids = item.get("externalIds") or {}
            records.append(
                _source_record(
                    title=item.get("title", ""),
                    authors=_normalize_authors(item.get("authors")),
                    year=item.get("year") or 0,
                    source=item.get("venue") or "Semantic Scholar",
                    doi=external_ids.get("DOI", ""),
                    url=item.get("url", ""),
                    literature_type=(item.get("publicationTypes") or ["research_article"])[0],
                    abstract=item.get("abstract") or "",
                    database=self.name,
                    raw=item,
                )
            )
        return [record for record in records if record["title"]]


class OpenAlexClient:
    name = "openalex"

    def __init__(self, http: HttpJsonClient | None = None) -> None:
        self.http = http or HttpJsonClient()

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        data = self.http.get_json(
            "https://api.openalex.org/works?" + _encoded({"search": query, "per-page": limit})
        )
        records = []
        for item in data.get("results", []):
            source = (
                (item.get("primary_location") or {})
                .get("source", {})
                .get("display_name", "OpenAlex")
            )
            records.append(
                _source_record(
                    title=item.get("title", ""),
                    authors=[
                        authorship.get("author", {}).get("display_name", "")
                        for authorship in item.get("authorships", [])
                        if authorship.get("author", {}).get("display_name")
                    ],
                    year=item.get("publication_year") or 0,
                    source=source,
                    doi=(item.get("doi") or "").replace("https://doi.org/", ""),
                    url=item.get("id", ""),
                    literature_type=item.get("type", "research_article"),
                    abstract=_openalex_abstract(item.get("abstract_inverted_index") or {}),
                    database=self.name,
                    raw=item,
                )
            )
        return [record for record in records if record["title"]]


class ArxivClient:
    name = "arxiv"

    def __init__(self, http: HttpJsonClient | None = None) -> None:
        self.http = http or HttpJsonClient()

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        url = "https://export.arxiv.org/api/query?" + _encoded(
            {"search_query": f"all:{query}", "start": 0, "max_results": limit}
        )
        text = self.http.get_text(url)
        root = ET.fromstring(text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        records = []
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
            year = int((entry.findtext("atom:published", default="0", namespaces=ns) or "0")[:4] or 0)
            authors = [
                author.findtext("atom:name", default="", namespaces=ns)
                for author in entry.findall("atom:author", ns)
            ]
            url_value = entry.findtext("atom:id", default="", namespaces=ns)
            records.append(
                _source_record(
                    title=title,
                    authors=[author for author in authors if author],
                    year=year,
                    source="arXiv",
                    url=url_value,
                    literature_type="preprint",
                    abstract=entry.findtext("atom:summary", default="", namespaces=ns) or "",
                    database=self.name,
                )
            )
        return [record for record in records if record["title"]]


class PubMedClient:
    name = "pubmed"

    def __init__(self, http: HttpJsonClient | None = None) -> None:
        self.http = http or HttpJsonClient()

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + _encoded(
            {"db": "pubmed", "term": query, "retmode": "json", "retmax": limit}
        )
        data = self.http.get_json(search_url)
        ids = data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + _encoded(
            {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
        )
        summary = self.http.get_json(summary_url)
        result = summary.get("result", {})
        records = []
        for pmid in ids:
            item = result.get(pmid, {})
            records.append(
                _source_record(
                    title=item.get("title", ""),
                    authors=_normalize_authors(item.get("authors")),
                    year=int(str(item.get("pubdate", "0"))[:4] or 0),
                    source=item.get("fulljournalname") or item.get("source") or "PubMed",
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    literature_type="research_article",
                    database=self.name,
                    raw=item,
                )
            )
        return [record for record in records if record["title"]]


class EuropePmcClient:
    name = "europe_pmc"

    def __init__(self, http: HttpJsonClient | None = None) -> None:
        self.http = http or HttpJsonClient()

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        data = self.http.get_json(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
            + _encoded({"query": query, "format": "json", "pageSize": limit})
        )
        records = []
        for item in data.get("resultList", {}).get("result", []):
            records.append(
                _source_record(
                    title=item.get("title", ""),
                    authors=[name.strip() for name in item.get("authorString", "").split(",") if name.strip()],
                    year=int(item.get("pubYear") or 0),
                    source=item.get("journalTitle") or "Europe PMC",
                    doi=item.get("doi", ""),
                    url=item.get("fullTextUrlList", {}).get("fullTextUrl", [{}])[0].get("url", "")
                    if item.get("fullTextUrlList")
                    else "",
                    literature_type=item.get("pubType", "research_article"),
                    abstract=item.get("abstractText", ""),
                    database=self.name,
                    raw=item,
                )
            )
        return [record for record in records if record["title"]]


class NasaAdsClient:
    name = "nasa_ads"

    def __init__(
        self, http: HttpJsonClient | None = None, token: str | None = None
    ) -> None:
        self.http = http or HttpJsonClient()
        self.token = token or os.getenv("NASA_ADS_TOKEN")

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        if not self.token:
            return []
        data = self.http.get_json(
            "https://api.adsabs.harvard.edu/v1/search/query?"
            + _encoded(
                {
                    "q": query,
                    "rows": limit,
                    "fl": "title,author,year,pub,doi,abstract,bibcode",
                }
            ),
            headers={"Authorization": f"Bearer {self.token}"},
        )
        records = []
        for item in data.get("response", {}).get("docs", []):
            doi = (item.get("doi") or [""])[0]
            bibcode = item.get("bibcode", "")
            records.append(
                _source_record(
                    title=(item.get("title") or [""])[0],
                    authors=item.get("author") or [],
                    year=int(item.get("year") or 0),
                    source=item.get("pub") or "NASA ADS",
                    doi=doi,
                    url=f"https://ui.adsabs.harvard.edu/abs/{urllib.parse.quote(bibcode)}/abstract"
                    if bibcode
                    else "",
                    literature_type="research_article",
                    abstract=item.get("abstract", ""),
                    database=self.name,
                    raw=item,
                )
            )
        return [record for record in records if record["title"]]


def default_literature_clients() -> list[LiteratureClient]:
    return [
        SemanticScholarClient(),
        OpenAlexClient(),
        CrossrefClient(),
        ArxivClient(),
        PubMedClient(),
        EuropePmcClient(),
        NasaAdsClient(),
    ]


def _openalex_abstract(inverted_index: dict[str, list[int]]) -> str:
    if not inverted_index:
        return ""
    words_by_position: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for position in positions:
            words_by_position[position] = word
    return " ".join(words_by_position[index] for index in sorted(words_by_position))
