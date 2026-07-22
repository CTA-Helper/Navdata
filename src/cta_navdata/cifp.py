"""Reads the airport and approach records that ENR 1.8 corrections need from a CIFP file.

The CIFP is ARINC 424-18: fixed-width 132-character records identified by a section code
(column 5) and subsection code (column 13). Only airport reference points (``PA``) and
airport approach procedures (``PF``) matter here — ENR 1.8 5.c forbids correcting SID, ODP
and STAR altitudes, so those sections are skipped.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import arinc424.record

_SECTION = slice(4, 6)
_SUBSECTION = 12

# A record numbered 0 carries no continuations; one numbered 1 is the first of several.
# Both are primary records. Treating only 0 as primary silently drops every approach that
# has a Level of Service continuation record, which is most of them.
_PRIMARY_CONTINUATION_NUMBERS = ("0", "1")

# Route type (column 20) distinguishes the transitions feeding an approach from the final
# approach route, which instead carries the approach type letter.
_APPROACH_TRANSITION = "A"

_RUNWAY_FIX = re.compile(r"^RW(\d{2}[LCRB]?)$")
_APPROACH_IDENTIFIER = re.compile(r"^[A-Z](?P<runway>\d{2}[LCR]?)")


@dataclass(frozen=True)
class Airport:
    """An airport reference point. ``elevation`` is the datum every correction subtracts."""

    icao_identifier: str
    faa_identifier: str | None
    name: str
    elevation: int | None
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class Leg:
    """One leg of an approach procedure, as published."""

    identifier: str | None
    transition: str | None
    sequence: int
    leg_type: str
    waypoint_description: str
    altitude_description: str
    altitude: int | None
    altitude2: int | None

    @property
    def is_flyover(self) -> bool:
        return self.waypoint_description[1] == "Y"


@dataclass(frozen=True)
class Approach:
    """A published instrument approach procedure and all of its transitions."""

    airport: str
    identifier: str
    route_type: str
    legs: tuple[Leg, ...]

    @property
    def final_route(self) -> tuple[Leg, ...]:
        """The common route flown by every transition, from the IF/FACF to the missed hold."""
        return tuple(leg for leg in self.legs if leg.transition is None)

    @property
    def transition_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(leg.transition for leg in self.legs if leg.transition))

    @property
    def runway(self) -> str | None:
        """The runway served, or ``None`` for a circling-only procedure."""
        for leg in self.legs:
            if leg.identifier and (match := _RUNWAY_FIX.match(leg.identifier)):
                return match.group(1)
        match = _APPROACH_IDENTIFIER.match(self.identifier)
        return match.group("runway") if match else None


@dataclass(frozen=True)
class CIFP:
    """The subset of a CIFP file this tool consumes."""

    airports: dict[str, Airport]
    approaches: dict[str, list[Approach]]


def read(path: Path) -> CIFP:
    """Parse ``path`` (an extracted ``FAACIFP18`` file) into airports and approaches."""
    airports: dict[str, Airport] = {}
    legs: dict[tuple[str, str], list[tuple[str, Leg]]] = defaultdict(list)

    for subsection, fields in _primary_records(path):
        if subsection == "A":
            airport = _airport(fields)
            airports[airport.icao_identifier] = airport
        else:
            key = (
                fields["Airport Identifier"].strip(),
                fields["SID/STAR/Approach Identifier"].strip(),
            )
            legs[key].append((fields["Route Type"], _leg(fields)))

    approaches: dict[str, list[Approach]] = defaultdict(list)
    for (airport_identifier, identifier), routes in legs.items():
        approaches[airport_identifier].append(
            Approach(
                airport=airport_identifier,
                identifier=identifier,
                route_type=_final_route_type(routes),
                legs=tuple(leg for _, leg in routes),
            )
        )
    return CIFP(airports=airports, approaches=dict(approaches))


def _primary_records(path: Path) -> Iterator[tuple[str, dict[str, str]]]:
    """Yield ``(subsection, fields)`` for each primary airport and approach record, in file order.

    The continuation record number sits in a different column for each record type, so it is
    read from the parsed field rather than a fixed offset.

    The area code (columns 2-4) is deliberately ignored: it is not country-aligned, and
    filtering on ``USA`` would drop most of Alaska.
    """
    record = arinc424.record.Record()
    with path.open(encoding="utf-8", errors="replace") as cifp:
        for line in cifp:
            if line[_SECTION] != "P " or line[_SUBSECTION] not in "AF":
                continue
            if not record.read(line.rstrip("\n")):
                continue
            fields = {field.name: field.value for field in record.fields}
            if fields.get("Continuation Record No") in _PRIMARY_CONTINUATION_NUMBERS:
                yield line[_SUBSECTION], fields


def _airport(fields: dict[str, str]) -> Airport:
    faa_identifier = fields["ATA/IATA Designator"].strip()
    return Airport(
        icao_identifier=fields["Airport ICAO Identifier"].strip(),
        faa_identifier=faa_identifier or None,
        name=fields["Airport Name"].strip(),
        elevation=_feet(fields["Airport Elevation"]),
        latitude=_degrees(fields["Airport Reference Pt. Latitude"]),
        longitude=_degrees(fields["Airport Reference Pt. Longitude"]),
    )


def _leg(fields: dict[str, str]) -> Leg:
    transition = fields["Transition Identifier"].strip()
    identifier = fields["Fix Identifier"].strip()
    return Leg(
        identifier=identifier or None,
        transition=transition if fields["Route Type"] == _APPROACH_TRANSITION else None,
        sequence=int(fields["Sequence Number"]),
        leg_type=fields["Path and Termination"].strip(),
        waypoint_description=f"{fields['Waypoint Description Code']:<4}",
        altitude_description=fields["Altitude Description"],
        altitude=_feet(fields["Altitude"]),
        altitude2=_feet(fields["Altitude (2)"]),
    )


def _final_route_type(routes: list[tuple[str, Leg]]) -> str:
    """The approach type letter, taken from whichever legs are not a transition."""
    return next(
        (route_type for route_type, _ in routes if route_type != _APPROACH_TRANSITION),
        _APPROACH_TRANSITION,
    )


def _feet(value: str) -> int | None:
    """Parse an altitude or elevation field. Values below sea level carry a leading minus."""
    value = value.strip()
    return int(value) if value.lstrip("-").isdigit() else None


def _degrees(value: str) -> float | None:
    """Parse a packed hemisphere/degrees/minutes/seconds coordinate, e.g. ``N46545870``."""
    value = value.strip()
    if len(value) < 9 or value[0] not in "NSEW":
        return None
    hemisphere, digits = value[0], value[1:]
    degrees = int(digits[:-6])
    minutes, seconds = int(digits[-6:-4]), int(digits[-4:]) / 100
    magnitude = degrees + minutes / 60 + seconds / 3600
    return -magnitude if hemisphere in "SW" else magnitude
