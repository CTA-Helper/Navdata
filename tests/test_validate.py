"""The publication gates. A gate that never fires is worse than no gate at all."""

import pytest

from cta_navdata import validate
from cta_navdata.validate import ValidationError

# Distinguishes "the test said nothing about this field" from an explicit ``None``.
_DERIVED = object()

# Site numbers have to be well formed to reach the gates past the one checking their shape, so
# each airport an identifier names is assigned one in the order it first appears.
_SITE_NUMBERS: dict[str, str] = {}


def _site_number_for(identifier):
    return _SITE_NUMBERS.setdefault(identifier, f"{len(_SITE_NUMBERS) + 1:05d}.A")


def document(*, airports=None, cold_temperature_valid_to="2026-09-03", **counts):
    airports = airports if airports is not None else [_airport("KMSO", cold_temperature=True)]
    return {
        "meta": {
            "airacCycle": "2607",
            "cycleEffective": "2026-07-09",
            "cycleExpires": "2026-08-06",
            "coldTemperatureList": {
                "effectiveFrom": "2025-10-02",
                "effectiveTo": cold_temperature_valid_to,
            },
            "counts": {
                "airports": 3026,
                "approaches": 10243,
                "coldTemperatureAirports": 195,
            }
            | counts,
        },
        "airports": airports,
    }


def _airport(
    identifier,
    *,
    site_number=_DERIVED,
    icao=_DERIVED,
    faa_identifier=_DERIVED,
    cold_temperature=False,
    elevation=3206,
    missed=12000,
    faf=6200,
):
    """Build one airport record, named the way the FAA's lists name it.

    Each identifier gets a site number of its own, standing in for whatever the FAA actually
    numbered the airport: the gates care only that every airport has a well formed one and
    that no two share it.
    """
    return {
        "siteNumber": (_site_number_for(identifier) if site_number is _DERIVED else site_number),
        "faaIdentifier": (identifier.removeprefix("K") if faa_identifier is _DERIVED else faa_identifier),
        "icaoIdentifier": identifier if icao is _DERIVED else icao,
        "elevation": elevation,
        "coldTemperature": {"restrictionTemperatureC": -11} if cold_temperature else None,
        "approaches": [
            {
                "referenceAltitudes": {
                    "allSegments": {"altitude": faf},
                    "missed": {"altitude": missed},
                }
            }
        ],
    }


def test_a_valid_document_passes():
    validate.check(document(), {"KMSO"})


def test_an_airport_matching_no_nasr_record_is_rejected():
    """Without a site number a favorite could only be pinned to a code the FAA may reassign."""
    airports = [_airport("KMSO", site_number=None, cold_temperature=True)]

    with pytest.raises(ValidationError, match=r"no site number: \['KMSO'\]"):
        validate.check(document(airports=airports), {"KMSO"})


def test_a_site_number_shared_by_two_airports_is_rejected():
    airports = [_airport("KMSO", site_number="12453.A"), _airport("KSFO", site_number="12453.A")]

    with pytest.raises(ValidationError, match=r"site number\(s\) identify more than one airport"):
        validate.check(document(airports=airports), set())


def test_a_site_number_that_is_not_shaped_like_one_is_rejected():
    """The app pins favorites to this string, so its shape is part of the primary key."""
    airports = [_airport("KMSO", site_number="03555.*A", cold_temperature=True)]

    with pytest.raises(ValidationError, match=r"not of the form 03555.A: \['03555\.\*A'\]"):
        validate.check(document(airports=airports), {"KMSO"})


def test_a_site_number_carrying_an_inserted_sequence_is_accepted():
    """A third of the airports are numbered fractionally, so the sequence is part of the form."""
    airports = [_airport("KMSO", site_number="00218.12A", cold_temperature=True)]

    validate.check(document(airports=airports), {"KMSO"})


def test_an_icao_code_shared_by_two_airports_is_rejected():
    """The app keys on the codes too, and resolves a duplicate by merging the two airports."""
    airports = [_airport("KMSO"), _airport("KMSO", site_number="99999.A", faa_identifier="XXX")]

    with pytest.raises(ValidationError, match=r"ICAO code\(s\) identify more than one airport"):
        validate.check(document(airports=airports), set())


def test_a_shared_faa_identifier_is_rejected_even_when_the_icao_codes_differ():
    airports = [_airport("KMSO"), _airport("KSFO", site_number="99999.A", faa_identifier="MSO")]

    with pytest.raises(ValidationError, match=r"FAA identifier\(s\) identify more than one airport"):
        validate.check(document(airports=airports), set())


def test_airports_without_icao_codes_are_not_duplicates_of_each_other():
    """A quarter of a cycle publishes no ICAO code; absence is not a collision."""
    airports = [_airport("05U", icao=None), _airport("0A9", icao=None)]

    validate.check(document(airports=airports), set())


def test_an_airport_without_an_icao_code_is_named_by_its_location_identifier():
    """A quarter of the database has no ICAO code, so a failure must still name the airport."""
    airports = [_airport("05U", icao=None, site_number=None)]

    with pytest.raises(ValidationError, match=r"\['05U'\]"):
        validate.check(document(airports=airports), set())


def test_an_expired_cold_temperature_list_is_rejected():
    """The list has its own validity window; a stale one silently mis-states every limit."""
    with pytest.raises(ValidationError, match="does not cover cycle"):
        validate.check(document(cold_temperature_valid_to="2026-06-30"), {"KMSO"})


def test_a_dropped_cold_temperature_airport_is_rejected():
    with pytest.raises(ValidationError, match=r"absent from the output: \['PAEI'\]"):
        validate.check(document(), {"KMSO", "PAEI"})


def test_a_cold_temperature_airport_without_an_elevation_is_rejected():
    """Without an elevation there is no height above airport, so no correction is possible."""
    airports = [_airport("KMSO", cold_temperature=True, elevation=None)]

    with pytest.raises(ValidationError, match="no elevation"):
        validate.check(document(airports=airports), {"KMSO"})


def test_missing_reference_altitudes_are_rejected():
    """Every approach yields a FAF and a missed holding altitude; a shortfall means a layout change."""
    airports = [_airport(f"K{n:03d}", missed=None) for n in range(10)]

    with pytest.raises(ValidationError, match="'missed' reference altitude"):
        validate.check(document(airports=airports), set())


def test_an_implausible_change_in_size_is_rejected():
    with pytest.raises(ValidationError, match="exceeding the 20% drift limit"):
        validate.check(document(), {"KMSO"}, previous_counts={"approaches": 4000})


def test_a_truncated_source_is_rejected():
    with pytest.raises(ValidationError, match="expected at least"):
        validate.check(document(approaches=12), {"KMSO"})
