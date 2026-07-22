"""Scraping the Cold Temperature Airports PDF, and the gates that guard against a layout change."""

from datetime import date

import pdfplumber
import pytest

from cta_navdata import cold_temp
from cta_navdata.validate import ValidationError

from .conftest import FIXTURES


class TestScrape:
    def test_reads_the_published_validity_window(self, cold_temperature_list):
        assert cold_temperature_list.effective_from == date(2025, 10, 2)
        assert cold_temperature_list.effective_to == date(2026, 9, 3)
        assert cold_temperature_list.covers(date(2026, 7, 9))

    def test_reads_the_affected_segments_of_a_row(self, cold_temperature_list):
        juneau = cold_temperature_list.by_identifier()["PAJN"]

        assert juneau.affected_segments == ("Intermediate", "Final")

    def test_reads_a_temperature_above_freezing(self, cold_temperature_list):
        """Most limits are negative, so an unsigned one must not be mis-parsed."""
        assert cold_temperature_list.by_identifier()["PAJN"].restriction_temperature_c == 1

    def test_joins_an_airport_name_wrapped_across_lines(self, cold_temperature_list):
        anchorage = cold_temperature_list.by_identifier()["PANC"]

        assert anchorage.name == "Ted Stevens Anchorage Intl"
        assert anchorage.affected_segments == ("Initial",)

    def test_flags_the_military_table_on_the_last_page(self, cold_temperature_list):
        by_identifier = cold_temperature_list.by_identifier()

        assert by_identifier["PAEI"].military
        assert not by_identifier["PABT"].military

    def test_skips_the_state_group_headings(self, cold_temperature_list):
        identifiers = {airport.identifier for airport in cold_temperature_list.airports}

        assert "Alaska" not in identifiers


class TestLayoutGates:
    """The scrape must fail rather than silently attribute X marks to the wrong segment."""

    def test_a_mark_between_columns_is_rejected(self):
        columns = dict(zip(cold_temp.SEGMENT_HEADINGS, (373.9, 428.0, 479.8, 522.4), strict=True))

        with pytest.raises(ValidationError, match="matches 0 of the segment columns"):
            cold_temp._column_at(400.0, columns, _page(), {"top": 100.0})

    def test_a_mark_matching_two_columns_is_rejected(self):
        """Columns closer together than the tolerance can no longer be told apart."""
        columns = dict(zip(cold_temp.SEGMENT_HEADINGS, (373.9, 380.0, 479.8, 522.4), strict=True))

        with pytest.raises(ValidationError, match="matches 2 of the segment columns"):
            cold_temp._column_at(377.0, columns, _page(), {"top": 100.0})

    def test_cells_disagreeing_with_the_marks_are_rejected(self):
        """Simulates extraction losing a segment that the page visibly marks."""
        with pdfplumber.open(FIXTURES / "Cold_Temp_Airports.pdf") as pdf:
            page = pdf.pages[0]
            table = page.find_tables()[0]
            columns = cold_temp._segment_columns(page, table)
            understated = [
                airport
                for airport in cold_temp._read_table(page, table, None)
                if airport.identifier != "PAJN"
            ]

            with pytest.raises(ValidationError, match="X marks disagree with the extracted table"):
                cold_temp._reject_mismatched_marks(page, table, columns, understated)


def _page():
    """A stand-in page, used only for the page number in the error message."""
    return type("Page", (), {"page_number": 1})()
