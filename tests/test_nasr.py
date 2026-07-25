"""Reading the NASR airport file, the source of the site number every airport is keyed on."""

from datetime import date

from cta_navdata import airac


class TestRead:
    def test_keys_records_by_location_identifier(self, facilities):
        assert set(facilities) == {"MSO", "05U", "BET", "FAI"}

    def test_joins_the_site_number_to_its_facility_type(self, facilities):
        assert facilities["MSO"].site_number == "12453.A"

    def test_reads_a_site_number_inserted_between_two_others(self, facilities):
        """The FAA inserts fractionally rather than renumbering, so the key is not an integer."""
        assert facilities["BET"].site_number == "50061.1A"

    def test_drops_the_asterisk_form_5010_separates_the_type_code_with(self, facilities):
        """An asterisk left in would rekey the airport, and the app cannot undo a rekeyed favorite."""
        assert facilities["FAI"].site_number == "50219.A"

    def test_reads_the_icao_code_when_one_is_published(self, facilities):
        assert facilities["MSO"].icao_identifier == "KMSO"

    def test_reads_no_icao_code_for_an_airport_that_has_none(self, facilities):
        """A quarter of the airports the CIFP codes hold no ICAO code, only a location id."""
        assert facilities["05U"].icao_identifier is None

    def test_reads_the_associated_city_and_state(self, facilities):
        eureka = facilities["05U"]

        assert (eureka.city, eureka.state, eureka.state_name) == ("EUREKA", "NV", "NEVADA")


def test_subscription_url_names_the_cycle_by_day_month_and_year():
    """The NASR subscription is published under a date form none of the other sources use."""
    assert airac.Cycle(date(2026, 7, 9)).nasr_url.endswith("/09_Jul_2026_CSV.zip")
