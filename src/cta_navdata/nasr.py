"""Parses the FAA NASR airport file, which supplies each airport's identity and its codes."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AirportRecord:
    """One landing facility, as the NASR airport file describes it.

    Every other way of naming an airport is reassigned from time to time: Palm Beach became
    ``DJT``/``KDJT`` in cycle 2607, taking its location identifier, its ICAO code and its name
    with it. The site number FAA Form 5010 files the facility under does not move, which makes
    it the only identifier a saved favorite can safely be pinned to.
    """

    site_number: str
    faa_identifier: str
    icao_identifier: str | None
    city: str | None
    state: str | None
    state_name: str | None


def read(path: Path) -> dict[str, AirportRecord]:
    """Parse ``APT_BASE.csv`` into records, keyed by FAA location identifier.

    The location identifier is the key because it is what the CIFP records for every airport,
    making it the only field the two sources can be joined on. That it is unstable does no harm
    here: both sources are published for the same 28-day cycle, so they rename in step.
    """
    with path.open(encoding="utf-8-sig", newline="") as airports:
        return {record.faa_identifier: record for record in map(_record, csv.DictReader(airports))}


def _record(row: dict[str, str]) -> AirportRecord:
    return AirportRecord(
        # The type code distinguishes the airport from the heliport or seaplane base that may
        # share its number, and completes the key NASR files the facility under.
        site_number=_site_number(row["SITE_NO"]) + _text(row["SITE_TYPE_CODE"]),
        faa_identifier=_text(row["ARPT_ID"]),
        icao_identifier=_optional(row["ICAO_ID"]),
        city=_optional(row["CITY"]),
        state=_optional(row["STATE_CODE"]),
        state_name=_optional(row["STATE_NAME"]),
    )


def _site_number(value: str) -> str:
    """Read the number the facility is filed under, dropping the separator some renderings carry.

    FAA Form 5010 writes the type code onto the end of the number behind an asterisk, giving
    ``03555.*A``. The CSV subscription splits the two into columns and so publishes no asterisk,
    but one arriving here would silently rekey every airport, which the app persists in favorites
    and could not undo — so it is normalized out rather than trusted to stay absent.
    """
    return _text(value).replace("*", "")


def _text(value: str) -> str:
    return value.strip()


def _optional(value: str) -> str | None:
    """Read a column NASR leaves empty rather than absent, e.g. the ICAO code of a small field."""
    return _text(value) or None
