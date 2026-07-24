"""Reads the d-TPP metafile for official approach names and approach plate URLs.

The metafile names charts the way pilots do ("RNAV (GPS) Y RWY 12") where the CIFP names
them the way ARINC 424 does ("R12-Y"), and it gives the plate's PDF filename. Since ARINC
424 publishes no minima, that plate is where the pilot reads the DA or MDA the final segment
correction needs.

Charts are matched to procedures on approach type, runway and suffix letter. A chart that
does not match unambiguously is left unmatched rather than guessed — a plausible-looking
link to the wrong approach plate would be worse than no link.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from defusedxml import ElementTree

from .airac import Cycle

# ARINC 424 approach type letter (the first character of the approach identifier) to the
# names the d-TPP uses for that procedure type.
_APPROACH_TYPES = {
    "B": ("LOC BC",),
    "D": ("VOR/DME",),
    "F": ("FMS",),
    "G": ("IGS",),
    "H": ("RNAV (RNP)",),
    "I": ("ILS",),
    "J": ("GLS",),
    "L": ("LOC",),
    "M": ("MLS",),
    "N": ("NDB",),
    "P": ("GPS",),
    "Q": ("NDB/DME",),
    "R": ("RNAV (GPS)",),
    "S": ("VOR/DME", "VOR"),
    "T": ("TACAN",),
    "U": ("SDF",),
    "V": ("VOR",),
    "X": ("LDA",),
}

_IAP = "IAP"
_RUNWAY = re.compile(r"RWY\s+(\d{2}[LCR]?)")
_SUFFIX_BEFORE_RUNWAY = re.compile(r"\b([A-Z])\s+RWY\b")
_CIRCLING_SUFFIX = re.compile(r"-([A-Z])\s*$")

# "R12-Y", "H02LZ", "S07": type letter, runway, then an optional suffix letter.
_PROCEDURE = re.compile(r"^(?P<type>[A-Z])(?P<runway>\d{2}[LCR]?)-?(?P<suffix>[A-Z])?$")
# "VOR-A", "RNV-B": a circling procedure, named by type and letter with no runway.
_CIRCLING_PROCEDURE = re.compile(r"^(?P<type>[A-Z]{3})-?(?P<suffix>[A-Z])$")

_CIRCLING_TYPES = {
    "GPS": ("RNAV (GPS)", "GPS"),
    "LBC": ("LOC BC",),
    "LDA": ("LDA",),
    "LOC": ("LOC",),
    "NDB": ("NDB",),
    "RNV": ("RNAV (GPS)",),
    "VDM": ("VOR/DME",),
    "VOR": ("VOR",),
}


@dataclass(frozen=True)
class Chart:
    """One published approach plate."""

    name: str
    url: str


@dataclass(frozen=True)
class Location:
    """An airport's place, as the d-TPP names it.

    CIFP airport names are upper-cased and truncated to 30 characters and carry no city or
    state, so this is the only source that lets a pilot find an airport by where it is.
    """

    name: str
    city: str
    state: str
    state_name: str


def read(path: Path, cycle: Cycle) -> dict[str, dict[str, Chart]]:
    """Map each airport's ICAO identifier to its approach charts, keyed by matching key."""
    charts: dict[str, dict[str, Chart]] = {}
    for airport in ElementTree.parse(path).getroot().iter("airport_name"):
        identifier = airport.get("icao_ident") or airport.get("apt_ident")
        if not identifier:
            continue
        if indexed := _index(airport, cycle):
            charts[identifier] = indexed
    return charts


def locations(path: Path) -> dict[str, Location]:
    """Map each airport's ICAO identifier to its place, read from the metafile's hierarchy."""
    places: dict[str, Location] = {}
    for state in ElementTree.parse(path).getroot().iter("state_code"):
        state_code = state.get("ID") or ""
        state_name = state.get("state_fullname") or ""
        for city in state.iter("city_name"):
            city_name = city.get("ID") or ""
            for airport in city.iter("airport_name"):
                identifier = airport.get("icao_ident") or airport.get("apt_ident")
                if not identifier:
                    continue
                places[identifier] = Location(
                    name=airport.get("ID") or "",
                    city=city_name,
                    state=state_code,
                    state_name=state_name,
                )
    return places


def chart_for(procedure: str, charts: dict[str, Chart]) -> Chart | None:
    """Find the plate for a CIFP approach identifier, e.g. ``"R12-Y"``."""
    key = _procedure_key(procedure)
    return charts.get(key) if key else None


def derive_name(procedure: str) -> str:
    """A readable name for a procedure with no matching chart, e.g. ``"R12-Y"`` to ``"RNAV (GPS) Y RWY 12"``."""
    if match := _PROCEDURE.match(procedure):
        types = _APPROACH_TYPES.get(match.group("type"))
        suffix = f" {match.group('suffix')}" if match.group("suffix") else ""
        return f"{types[0]}{suffix} RWY {match.group('runway')}" if types else procedure
    if match := _CIRCLING_PROCEDURE.match(procedure):
        types = _CIRCLING_TYPES.get(match.group("type"))
        return f"{types[0]}-{match.group('suffix')}" if types else procedure
    return procedure


def _index(airport, cycle: Cycle) -> dict[str, Chart]:
    """Key an airport's approach charts so procedures can be looked up against them.

    A chart naming more than one procedure type ("ILS OR LOC RWY 12") is indexed under each,
    since the CIFP publishes those as separate procedures.
    """
    by_key: dict[str, list[Chart]] = defaultdict(list)
    for record in airport.iter("record"):
        if record.findtext("chart_code") != _IAP:
            continue
        name = (record.findtext("chart_name") or "").strip()
        pdf = (record.findtext("pdf_name") or "").strip()
        if not name or not pdf:
            continue
        chart = Chart(name=name, url=cycle.chart_url(pdf))
        for key in _chart_keys(name):
            by_key[key].append(chart)

    # Two charts claiming one key means the key is not specific enough to trust.
    return {key: charts[0] for key, charts in by_key.items() if len(charts) == 1}


def _chart_keys(name: str) -> set[str]:
    """Every ``type|runway|suffix`` key a chart name can be matched by."""
    runway = _RUNWAY.search(name)
    if runway:
        suffix = _SUFFIX_BEFORE_RUNWAY.search(name)
        location = f"{runway.group(1)}|{suffix.group(1) if suffix else ''}"
    else:
        circling = _CIRCLING_SUFFIX.search(name)
        if not circling:
            return set()
        location = f"|{circling.group(1)}"
    return {f"{approach_type}|{location}" for approach_type in _types_named_in(name)}


def _types_named_in(name: str) -> set[str]:
    """The procedure types a chart name mentions, longest first so LOC BC beats LOC."""
    remaining = name
    found = set()
    for approach_type in sorted(
        {t for types in _APPROACH_TYPES.values() for t in types}, key=len, reverse=True
    ):
        if approach_type in remaining:
            found.add(approach_type)
            remaining = remaining.replace(approach_type, "")
    return found


def _procedure_key(procedure: str) -> str | None:
    if match := _PROCEDURE.match(procedure):
        types = _APPROACH_TYPES.get(match.group("type"))
        return f"{types[0]}|{match.group('runway')}|{match.group('suffix') or ''}" if types else None
    if match := _CIRCLING_PROCEDURE.match(procedure):
        types = _CIRCLING_TYPES.get(match.group("type"))
        return f"{types[0]}||{match.group('suffix')}" if types else None
    return None
