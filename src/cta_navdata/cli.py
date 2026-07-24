"""Command line entry point for the navigation data generator."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

from . import airac, cifp, cold_temp, dtpp, merge, nasr, validate
from .airac import Cycle
from .download import fetch_all
from .validate import ValidationError

DATA_FILENAME = "cta-navdata.json.gz"
MANIFEST_FILENAME = "manifest.json"


def main(argv: list[str] | None = None) -> int:
    options = _parse(argv)
    cycle = airac.resolve(options.cycle)

    if options.print_cycle:
        print(cycle.effective.isoformat())
        return 0

    try:
        document, listed = _generate(cycle, options.cache)
        validate.check(document, listed, _previous_counts(options.previous_manifest))
    except ValidationError as failure:
        print(f"Validation failed for cycle {cycle}: {failure}", file=sys.stderr)
        return 1

    _write(document, options.output)
    counts = document["meta"]["counts"]
    print(
        f"Wrote cycle {cycle} (effective {cycle.effective}) to {options.output}: "
        f"{counts['airports']} airports, {counts['approaches']} approaches, "
        f"{counts['coldTemperatureAirports']} cold temperature airports"
    )
    return 0


def _generate(cycle: Cycle, cache: Path) -> tuple[dict, set[str]]:
    """Build the document, and return alongside it every airport the CTA list named."""
    print(
        f"Fetching sources for cycle {cycle} (effective {cycle.effective})...",
        file=sys.stderr,
    )
    sources = fetch_all(cycle, cache)

    print("Parsing CIFP...", file=sys.stderr)
    data = cifp.read(sources.cifp.path)

    print("Reading NASR airports...", file=sys.stderr)
    facilities = nasr.read(sources.nasr.path)

    print("Scraping cold temperature airports...", file=sys.stderr)
    restrictions = cold_temp.scrape(sources.cold_temperature.path)

    print("Reading d-TPP chart index...", file=sys.stderr)
    charts = dtpp.read(sources.dtpp.path, cycle)
    locations = dtpp.locations(sources.dtpp.path)

    document = merge.build(cycle, data, facilities, restrictions, charts, locations, sources)
    return document, {airport.identifier for airport in restrictions.airports}


def _write(document: dict, output: Path) -> None:
    """Write the gzipped document and the manifest an app polls to detect a new cycle."""
    output.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, separators=(",", ":")).encode()

    data_path = output / DATA_FILENAME
    # mtime=0 keeps the output byte-identical across runs of the same cycle.
    data_path.write_bytes(gzip.compress(payload, mtime=0))

    meta = document["meta"]
    (output / MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "airacCycle": meta["airacCycle"],
                "cycleEffective": meta["cycleEffective"],
                "cycleExpires": meta["cycleExpires"],
                "generatedAt": meta["generatedAt"],
                "counts": meta["counts"],
                "data": {
                    "filename": DATA_FILENAME,
                    "bytes": data_path.stat().st_size,
                    "uncompressedBytes": len(payload),
                    "sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
                },
            },
            indent=2,
        )
        + "\n"
    )


def _previous_counts(manifest: Path | None) -> dict[str, int] | None:
    """The previous cycle's counts, used to catch an implausible jump in the data."""
    if manifest is None or not manifest.exists():
        return None
    return json.loads(manifest.read_text()).get("counts")


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cta-navdata",
        description="Builds the cold temperature altitude correction database from FAA source data.",
    )
    parser.add_argument(
        "--cycle",
        default="current",
        help="AIRAC cycle to build: current, next, or a date within the cycle (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.cwd(),
        help="directory to write the data files to",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(".cache"),
        help="directory to cache downloaded sources in",
    )
    parser.add_argument(
        "--previous-manifest",
        type=Path,
        help="the preceding cycle's manifest.json, used to reject an implausible change in size",
    )
    parser.add_argument(
        "--print-cycle",
        action="store_true",
        help="print the resolved cycle's effective date and exit, without downloading anything",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
