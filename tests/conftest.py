from pathlib import Path

import pytest

from cta_navdata import cifp, cold_temp

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def data() -> cifp.CIFP:
    """A CIFP excerpt holding KMSO, PABE and PAFA."""
    return cifp.read(FIXTURES / "FAACIFP18")


@pytest.fixture(scope="session")
def cold_temperature_list() -> cold_temp.ColdTemperatureList:
    """Pages 1 and 7 of the Cold Temperature Airports PDF: the civil and military tables."""
    return cold_temp.scrape(FIXTURES / "Cold_Temp_Airports.pdf")


def approach(data: cifp.CIFP, airport: str, identifier: str) -> cifp.Approach:
    return next(a for a in data.approaches[airport] if a.identifier == identifier)
