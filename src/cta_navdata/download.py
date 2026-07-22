"""Fetches the FAA source files, caching them so repeated runs don't re-download 25 MB."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .airac import COLD_TEMPERATURE_AIRPORTS_URL, Cycle

_CIFP_MEMBER = "FAACIFP18"
_TIMEOUT = httpx.Timeout(30.0, read=300.0)

# aeronav.faa.gov rejects the default httpx user agent.
_HEADERS = {"User-Agent": "cta-navdata (+https://github.com/SF50-TOLD/NavDataDistribution)"}


@dataclass(frozen=True)
class Source:
    """A downloaded file and where it came from, recorded in the output's provenance."""

    url: str
    path: Path
    retrieved_at: datetime


@dataclass(frozen=True)
class Sources:
    cifp: Source
    dtpp: Source
    cold_temperature: Source


def fetch_all(cycle: Cycle, cache: Path) -> Sources:
    """Download every source needed for ``cycle`` into ``cache``."""
    cache.mkdir(parents=True, exist_ok=True)
    return Sources(
        cifp=_fetch_cifp(cycle, cache),
        dtpp=fetch(cycle.dtpp_metafile_url, cache / f"d-TPP_{cycle.identifier}.xml"),
        cold_temperature=fetch(COLD_TEMPERATURE_AIRPORTS_URL, cache / "Cold_Temp_Airports.pdf"),
    )


def fetch(url: str, destination: Path) -> Source:
    """Download ``url`` to ``destination``, reusing it if already present."""
    if not destination.exists():
        _stream_to(url, destination)
    return Source(url=url, path=destination, retrieved_at=datetime.now(UTC))


def _fetch_cifp(cycle: Cycle, cache: Path) -> Source:
    """Download the CIFP zip and extract the single ARINC 424 file it contains."""
    archive = cache / f"CIFP_{cycle.effective:%y%m%d}.zip"
    source = fetch(cycle.cifp_url, archive)
    extracted = cache / f"{_CIFP_MEMBER}_{cycle.identifier}"
    if not extracted.exists():
        with zipfile.ZipFile(archive) as zipped:
            extracted.write_bytes(zipped.read(_CIFP_MEMBER))
    return Source(url=source.url, path=extracted, retrieved_at=source.retrieved_at)


def _stream_to(url: str, destination: Path) -> None:
    """Stream to a temporary file first, so an interrupted download can't poison the cache."""
    partial = destination.with_suffix(destination.suffix + ".partial")
    with httpx.stream("GET", url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as response:
        response.raise_for_status()
        with partial.open("wb") as output:
            for chunk in response.iter_bytes():
                output.write(chunk)
    partial.replace(destination)
