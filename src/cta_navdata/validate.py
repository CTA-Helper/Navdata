"""Sanity gates the generated document must clear before it may be published.

Nothing here tries to prove the data is correct — only that it is not obviously broken. A
failed gate aborts the run so that no release is cut, leaving consumers on the last known
good cycle rather than shipping something silently wrong.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

# A handful of procedures legitimately code no intermediate fix, but a FAF, a missed approach
# point and a missed holding altitude are present in every approach the FAA publishes. A drop
# below this share means the record layout moved, not that the data changed.
MINIMUM_REFERENCE_COVERAGE = 0.995

# Cycle-to-cycle churn is small; a fifth of the database appearing or vanishing is a bug.
MAXIMUM_COUNT_DRIFT = 0.20

# Floors that catch a source truncated or half-downloaded.
MINIMUM_COUNTS = {"airports": 1000, "approaches": 5000, "coldTemperatureAirports": 50}


class ValidationError(Exception):
    """A gate failed. The message is written verbatim into the notification issue."""


def _identifier(airport: dict) -> str:
    """Name an airport the way the FAA's published lists do: by ICAO code, or by location id.

    Only about three quarters of the airports here hold an ICAO code, so this is what a
    failure message must quote for a pilot to recognise which airport it means.
    """
    return airport["icaoIdentifier"] or airport["faaIdentifier"]


def check(
    document: dict,
    cold_temperature_identifiers: set[str],
    previous_counts: dict[str, int] | None = None,
) -> None:
    """Run every gate over a built document, raising ``ValidationError`` on the first failure.

    ``cold_temperature_identifiers`` is every airport the CTA list named, so that one being
    dropped on the way into the document is caught rather than quietly reducing the count.
    """
    _check_minimum_counts(document)
    _check_site_numbers(document)
    _check_cold_temperature_validity(document)
    _check_cold_temperature_airports_present(document, cold_temperature_identifiers)
    _check_cold_temperature_airports_resolve(document)
    _check_reference_coverage(document)
    if previous_counts:
        _check_drift(document, previous_counts)


def _check_minimum_counts(document: dict) -> None:
    counts = document["meta"]["counts"]
    for name, floor in MINIMUM_COUNTS.items():
        if counts[name] < floor:
            raise ValidationError(f"Only {counts[name]} {name} were parsed, expected at least {floor}")


def _check_cold_temperature_validity(document: dict) -> None:
    """The CTA list has its own validity window, unrelated to the AIRAC cycle."""
    meta = document["meta"]
    listed = meta["coldTemperatureList"]
    effective_from = date.fromisoformat(listed["effectiveFrom"])
    effective_to = date.fromisoformat(listed["effectiveTo"])
    cycle_effective = date.fromisoformat(meta["cycleEffective"])
    if not effective_from <= cycle_effective <= effective_to:
        raise ValidationError(
            f"The cold temperature list is valid {effective_from} to {effective_to}, which does "
            f"not cover cycle {meta['airacCycle']} effective {cycle_effective}. "
            "A newer list has probably been published."
        )


def _check_site_numbers(document: dict) -> None:
    """Every airport must carry the site number that identifies it across a code change.

    An airport NASR did not match has none, and would be one a saved favorite could only be
    pinned to by a code the FAA may reassign — so the cycle is rejected instead.
    """
    airports = document["airports"]
    if unmatched := [_identifier(airport) for airport in airports if not airport["siteNumber"]]:
        raise ValidationError(
            f"{len(unmatched)} airport(s) matched no NASR record, so they carry no site number: "
            f"{sorted(unmatched)}"
        )

    counts = Counter(airport["siteNumber"] for airport in airports)
    if shared := sorted(number for number, count in counts.items() if count > 1):
        raise ValidationError(f"{len(shared)} site number(s) identify more than one airport: {shared}")


def _check_cold_temperature_airports_present(document: dict, expected: set[str]) -> None:
    """Every airport the CTA list names must survive into the document."""
    present = {_identifier(airport) for airport in document["airports"] if airport["coldTemperature"]}
    if missing := expected - present:
        raise ValidationError(
            f"{len(missing)} airport(s) on the cold temperature list are absent from the output: "
            f"{sorted(missing)}"
        )


def _check_cold_temperature_airports_resolve(document: dict) -> None:
    """Every CTA must be an airport in the output, or the app cannot find its elevation."""
    unresolved = [
        _identifier(airport)
        for airport in document["airports"]
        if airport["coldTemperature"] and airport["elevation"] is None
    ]
    if unresolved:
        raise ValidationError(
            f"{len(unresolved)} cold temperature airport(s) have no elevation, so no correction "
            f"can be computed for them: {sorted(unresolved)}"
        )


def _check_reference_coverage(document: dict) -> None:
    """The All Segments Method needs a FAF altitude and a missed holding altitude everywhere."""
    approaches = [approach for airport in document["airports"] for approach in airport["approaches"]]
    for segment in ("allSegments", "missed"):
        resolved = sum(
            1 for approach in approaches if approach["referenceAltitudes"][segment]["altitude"] is not None
        )
        coverage = resolved / len(approaches)
        if coverage < MINIMUM_REFERENCE_COVERAGE:
            raise ValidationError(
                f"Only {coverage:.1%} of approaches resolved a {segment!r} reference altitude "
                f"({resolved} of {len(approaches)}), below the {MINIMUM_REFERENCE_COVERAGE:.1%} floor. "
                "The CIFP record layout has probably changed."
            )


def _check_drift(document: dict, previous_counts: dict[str, int]) -> None:
    counts = document["meta"]["counts"]
    for name, previous in previous_counts.items():
        current = counts.get(name)
        if not previous or current is None:
            continue
        drift = abs(current - previous) / previous
        if drift > MAXIMUM_COUNT_DRIFT:
            raise ValidationError(
                f"{name} changed from {previous} to {current} ({drift:.0%}), exceeding the "
                f"{MAXIMUM_COUNT_DRIFT:.0%} drift limit for one cycle"
            )
