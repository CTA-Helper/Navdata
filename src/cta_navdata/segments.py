"""Classifies approach legs into the four ENR 1.8 segments and finds each segment's reference altitude.

ENR 1.8 corrections are always ``table(reported_temperature, reference_altitude - airport_elevation)``,
rounded, then added to the published altitudes of the affected segment. Which altitude is the
reference depends on the segment (ENR 1.8 5.f.2.1):

    initial       the IF altitude
    intermediate  the FAF/PFAF altitude
    final         the MDA or DA — not published in ARINC 424, so the pilot must supply it
    missed        the final missed approach holding altitude

The All Segments Method (ENR 1.8 5.f.1) instead corrects everything from the FAF up to the IAF
using the FAF altitude, which is why ``all_segments`` is reported separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .cifp import Approach, Leg

# Waypoint description code column 4 (record column 43) names the fix's role in the approach.
_IAF = "A"
_INTERMEDIATE_FIX = "B"
_INITIAL_APPROACH_FIX_WITH_FACF = "D"
_FAF = "F"
_FACF = "I"
_MISSED_APPROACH_POINT = "M"

# Column 3 (record column 42) marks step-down fixes.
_STEPDOWN = "S"

_HOLDING_LEG_TYPES = ("HA", "HF", "HM")
_HOLD_TO_MANUAL_TERMINATION = "HM"

_ALTITUDE_DESCRIPTIONS = {
    " ": "at",
    "+": "atOrAbove",
    "-": "atOrBelow",
    "B": "between",
    "C": "atOrAboveSecond",
    "G": "glideslope",
    "H": "glideslope",
    "I": "glideslopeIntercept",
    "J": "glideslopeIntercept",
    "V": "stepDownVnav",
    "X": "stepDownVnav",
}


class Segment(StrEnum):
    INITIAL = "initial"
    INTERMEDIATE = "intermediate"
    FINAL = "final"
    MISSED = "missed"


class Role(StrEnum):
    IAF = "iaf"
    IAF_HOLDING = "iafHolding"
    INTERMEDIATE_FIX = "if"
    FACF = "facf"
    FAF = "faf"
    MAP = "map"
    STEPDOWN = "stepdown"
    MISSED_HOLDING = "missedHolding"


class ReferenceSource(StrEnum):
    IF = "if"
    FAF = "faf"
    MISSED_HOLDING = "missedHolding"
    PILOT_ENTERED = "pilotEntered"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ClassifiedLeg:
    """A published leg with its ENR 1.8 role and segment resolved."""

    leg: Leg
    role: Role | None
    segment: Segment

    @property
    def is_correctable(self) -> bool:
        """Whether ENR 1.8 applies a correction to this leg's published altitude.

        Two altitudes are published but never corrected. The runway threshold crossing
        altitude coded at the missed approach point is derived from the threshold crossing
        height rather than published as a procedure altitude. And in the missed approach the
        correction is added "to the final MA altitude only" (ENR 1.8 5.f.2.1.4), so the climb
        and intermediate altitudes along the missed approach are left as published.
        """
        if self.leg.altitude is None:
            return False
        if self.role is Role.MAP and _is_runway(self.leg):
            return False
        if self.segment is Segment.MISSED:
            return self.role is Role.MISSED_HOLDING
        return True


@dataclass(frozen=True)
class Reference:
    """The altitude a segment's correction is calculated from."""

    altitude: int | None
    source: ReferenceSource


@dataclass(frozen=True)
class ClassifiedApproach:
    legs: tuple[ClassifiedLeg, ...]
    references: dict[str, Reference]


def classify(approach: Approach) -> ClassifiedApproach:
    """Resolve every leg's role and segment, and compute the segment reference altitudes."""
    final_route = approach.final_route
    map_index = _index_of(final_route, _MISSED_APPROACH_POINT)
    faf_index = _index_of(final_route, _FAF)
    intermediate_fix_index = _intermediate_fix_index(final_route)

    legs = tuple(
        ClassifiedLeg(
            leg=leg,
            role=_role(leg, position, map_index, final_route),
            segment=_segment(leg, position, faf_index, map_index, intermediate_fix_index),
        )
        for position, leg in _positions(approach, final_route)
    )
    return ClassifiedApproach(
        legs=legs,
        references=_references(final_route, faf_index, intermediate_fix_index, map_index),
    )


def _positions(approach: Approach, final_route: tuple[Leg, ...]) -> list[tuple[int | None, Leg]]:
    """Pair each leg with its index along the final route, or ``None`` if it is on a transition."""
    indices = {id(leg): index for index, leg in enumerate(final_route)}
    return [(indices.get(id(leg)), leg) for leg in approach.legs]


