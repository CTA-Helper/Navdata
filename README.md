# CTA NavData

Builds the navigation database behind [CTA Helper](../CTA%20Helper), an app that
applies the cold temperature altitude corrections described in
[AIP ENR 1.8](https://www.faa.gov/air_traffic/publications/atpubs/aip_html/part2_enr_section_1.8.html).

For a given AIRAC cycle the generator downloads three FAA sources, merges them,
and writes a single JSON document that is published as a GitHub Release asset.

| Source | What it provides |
| --- | --- |
| [CIFP](https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/cifp/) | Airport elevations, and every approach procedure's fixes and published altitudes (ARINC 424-18) |
| [Cold Temperature Airports](https://aeronav.faa.gov/d-tpp/Cold_Temp_Airports.pdf) | The restriction temperature and affected segments for each CTA |
| [d-TPP metafile](https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/dtpp/) | Official approach names and approach plate URLs |

## Usage

```console
cta-navdata --cycle current --output ./out
```

| Option | Description | Default |
| --- | --- | --- |
| `--cycle` | `current`, `next`, or a date in the cycle | `current` |
| `--output` | Where to write the data files | current directory |
| `--cache` | Where to cache the downloaded sources | `.cache` |
| `--previous-manifest` | Last cycle's manifest, for the drift check | — |
| `--print-cycle` | Print the effective date and exit, fetching nothing | off |

`--print-cycle` lets CI work out the target cycle before deciding whether to run
the full pipeline.

## What the data supports

ENR 1.8 corrections all take the same form: look up the reported temperature
against the height above the airport, round the result, and add it to the
published altitudes of the affected segment. Every input to that except the
temperature is in this file.

Each approach carries a `referenceAltitudes` block giving the altitude each
method enters the ICAO table with:

```jsonc
"referenceAltitudes": {
  "allSegments":  { "altitude": 6200,  "source": "faf" },
  "initial":      { "altitude": 9400,  "source": "if" },
  "intermediate": { "altitude": 6200,  "source": "faf" },
  "final":        { "altitude": null,  "source": "pilotEntered" },
  "missed":       { "altitude": 12000, "source": "missedHolding" }
}
```

Each fix is then tagged with the `segment` it falls in and whether it is
`correctable`, so an app can apply a segment's correction to exactly the
altitudes ENR 1.8 says it applies to.

The ICAO Cold Temperature Error Table itself is deliberately **not** included —
this file holds only FAA-sourced data, and the app owns the table.

### Approach minima are not here

ARINC 424 does not publish DA or MDA, and no machine-readable FAA source does
either. The final segment reference is therefore always `null` with a source of
`pilotEntered`; the pilot must read the minima off the plate, which is what
`chartUrl` links to.

### Where a reference is unavailable

About 4% of procedures — mostly RNAV (RNP) and VOR — code no intermediate fix,
so their initial and intermediate references come back `null` with a source of
`unavailable` rather than a guess. The All Segments Method needs only the FAF
altitude, so it still applies.

### What is not corrected

SID, ODP and STAR altitudes are never corrected (ENR 1.8 5.c), so departures and
arrivals are not in this file. Neither is the runway threshold crossing altitude
coded at the missed approach point, which is not a published procedure altitude.

## Distribution

Each cycle is published as a release tagged with its effective date, with two
stable filenames so an app can always fetch the newest:

- `cta-navdata.json.gz` — the data, roughly 30 MB expanding from a 1.3 MB
  download
- `manifest.json` — cycle dates, counts and a SHA-256, cheap to poll for a new
  cycle

```console
curl -L -o cta-navdata.json.gz \
  https://github.com/CTA-Helper/Navdata/releases/latest/download/cta-navdata.json.gz
```

## Validation

Scraping a PDF fails silently: if the table shifts, extraction still returns
rows, just with the X marks against the wrong segments. So every row is read
twice — once from the extracted cells and once from the geometry of the X glyphs
— and the two must agree exactly. Further gates check that the list's validity
window covers the cycle, that every listed airport survives into the output with
an elevation, that every approach resolves a FAF and a missed holding altitude,
and that the database has not changed size implausibly since last cycle.

If any gate fails the run publishes nothing and opens an issue with the
diagnostics attached. Consumers stay on the last good cycle rather than
receiving something subtly wrong.

## Development

The project uses pyenv and ruff.

```console
pyenv virtualenv 3.13 cta-navdata && pyenv local cta-navdata
pip install -e . ruff pytest
ruff check --fix && ruff format && pytest
```

The tests run against checked-in excerpts of the real sources.
`test_segments.py` asserts the worked example published in ENR 1.8 6.a —
Missoula's RNAV (GPS) Y RWY 12 — which names the altitude of every fix it
corrects and so pins the classifier end to end.
