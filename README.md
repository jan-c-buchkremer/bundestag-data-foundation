# bundestag-data-foundation

Ingestion, normalisation, storage and a small query layer for German Bundestag data of
the current Wahlperiode: speeches (split per speaker, interjections separated), roll-call
votes per member, Drucksachen with authorship, and the people behind them. Every fact
carries a source pointer (`source_url`, `source_document_id`, `retrieved_at`).

It is the foundation for two downstream applications kept in separate repositories: a
fact sheet per MdB, and a topic landscape of one sitting week. This repository has no UI.

## How it works

```
bdf fetch …   raw files from bundestag.de, DIP, abgeordnetenwatch, Bundeswahlleiterin, Wikidata → data/raw/ (kept unchanged)
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

## Unattended updates

```sh
uv run bdf update          # fetch everything new since the last run from all sources, then ingest
```

Each source picks its own window from what is already in `data/raw/`: protocols from the next sitting
number (probing until three in a row are not published) plus every protocol still in its preliminary version
and its PDF (until the final XML is served), votes and DIP from the latest date on disk minus
14 days (late publications), Stammdaten, abgeordnetenwatch, the bundestag.de biography list and the Wikidata government
roster in full (portrait images only when new). The first run backfills the whole
Wahlperiode. Without `DIP_API_KEY` DIP is skipped; network errors and 429/5xx are retried
with backoff; a source that still fails (or a DIP IP block) is skipped for that run, the other sources are fetched,
everything on disk is ingested, and the command exits 1.

## Container

CI tests every push; pushes to `main` and `deploy` also publish `ghcr.io/jan-c-buchkremer/bundestag-data-foundation`
(tags: branch name, short sha). The data directory is `/data`, the entrypoint is `bdf`:

```sh
docker run --rm -v "$PWD/data:/data" -e DIP_API_KEY ghcr.io/jan-c-buchkremer/bundestag-data-foundation:main update
```

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
uv run bdf query decisions --sitting 21/90
```

Portraits and the government roster (current state, no date range):

```sh
uv run bdf fetch photos          # bundestag.de biography list (all pages) + portraits → data/raw/bundestag/{biografien,fotos}
uv run bdf fetch government      # Wikidata roster + Commons portraits → data/raw/wikidata
uv run bdf fetch hib --wp 21     # heute im bundestag: every article page of the Wahlperiode → data/raw/bundestag/hib (~2 h at first)
uv run bdf ingest
uv run bdf query government --date 2026-09-28   # roles held that day, with person id and source (wikidata | stammdaten | protocol)
uv run bdf query stale-roles                    # protocol-only roles not printed in a protocol for >90 days (kept current)
uv run bdf query photos --missing               # sitting members without a portrait
```

`--person` takes an MdB id (`11004819`) or a name. `votes` shows the member's own vote next
to their fraction's majority. `corpus` gives one clean speech per line with speaker id,
fraction, role, date and agenda item — the input for the topic landscape. `decisions` lists every
decision on substance the chair announced in a sitting — show of hands with each fraction's position,
roll-call votes with their totals — with its agenda item, Drucksache and the protocol as source.

Agenda items that are blocks of many items voted one by one (e.g. 21/96/6, TOP 41b–41s) also have
`agenda_sub_item` rows (one per called-up item, with its own title and Drucksachen); `decision.sub_item_id`
and `speech.sub_item_id` say which. `agenda_item_vorlage` lists every Drucksache of an item or sub-item with its Vorgang, and
`no_debate` on items and sub-items is set where the chair says no Aussprache is provided.

The same pipeline is exercised offline by `uv run pytest` on the fixtures in `tests/fixtures/`
(a real protocol excerpt, a real vote XLSX, a Stammdaten excerpt, abgeordnetenwatch records,
and recorded DIP responses for four Drucksachen of that week).

## Data sources and licences

