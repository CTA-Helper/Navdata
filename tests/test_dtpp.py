"""The d-TPP metafile reader. Only the location hierarchy walk is exercised here; the chart
matching is pinned end to end by ``test_segments.py`` through the merged document."""

from cta_navdata import dtpp

_METAFILE = """<?xml version="1.0" encoding="utf-8"?>
<digital_tpp cycle="2607">
  <state_code ID="MT" state_fullname="MONTANA">
    <city_name ID="MISSOULA">
      <airport_name ID="MISSOULA MONTANA" apt_ident="MSO" icao_ident="KMSO"/>
    </city_name>
  </state_code>
  <state_code ID="AL" state_fullname="ALABAMA">
    <city_name ID="ANDALUSIA">
      <airport_name ID="SOUTH ALABAMA RGNL AT BILL BENTON FLD" apt_ident="79J"/>
    </city_name>
  </state_code>
</digital_tpp>
"""


def test_locations_reads_the_state_city_airport_hierarchy(tmp_path):
    path = tmp_path / "d-TPP_Metafile.xml"
    path.write_text(_METAFILE)

    locations = dtpp.locations(path)

    assert locations["KMSO"] == dtpp.Location(
        name="MISSOULA MONTANA", city="MISSOULA", state="MT", state_name="MONTANA"
    )


def test_locations_falls_back_to_the_faa_identifier_when_there_is_no_icao_code(tmp_path):
    path = tmp_path / "d-TPP_Metafile.xml"
    path.write_text(_METAFILE)

    locations = dtpp.locations(path)

    assert "79J" in locations
    assert locations["79J"].name == "SOUTH ALABAMA RGNL AT BILL BENTON FLD"
