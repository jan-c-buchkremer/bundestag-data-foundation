# bundestag-data-foundation

Ingestion, normalisation, storage and a small query layer for German Bundestag data of
the current Wahlperiode: speeches (split per speaker, interjections separated), roll-call
votes per member, Drucksachen with authorship, and the people behind them. Every fact
carries a source pointer (`source_url`, `source_document_id`, `retrieved_at`).

It is the foundation for two downstream applications kept in separate repositories: a
fact sheet per MdB, and a topic landscape of one sitting week. This repository has no UI.

## How it works

```
bdf fetch …   raw files from bundestag.de, DIP and abgeordnetenwatch → data/raw/ (kept unchanged)
bdf ingest    parse data/raw/ → data/bundestag.sqlite (idempotent upserts, never touches the network)
bdf query …   canned queries with source pointers (text or JSON lines)
```

Python 3.12+, [uv](https://docs.astral.sh/uv/), SQLite. Runs on Windows and Linux.
Design: `docs/design.md`. Why these sources: `docs/landscape.md`. Decisions: `docs/decisions.md`.

## Setup

```sh
uv sync
uv run bdf --help
```

The DIP API needs a key: `export DIP_API_KEY=…` (Windows: `$env:DIP_API_KEY = "…"`).
Ask for a personal one with `docs/dip-api-key-request.md`. All other sources need nothing.
`BDF_DATA_DIR` moves the data directory (default `./data`).

## Acceptance test: one sitting week end to end

Sitting week 6–10 July 2026 (sittings 21/88–90, ten roll-call votes):

```sh
uv run bdf fetch stammdaten
uv run bdf fetch protocols --wp 21 --from 88 --to 90
uv run bdf fetch votes --from 2026-07-06 --to 2026-07-10
uv run bdf fetch aw --wp 21
uv run bdf fetch dip --from 2026-07-06 --to 2026-07-10        # needs DIP_API_KEY
uv run bdf ingest
```

Then, each answered from the store with a source pointer per row:

```sh
uv run bdf query speeches    --person "Nina Warken"   --from 2026-07-06 --to 2026-07-10
uv run bdf query votes       --person "Pascal Meiser" --from 2026-07-06 --to 2026-07-10
uv run bdf query drucksachen --person "Pascal Meiser" --from 2026-07-06 --to 2026-07-10
uv run bdf query corpus --from 2026-07-06 --to 2026-07-10 --json > week.jsonl
```

`--person` takes an MdB id (`11004819`) or a name. `votes` shows the member's own vote next
to their fraction's majority. `corpus` gives one clean speech per line with speaker id,
fraction, role, date and agenda item — the input for the topic landscape.

The same pipeline is exercised offline by `uv run pytest` on the fixtures in `tests/fixtures/`
(a real protocol excerpt, a real vote XLSX, a Stammdaten excerpt, abgeordnetenwatch records,
and — until a DIP key is available — synthetic DIP responses written to the OpenAPI spec).

## Data sources and licences

| Source | Used for | Terms |
|---|---|---|
| bundestag.de Open Data — Plenarprotokoll XML | sittings, agenda items, speeches, non-MdB speakers | official documents; attribution "Deutscher Bundestag", document number |
| bundestag.de Open Data — MdB Stammdaten XML | persons, mandates, fraction/committee memberships | as above |
| bundestag.de — Namentliche Abstimmungen XLSX | roll-call votes, one row per member | as above |
| DIP API | Drucksachen, authorship, Vorgänge, vote ↔ Drucksache link | free, incl. commercial; attribution "Deutscher Bundestag/Bundesrat – DIP" |
| abgeordnetenwatch.de API v2 | cross-ids (validated by name + birth year), Wikidata QIDs | CC0 1.0 |

Details and quotes in `docs/licences.md`. Code is MIT.

## Known limits

- The Stammdaten file is published irregularly; members who joined after its date (two as
  of September 2026) have no mandate row and are stored with `is_mdb = 0` from their first
  speech until the next Stammdaten file arrives. `ingest` lists names it cannot match.
- The DIP fetcher is written against the OpenAPI spec and tested on synthetic fixtures only,
  because no API key was available yet. Expect small fixes on first real run.
- abgeordnetenwatch's `ext_id_bundestagsverwaltung` is wrong for ~9 % of WP21 members and is
  therefore never trusted on its own.
- One `<rede>` with a question from another member becomes several speech rows
  (`ID…`, `ID…-2`, `ID…-3`); presidency remarks inside a speech are kept as `chair` paragraphs.