| Source | Used for | Terms |
|---|---|---|
| bundestag.de Open Data — Plenarprotokoll XML | sittings, agenda items, speeches, non-MdB speakers | official documents; attribution "Deutscher Bundestag", document number |
| bundestag.de Open Data — MdB Stammdaten XML | persons, mandates, fraction/committee memberships | as above |
| bundestag.de — Namentliche Abstimmungen XLSX | roll-call votes, one row per member | as above |
| DIP API | Drucksachen, authorship, Vorgänge, vote ↔ Drucksache link | free, incl. commercial; attribution "Deutscher Bundestag/Bundesrat – DIP" |
| abgeordnetenwatch.de API v2 | cross-ids (validated by name + birth year), Wikidata QIDs, side jobs (Nebentätigkeiten) | CC0 1.0 |
| bundestag.de — MdB biographies (card list behind /abgeordnete) | portrait per MdB with photographer credit | Bundestag terms; photos: third-party rights, credit shown |
| Wikidata (SPARQL) | government roster since 2025-05-06: offices, dates, departments (with Stammdaten and protocol roles) | CC0 1.0 |
| Wikimedia Commons | portraits of government members without a bundestag.de card | per file (mostly CC BY-SA), author + licence stored |

Details and quotes in `docs/licences.md`. Code is MIT.

`bdf export <dir>` writes the store as open data: one gzipped CSV per table, a Frictionless `datapackage.json`
(types, descriptions, keys, sources and licences per table) and a README with the required attributions
(`docs/design.md`, "Open-data export").

## Known limits

- The government roster merges Wikidata, the Stammdaten and the roles printed in the protocols; `source_kind`
  says which one a row's dates come from. Protocol rows (e.g. ministers of the September 2026 reshuffle that neither
  Wikidata nor the Stammdaten have yet) carry evidence dates: the first and last sitting the role is printed, not
  the appointment; show them as "belegt ab … (Plenarprotokoll)". An open minister role is closed the day before the
  protocols first show a successor (an inference, printed by `ingest`). Non-MdB Staatsminister and Parlamentarische
  Staatssekretäre who never speak are missing.
- The Stammdaten file is published irregularly; members who joined after its date (two as
  of September 2026) have no mandate row and are stored with `is_mdb = 0` from their first
  speech until the next Stammdaten file arrives. `ingest` lists names it cannot match.
- `search.dip.bundestag.de` sits behind bot protection (Enodia) that blocks the IP for
  ~15 minutes after a request burst; `fetch dip` therefore paces itself to 2 requests/s
  (a sitting week takes ~3 minutes) and, if blocked, stops with a message. Rerunning
  resumes from `data/raw`.
- Per-Drucksache DIP files (authors, Vorgänge) are fetched once and not refreshed, so a Vorgang's
  `beratungsstand` stays as it was at the first fetch. `fetch dip --force` over a range refreshes them.
- abgeordnetenwatch's `ext_id_bundestagsverwaltung` is wrong for ~9 % of WP21 members and is
  therefore never trusted on its own.
- bundestag.de serves a preliminary protocol XML before the final one, sometimes for months; it can end hours
  before the sitting did. Such sittings have `sitting.preliminary = 1`; `update` fetches them again until they are
  final, and meanwhile reads what the XML lacks from the final PDF: agenda items, speeches and the chair's words
  after `sitting.last_page` have the PDF as their `source_url`. Text read from a PDF can carry layout slips the XML
  would not have. `bdf query protocol-gaps` lists the preliminary sittings, with every Beratung DIP places in a
  sitting that no agenda item there carries.
- Decisions are read from the chair's words by rules (`docs/decisions.md` has the measured recall).
  Show-of-hands results are per fraction as announced; where the chair names no fraction ("Wer stimmt
  dafür? – Wer stimmt dagegen? – … angenommen") the decision has no fraction rows. Subjects are the
  chair's phrase ("Beschlussempfehlung des Ausschusses …"), sometimes only a noun.
- One `<rede>` with a question from another member becomes several speech rows
  (`ID…`, `ID…-2`, `ID…-3`); presidency remarks inside a speech are kept as `chair` paragraphs.
