"""NVIDIA's 8-K filings from SEC EDGAR, used to confirm a CEO change."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Any

import httpx

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
NVIDIA_CIK = 1045810


@dataclass(frozen=True)
class Filing:
    accession: str
    form: str
    filed: date
    document: str
    url: str


def recent_8ks(user_agent: str, today: date, days: int = 60, cik: int = NVIDIA_CIK) -> list[Filing]:
    payload = _get_json(SUBMISSIONS_URL.format(cik=cik), user_agent)
    return parse_submissions(payload, today - timedelta(days=days), cik)


def parse_submissions(payload: dict[str, Any], since: date, cik: int = NVIDIA_CIK) -> list[Filing]:
    recent = payload.get("filings", {}).get("recent", {})
    columns = zip(
        recent.get("accessionNumber", []),
        recent.get("form", []),
        recent.get("filingDate", []),
        recent.get("primaryDocument", []),
        strict=False,
    )
    filings = []
    for accession, form, filed, document in columns:
        if form not in ("8-K", "8-K/A") or not document:
            continue
        filed_on = date.fromisoformat(filed)
        if filed_on < since:
            continue
        url = ARCHIVE_URL.format(cik=cik, accession=accession.replace("-", ""), document=document)
        filings.append(Filing(accession, form, filed_on, document, url))
    return sorted(filings, key=lambda f: f.filed)


def filing_text(filing: Filing, user_agent: str) -> str:
    try:
        response = httpx.get(filing.url, headers={"User-Agent": user_agent}, timeout=30)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"SEC filing {filing.accession}: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise RuntimeError(f"SEC filing {filing.accession}: HTTP {response.status_code}")
    return html_to_text(response.text)


def _get_json(url: str, user_agent: str) -> Any:
    try:
        response = httpx.get(url, headers={"User-Agent": user_agent}, timeout=30)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"SEC: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise RuntimeError(f"SEC: HTTP {response.status_code}")
    return response.json()


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("p", "div", "br", "tr", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextParser()
    parser.feed(markup)
    text = html.unescape("".join(parser.parts)).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()
