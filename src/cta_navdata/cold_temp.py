"""Scrapes the FAA's Cold Temperature Airports list.

The list is published only as a PDF, so this is the fragile link in the pipeline and its
dangerous failure mode is silent: if the table shifts, cell extraction still returns rows,
just with the X marks against the wrong segments. Every row is therefore read twice — once
from the extracted table cells and once from the geometry of the X glyphs — and the two must
agree exactly or the scrape fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path

import pdfplumber

from .validate import ValidationError

SEGMENT_HEADINGS = ("Initial", "Intermediate", "Final", "Missed")

# An X sits on its column's centre; adjacent columns are ~45pt apart, so this tolerance
# accepts normal typesetting jitter while still catching a shifted or inserted column.
_COLUMN_TOLERANCE = 15.0

_TITLE = "COLD TEMPERATURE AIRPORTS"
# Editions print either a window (``2 Oct 2025 – 3 Sep 2026``) or only the day the list takes
# effect (``Effective 04 September 2026``), standing until it is superseded.
_VALIDITY = re.compile(r"(\d{1,2} \w{3} \d{4})\s*[–-]\s*(\d{1,2} \w{3} \d{4})")
_EFFECTIVE = re.compile(r"Effective (\d{1,2} \w+ \d{4})")
_IDENTIFIER = re.compile(r"^[A-Z0-9]{3,4}$")
# The column is in Celsius, and some rows print the bare number.
_TEMPERATURE = re.compile(r"^([+-]?\d+)\s*C?$")

# The note that introduces the second table on the last page. Those airfields meet the CTA
# criteria but the procedures bind only FAA-authorised operators, not the military services.
_MILITARY_NOTE = "military airfields"


@dataclass(frozen=True)
class ColdTemperatureAirport:
    """One row of the CTA list."""

    identifier: str
    name: str
    restriction_temperature_c: int
    affected_segments: tuple[str, ...]
    military: bool


@dataclass(frozen=True)
class ColdTemperatureList:
    """The published list, with the validity window printed on its first page.

    ``effective_to`` is ``None`` for an edition that stands until it is superseded.
    """

    effective_from: date
    effective_to: date | None
    airports: tuple[ColdTemperatureAirport, ...]

    def covers(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_to is None or day <= self.effective_to)

    def by_identifier(self) -> dict[str, ColdTemperatureAirport]:
        return {airport.identifier: airport for airport in self.airports}


def scrape(path: Path) -> ColdTemperatureList:
    """Parse the Cold Temperature Airports PDF, failing loudly on any structural surprise."""
    airports: list[ColdTemperatureAirport] = []
    with pdfplumber.open(path) as pdf:
        effective_from, effective_to = _validity(pdf.pages[0])
        for page in pdf.pages:
            military_boundary = _military_boundary(page)
            for table in page.find_tables():
                airports.extend(_read_table(page, table, military_boundary))

    if not airports:
        raise ValidationError("Cold temperature PDF yielded no airports at all")
    _reject_duplicates(airports)
    return ColdTemperatureList(effective_from, effective_to, tuple(airports))


def _read_table(page, table, military_boundary: float | None) -> list[ColdTemperatureAirport]:
    """Read one table, cross-checking extracted cells against the geometry of the X marks."""
    columns = _segment_columns(page, table)
    if not columns:
        return []

    military = military_boundary is not None and table.bbox[1] > military_boundary
    rows = _reattach_stray_marks([_normalise(row) for row in table.extract()])
    airports = [_airport(row, military) for row in rows if _is_airport_row(row)]

    _reject_unreadable_rows(rows, page)
    _reject_mismatched_marks(page, table, columns, airports)
    return airports


def _segment_columns(page, table) -> dict[str, float]:
    """The x-centre of each segment column, read from this table's own heading row.

    Read per table rather than per page: the last page carries two tables whose columns do
    not line up with each other.
    """
    headings = {
        word["text"]: _centre(word)
        for word in _words_within(page, table.bbox)
        if word["text"] in SEGMENT_HEADINGS
    }
    if not headings:
        return {}
    if missing := set(SEGMENT_HEADINGS) - headings.keys():
        raise ValidationError(
            f"Cold temperature PDF page {page.page_number}: table at y={table.bbox[1]:.0f} "
            f"is missing the segment heading(s) {sorted(missing)}"
        )
    return headings


def _reject_mismatched_marks(
    page, table, columns: dict[str, float], airports: list[ColdTemperatureAirport]
) -> None:
    """Require the X glyphs on the page to agree with the segments read from the cells.

    This is the check that catches a silently shifted column: cell extraction would still
    produce plausible rows, but the marks would no longer land on the columns they were
    assigned to.
    """
    from_geometry: list[str] = []
    for word in _words_within(page, table.bbox):
        if word["text"] != "X":
            continue
        from_geometry.append(_column_at(_centre(word), columns, page, word))

    from_cells = [segment for airport in airports for segment in airport.affected_segments]
    if sorted(from_geometry) != sorted(from_cells):
        raise ValidationError(
            f"Cold temperature PDF page {page.page_number}: the X marks disagree with the "
            f"extracted table. Geometry found {_tally(from_geometry)}, cells found {_tally(from_cells)}"
        )


def _column_at(centre: float, columns: dict[str, float], page, word) -> str:
    """Name the column an X falls in, rejecting marks that are ambiguous or between columns."""
    matches = [name for name, column in columns.items() if abs(centre - column) <= _COLUMN_TOLERANCE]
    if len(matches) != 1:
        raise ValidationError(
            f"Cold temperature PDF page {page.page_number}: an X at x={centre:.1f}, "
            f"y={word['top']:.1f} matches {len(matches)} of the segment columns "
            f"{ {name: round(x, 1) for name, x in columns.items()} }; the table layout has changed"
        )
    return matches[0]


def _reject_unreadable_rows(rows: list[list[str]], page) -> None:
    """Every row must be an airport, a heading, or a state group label — nothing else."""
    for row in rows:
        if _is_airport_row(row) or _is_heading_row(row) or _is_group_row(row):
            continue
        raise ValidationError(f"Cold temperature PDF page {page.page_number}: cannot read row {row}")


def _reattach_stray_marks(rows: list[list[str]]) -> list[list[str]]:
    """Move marks set on a state label's line down onto the airport row beneath it.

    The FAA occasionally sets a row's X a line high, onto the state label above it (D55 under
    North Dakota in the 04 September 2026 edition). Only that exact shape is repaired — a label
    carrying marks over an airport row carrying none — so any other misplacement still fails.
    """
    for label, airport in pairwise(rows):
        if _is_marked_group_row(label) and _is_unmarked_airport_row(airport):
            airport[3:7], label[3:7] = label[3:7], airport[3:7]
    return rows


def _airport(row: list[str], military: bool) -> ColdTemperatureAirport:
    return ColdTemperatureAirport(
        identifier=row[0],
        name=row[1],
        restriction_temperature_c=int(_TEMPERATURE.match(row[2]).group(1)),
        affected_segments=tuple(
            heading for heading, cell in zip(SEGMENT_HEADINGS, row[3:7], strict=True) if cell
        ),
        military=military,
    )


def _is_airport_row(row: list[str]) -> bool:
    return _names_an_airport(row) and any(row[3:7])


def _is_unmarked_airport_row(row: list[str]) -> bool:
    return _names_an_airport(row) and not any(row[3:7])


def _names_an_airport(row: list[str]) -> bool:
    return len(row) >= 7 and bool(_IDENTIFIER.match(row[0])) and bool(_TEMPERATURE.match(row[2]))


def _is_marked_group_row(row: list[str]) -> bool:
    return len(row) >= 7 and bool(row[0]) and not any(row[1:3]) and any(row[3:7])


def _is_heading_row(row: list[str]) -> bool:
    return row[0] == "Identifier" or any(cell in SEGMENT_HEADINGS for cell in row)


def _is_group_row(row: list[str]) -> bool:
    """A state name spanning the table, e.g. ``Montana``."""
    return bool(row[0]) and not any(row[1:])


def _validity(page) -> tuple[date, date | None]:
    text = page.extract_text() or ""
    if _TITLE not in text:
        raise ValidationError(f"Cold temperature PDF does not begin with {_TITLE!r}; the source has changed")
    if window := _VALIDITY.search(text):
        return _day(window.group(1)), _day(window.group(2))
    if effective := _EFFECTIVE.search(text):
        return _day(effective.group(1)), None
    raise ValidationError("Cold temperature PDF has no readable validity date on page 1")


def _military_boundary(page) -> float | None:
    """The y of the note introducing the military table; tables below it are military."""
    text = page.extract_text() or ""
    if _MILITARY_NOTE not in text:
        return None
    notes = [word for word in page.extract_words() if word["text"] == "military"]
    return min(word["top"] for word in notes) if notes else None


def _reject_duplicates(airports: list[ColdTemperatureAirport]) -> None:
    seen: set[str] = set()
    for airport in airports:
        if airport.identifier in seen:
            raise ValidationError(f"Cold temperature PDF lists {airport.identifier} more than once")
        seen.add(airport.identifier)


def _words_within(page, bbox) -> list[dict]:
    left, top, right, bottom = bbox
    return [
        word
        for word in page.extract_words()
        if left <= word["x0"] and word["x1"] <= right and top <= word["top"] and word["bottom"] <= bottom
    ]


def _normalise(row: list[str | None]) -> list[str]:
    return [(cell or "").replace("\n", " ").strip() for cell in row]


def _centre(word: dict) -> float:
    return (word["x0"] + word["x1"]) / 2


def _day(text: str) -> date:
    """Read one of the validity dates the list prints, e.g. ``02 Oct 2025`` or ``04 September 2026``.

    The date carries no time of day and so no zone, and is narrowed to a ``date`` here before
    anything can read one into it.
    """
    for month in ("%b", "%B"):
        try:
            return datetime.strptime(text, f"%d {month} %Y").date()  # noqa: DTZ007
        except ValueError:
            continue
    raise ValidationError(f"Cold temperature PDF prints an unreadable date {text!r}")


def _tally(segments: list[str]) -> dict[str, int]:
    return {heading: segments.count(heading) for heading in SEGMENT_HEADINGS if segments.count(heading)}
