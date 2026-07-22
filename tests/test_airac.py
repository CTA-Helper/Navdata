"""AIRAC cycle arithmetic, checked against cycles the FAA still had online."""

from datetime import date

import pytest

from cta_navdata import airac


@pytest.mark.parametrize(
    ("effective", "identifier"),
    [
        (date(2025, 12, 25), "2513"),  # the last cycle of 2025
        (date(2026, 1, 22), "2601"),  # the first of 2026, 28 days later
        (date(2026, 7, 9), "2607"),
        (date(2026, 8, 6), "2608"),
    ],
)
def test_cycle_identifier_restarts_each_calendar_year(effective, identifier):
    assert airac.Cycle(effective).identifier == identifier


def test_cycle_runs_for_28_days():
    cycle = airac.Cycle(date(2026, 7, 9))

    assert cycle.expires == date(2026, 8, 6)
    assert cycle.covers(date(2026, 8, 5))
    assert not cycle.covers(cycle.expires)


def test_a_date_resolves_to_the_cycle_containing_it():
    assert airac.resolve("2026-07-22").effective == date(2026, 7, 9)


def test_current_and_next_are_resolved_relative_to_today():
    today = date(2026, 7, 22)

    assert airac.resolve("current", today).effective == date(2026, 7, 9)
    assert airac.resolve("next", today).effective == date(2026, 8, 6)


def test_source_urls_are_built_from_the_cycle():
    cycle = airac.Cycle(date(2026, 7, 9))

    assert cycle.cifp_url.endswith("/CIFP_260709.zip")
    assert cycle.dtpp_metafile_url.endswith("/d-tpp/2607/xml_data/d-TPP_Metafile.xml")
    assert cycle.chart_url("00266RY12.PDF").endswith("/d-tpp/2607/00266RY12.PDF")
