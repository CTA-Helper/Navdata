"""Segment classification, checked against the worked example published in AIP ENR 1.8 6.a.

That example corrects Missoula's RNAV (GPS) Y RWY 12, and names the altitude of every fix it
touches, so it doubles as ground truth for the whole classifier.
"""

from cta_navdata import segments
from cta_navdata.segments import ReferenceSource, Role, Segment

from .conftest import approach


def altitudes(classified, segment: Segment) -> dict[str, int]:
    """Correctable altitudes in a segment, keyed by fix."""
    return {
        leg.leg.identifier: leg.leg.altitude
        for leg in classified.legs
        if leg.segment is segment and leg.is_correctable
    }


class TestMissoulaRnavY12:
    """AIP ENR 1.8 6.a: KMSO RNAV (GPS) Y RWY 12, airport elevation 3206 ft."""

    def test_airport_elevation_is_the_correction_datum(self, data):
        assert data.airports["KMSO"].elevation == 3206

    def test_reference_altitudes_match_the_published_example(self, data):
        references = segments.classify(approach(data, "KMSO", "R12-Y")).references

        # The example enters the table with the FAF altitude for everything outside the FAF.
        assert references["allSegments"].altitude == 6200
        assert references[Segment.INTERMEDIATE].altitude == 6200
        assert references[Segment.INITIAL].altitude == 9400
        assert references[Segment.MISSED].altitude == 12000

    def test_final_segment_reference_must_come_from_the_pilot(self, data):
        """ARINC 424 publishes no minima, so the LNAV MDA of 4520 ft cannot be precomputed."""
        final = segments.classify(approach(data, "KMSO", "R12-Y")).references[Segment.FINAL]

        assert final.altitude is None
        assert final.source is ReferenceSource.PILOT_ENTERED

    def test_initial_segment_holds_the_iaf_and_if_altitudes(self, data):
        classified = segments.classify(approach(data, "KMSO", "R12-Y"))

        # LANNY, CHARL and ODIRE are the IAFs the example corrects 9400 -> 9700.
        assert altitudes(classified, Segment.INITIAL) == {
            "ODIRE": 9400,
            "EHRAY": 11800,
            "RIVAL": 9500,
            "LANNY": 9400,
        }

    def test_intermediate_segment_runs_from_the_faf_up_to_but_not_including_the_if(self, data):
        classified = segments.classify(approach(data, "KMSO", "R12-Y"))

        # CALIP 7000 -> 7300 and SUPPY 6200 -> 6500 in the example; ODIRE is the IF and
        # belongs to the initial segment.
        assert altitudes(classified, Segment.INTERMEDIATE) == {
            "CALIP": 7000,
            "SUPPY": 6200,
        }

    def test_final_segment_holds_the_step_down_inside_the_faf(self, data):
        classified = segments.classify(approach(data, "KMSO", "R12-Y"))

        # BEGPE 4840 -> 4990 in the example. The runway threshold crossing altitude coded at
        # the missed approach point is not a published altitude and is not corrected.
        assert altitudes(classified, Segment.FINAL) == {"BEGPE": 4840}

    def test_missed_approach_holding_altitude_is_identified(self, data):
        classified = segments.classify(approach(data, "KMSO", "R12-Y"))
        holding = [leg for leg in classified.legs if leg.role is Role.MISSED_HOLDING]

        # JENKI 12000 -> 12500 in the example.
        assert [(leg.leg.identifier, leg.leg.altitude) for leg in holding] == [("JENKI", 12000)]

    def test_roles_are_resolved_from_the_waypoint_description_code(self, data):
        roles = {
            (leg.leg.transition, leg.leg.identifier): leg.role
            for leg in segments.classify(approach(data, "KMSO", "R12-Y")).legs
        }

        assert roles[("JENKI", "LANNY")] is Role.IAF
        assert roles[("CHARL", "ODIRE")] is Role.IAF_HOLDING
        assert roles[("JENKI", "ODIRE")] is Role.INTERMEDIATE_FIX
        assert roles[(None, "ODIRE")] is Role.FACF
        assert roles[(None, "SUPPY")] is Role.FAF
        assert roles[(None, "BEGPE")] is Role.STEPDOWN
        assert roles[(None, "RW12")] is Role.MAP


class TestProceduresWithoutAnIntermediateFix:
    """Around 4% of procedures code no IF. Their references are absent, never guessed."""

    def test_missing_references_are_reported_unavailable(self, data):
        references = segments.classify(approach(data, "PAFA", "H02LZ")).references

        assert references[Segment.INITIAL].altitude is None
        assert references[Segment.INITIAL].source is ReferenceSource.UNAVAILABLE

    def test_the_all_segments_method_still_applies(self, data):
        """The All Segments Method needs only the FAF, so it survives a missing IF."""
        references = segments.classify(approach(data, "PAFA", "H02LZ")).references

        assert references["allSegments"].altitude is not None
        assert references[Segment.MISSED].altitude is not None


class TestPrimaryRecordSelection:
    """A procedure carrying a Level of Service continuation numbers its primary record 1.

    Treating only 0 as primary drops those records, and with them most FAFs in the database.
    """

    def test_a_faf_on_a_continued_record_is_read(self, data):
        classified = segments.classify(approach(data, "PABE", "R01L"))
        faf = [leg for leg in classified.legs if leg.role is Role.FAF]

        assert [(leg.leg.identifier, leg.leg.altitude) for leg in faf] == [("NAPAC", 1800)]