def _segment(
    leg: Leg,
    position: int | None,
    faf_index: int | None,
    map_index: int | None,
    intermediate_fix_index: int | None,
) -> Segment:
    """Place a leg in its ENR 1.8 segment.

    Transitions run from an IAF to the IF, so they are always initial. Along the final route
    the IF itself belongs to the initial segment — ENR 1.8 5.f.2.1.2 defines the intermediate
    segment as running from the FAF "up to but not including the IF altitude".
    """
    if position is None:
        return Segment.INITIAL
    if map_index is not None and position > map_index:
        return Segment.MISSED
    if faf_index is not None and position > faf_index:
        return Segment.FINAL
    if intermediate_fix_index is not None and position <= intermediate_fix_index:
        return Segment.INITIAL
    return Segment.INTERMEDIATE


def _role(leg: Leg, position: int | None, map_index: int | None, final_route: tuple[Leg, ...]) -> Role | None:
    if position is not None and position == _missed_holding_index(final_route, map_index):
        return Role.MISSED_HOLDING
    match _fix_code(leg):
        case "A" | "D":
            return Role.IAF_HOLDING if leg.leg_type in _HOLDING_LEG_TYPES else Role.IAF
        case "B":
            return Role.INTERMEDIATE_FIX
        case "I":
            return Role.FACF
        case "F":
            return Role.FAF
        case "M":
            return Role.MAP
    return Role.STEPDOWN if leg.waypoint_description[2] == _STEPDOWN else None


def _references(
    final_route: tuple[Leg, ...],
    faf_index: int | None,
    intermediate_fix_index: int | None,
    map_index: int | None,
) -> dict[str, Reference]:
    faf = _altitude_at(final_route, faf_index)
    intermediate_fix = _altitude_at(final_route, intermediate_fix_index)
    missed_holding = _altitude_at(final_route, _missed_holding_index(final_route, map_index))
    return {
        Segment.INITIAL: _reference(intermediate_fix, ReferenceSource.IF),
        Segment.INTERMEDIATE: _reference(faf, ReferenceSource.FAF),
        # ARINC 424 publishes no minima, so the final segment reference cannot be precomputed.
        Segment.FINAL: Reference(None, ReferenceSource.PILOT_ENTERED),
        Segment.MISSED: _reference(missed_holding, ReferenceSource.MISSED_HOLDING),
        "allSegments": _reference(faf, ReferenceSource.FAF),
    }


def _reference(altitude: int | None, source: ReferenceSource) -> Reference:
    """Never guess: an absent reference altitude is reported as unavailable."""
    return (
        Reference(altitude, source) if altitude is not None else Reference(None, ReferenceSource.UNAVAILABLE)
    )


def _intermediate_fix_index(final_route: tuple[Leg, ...]) -> int | None:
    """The IF, preferring a coded intermediate fix over a final approach course fix.

    Roughly 4% of procedures — mostly RNP AR and VOR/LOC — code neither. Their initial and
    intermediate references come back unavailable rather than guessed; the All Segments
    Method still applies, since it needs only the FAF.
    """
    coded = _index_of(final_route, _INTERMEDIATE_FIX)
    return coded if coded is not None else _index_of(final_route, _FACF)


def _missed_holding_index(final_route: tuple[Leg, ...], map_index: int | None) -> int | None:
    """The final missed approach holding leg: the hold that terminates the procedure."""
    if map_index is None:
        return None
    missed = range(map_index + 1, len(final_route))
    holds = [index for index in missed if final_route[index].leg_type == _HOLD_TO_MANUAL_TERMINATION]
    if holds:
        return holds[-1]
    with_altitude = [index for index in missed if final_route[index].altitude is not None]
    return with_altitude[-1] if with_altitude else None


def _index_of(final_route: tuple[Leg, ...], fix_code: str) -> int | None:
    return next(
        (index for index, leg in enumerate(final_route) if _fix_code(leg) == fix_code),
        None,
    )


def _altitude_at(final_route: tuple[Leg, ...], index: int | None) -> int | None:
    return final_route[index].altitude if index is not None else None


def _fix_code(leg: Leg) -> str:
    return leg.waypoint_description[3]


def _is_runway(leg: Leg) -> bool:
    return bool(leg.identifier and leg.identifier.startswith("RW"))


def altitude_description(code: str) -> str:
    """Name an ARINC 424 altitude description code, e.g. ``"+"`` is ``"atOrAbove"``."""
    return _ALTITUDE_DESCRIPTIONS.get(code, "unknown")
