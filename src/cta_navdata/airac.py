"""AIRAC cycle arithmetic and the FAA source URLs derived from a cycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

CYCLE_LENGTH = timedelta(days=28)

# Any known effective date anchors the whole 28-day sequence. This one is taken from the
# d-TPP metafile for cycle 2607 (`from_edate="0901Z  07/09/26"`).
_ANCHOR = date(2026, 7, 9)

COLD_TEMPERATURE_AIRPORTS_URL = "https://aeronav.faa.gov/d-tpp/Cold_Temp_Airports.pdf"

# NASR names its subscription after the effective date in English, which `%b` would render in
# whatever locale the generator happens to run under.
_MONTH_ABBREVIATIONS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip


@dataclass(frozen=True, order=True)
class Cycle:
    """A 28-day AIRAC cycle, identified by the date it takes effect."""

    effective: date

    @property
    def expires(self) -> date:
        """The date the following cycle takes effect."""
        return self.effective + CYCLE_LENGTH

    @property
    def identifier(self) -> str:
        """The FAA cycle identifier, e.g. ``"2607"``."""
        return f"{self.effective.year % 100:02d}{self.ordinal_in_year:02d}"

    @property
    def ordinal_in_year(self) -> int:
        """This cycle's 1-based position among the cycles taking effect in its year."""
        since_first = self.effective - _first_effective_in(self.effective.year)
        return since_first // CYCLE_LENGTH + 1

    @property
    def previous(self) -> Cycle:
        return Cycle(self.effective - CYCLE_LENGTH)

    @property
    def next(self) -> Cycle:
        return Cycle(self.effective + CYCLE_LENGTH)

    @property
    def cifp_url(self) -> str:
        """The CIFP zip for this cycle. The FAA retains roughly six cycles."""
        return f"https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_{self.effective:%y%m%d}.zip"

    @property
    def nasr_url(self) -> str:
        """The NASR subscription zip for this cycle, in its CSV form."""
        month = _MONTH_ABBREVIATIONS[self.effective.month - 1]
        return (
            "https://nfdc.faa.gov/webContent/28DaySub/extra/"
            f"{self.effective.day:02d}_{month}_{self.effective.year}_CSV.zip"
        )

    @property
    def dtpp_metafile_url(self) -> str:
        """The d-TPP metafile for this cycle. Only the current and next cycles are retained."""
        return f"https://aeronav.faa.gov/d-tpp/{self.identifier}/xml_data/d-TPP_Metafile.xml"

    def chart_url(self, pdf_name: str) -> str:
        """The URL of an approach plate, given its ``pdf_name`` from the d-TPP metafile."""
        return f"https://aeronav.faa.gov/d-tpp/{self.identifier}/{pdf_name}"

    def covers(self, day: date) -> bool:
        return self.effective <= day < self.expires

    def __str__(self) -> str:
        return self.identifier


def containing(day: date) -> Cycle:
    """The cycle in effect on ``day``."""
    return Cycle(day - timedelta(days=(day - _ANCHOR).days % CYCLE_LENGTH.days))


def current(today: date | None = None) -> Cycle:
    return containing(today or date.today())


def resolve(selector: str, today: date | None = None) -> Cycle:
    """Resolve a CLI cycle selector: ``current``, ``next``, or an ISO date within a cycle."""
    if selector == "current":
        return current(today)
    if selector == "next":
        return current(today).next
    return containing(date.fromisoformat(selector))


def _first_effective_in(year: int) -> date:
    """The earliest cycle effective date falling within ``year``."""
    aligned = containing(date(year, 1, 1)).effective
    return aligned if aligned.year == year else aligned + CYCLE_LENGTH
