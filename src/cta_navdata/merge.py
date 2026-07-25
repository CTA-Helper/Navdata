"""Assembles the parsed sources into the published document."""

from __future__ import annotations

from datetime import UTC, datetime

from . import dtpp, segments
from .airac import Cycle
from .cifp import CIFP, Airport, Approach
from .cold_temp import ColdTemperatureAirport, ColdTemperatureList
from .download import Source, Sources
from .dtpp import Chart, Location
from .nasr import AirportRecord
from .segments import ClassifiedLeg, Reference


def build(
    cycle: Cycle,
    data: CIFP,
    facilities: dict[str, AirportRecord],
    cold_temperature: ColdTemperatureList,
    charts: dict[str, dict[str, Chart]],
    locations: dict[str, Location],
    sources: Sources,
) -> dict:
    """Build the combined document for ``cycle``."""
    restrictions = cold_temperature.by_identifier()
    # Airports with no coded procedures are kept when they are on the CTA list — several
    # military fields are, and a pilot still needs their elevation and temperature limit to
    # correct manually.
    airports = [
        _airport(
            airport,
            facilities.get(airport.faa_identifier),
            data.approaches.get(identifier, []),
            restrictions.get(identifier),
            charts.get(identifier, {}),
            locations.get(identifier),
        )
        for identifier, airport in sorted(data.airports.items())
        if identifier in data.approaches or identifier in restrictions
    ]
    return {
        "meta": _meta(cycle, cold_temperature, sources, airports),
        "airports": airports,
    }


def _meta(
    cycle: Cycle,
    cold_temperature: ColdTemperatureList,
    sources: Sources,
    airports: list[dict],
) -> dict:
    return {
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "airacCycle": cycle.identifier,
        "cycleEffective": cycle.effective.isoformat(),
        "cycleExpires": cycle.expires.isoformat(),
        "sources": {
            "cifp": _source(sources.cifp),
            "nasr": _source(sources.nasr),
            "dtpp": _source(sources.dtpp),
            "coldTemperatureAirports": _source(sources.cold_temperature),
        },
        "coldTemperatureList": {
            "effectiveFrom": cold_temperature.effective_from.isoformat(),
            "effectiveTo": cold_temperature.effective_to.isoformat(),
        },
        "counts": {
            "airports": len(airports),
            "approaches": sum(len(airport["approaches"]) for airport in airports),
            "coldTemperatureAirports": sum(1 for airport in airports if airport["coldTemperature"]),
        },
        "notes": {
            "minima": (
                "ARINC 424 publishes no DA or MDA, so the final segment reference altitude is "
                "null and must be supplied by the pilot from the approach plate."
            ),
            "correctableAltitudes": (
                "Only altitudes flagged correctable receive a cold temperature correction. "
                "SID, ODP and STAR altitudes are never corrected (AIP ENR 1.8 5.c)."
            ),
        },
    }


def _source(source: Source) -> dict:
    return {
        "url": source.url,
        "retrievedAt": source.retrieved_at.isoformat(timespec="seconds"),
    }


def _airport(
    airport: Airport,
    facility: AirportRecord | None,
    approaches: list[Approach],
    restriction: ColdTemperatureAirport | None,
    charts: dict[str, Chart],
    location: Location | None,
) -> dict:
    return {
        # The site number is the airport's identity; the codes below all name it only for as
        # long as the FAA leaves them alone. An unmatched airport carries none, which
        # ``validate`` rejects rather than publishing an airport nothing can be pinned to.
        "siteNumber": facility.site_number if facility else None,
        "faaIdentifier": airport.faa_identifier,
        # NASR publishes the ICAO code in a column of its own, so it can say an airport has
        # none. The CIFP has one identifier field holding whichever code exists, which cannot.
        "icaoIdentifier": facility.icao_identifier if facility else None,
        # CIFP names are upper-cased and truncated to 30 characters, so prefer the d-TPP's
        # fuller name when it published one.
        "name": location.name if location else airport.name,
        # NASR is the authority on where an airport is. The d-TPP files everything outside the
        # states under a placeholder state of `XX` named "PACIFIC TERRITORIES", so taking the
        # postal code from it would publish a code that is not one; NASR names Guam GU, American
        # Samoa AS and the Marianas MP. Its city is filled in for every airport here, and the
        # d-TPP's stands in should that ever stop being true.
        "city": _city(facility, location),
        "state": facility.state if facility else None,
        "stateName": facility.state_name if facility else None,
        "elevation": airport.elevation,
        "latitude": airport.latitude,
        "longitude": airport.longitude,
        "coldTemperature": _cold_temperature(restriction),
        "approaches": [
            _approach(approach, charts) for approach in sorted(approaches, key=lambda a: a.identifier)
        ],
    }


def _city(facility: AirportRecord | None, location: Location | None) -> str | None:
    """The city the airport is associated with, which the app's airport search matches on."""
    from_nasr = facility.city if facility else None
    return from_nasr or (location.city if location else None)


def _cold_temperature(restriction: ColdTemperatureAirport | None) -> dict | None:
    if restriction is None:
        return None
    return {
        "restrictionTemperatureC": restriction.restriction_temperature_c,
        "affectedSegments": [segment.lower() for segment in restriction.affected_segments],
        "listedName": restriction.name,
        "military": restriction.military,
    }


def _approach(approach: Approach, charts: dict[str, Chart]) -> dict:
    classified = segments.classify(approach)
    chart = dtpp.chart_for(approach.identifier, charts)
    return {
        "identifier": approach.identifier,
        "name": chart.name if chart else dtpp.derive_name(approach.identifier),
        "runway": approach.runway,
        "chartUrl": chart.url if chart else None,
        "referenceAltitudes": {
            str(segment): _reference(reference) for segment, reference in classified.references.items()
        },
        "fixes": [_fix(leg) for leg in classified.legs],
    }


def _reference(reference: Reference) -> dict:
    return {"altitude": reference.altitude, "source": str(reference.source)}


def _fix(classified: ClassifiedLeg) -> dict:
    leg = classified.leg
    return {
        "identifier": leg.identifier,
        "transition": leg.transition,
        "sequence": leg.sequence,
        "legType": leg.leg_type,
        "role": str(classified.role) if classified.role else None,
        "segment": str(classified.segment),
        "altitude": leg.altitude,
        "altitude2": leg.altitude2,
        "altitudeDescription": segments.altitude_description(leg.altitude_description),
        "flyover": leg.is_flyover,
        "correctable": classified.is_correctable,
    }
