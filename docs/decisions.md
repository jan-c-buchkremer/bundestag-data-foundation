# Decisions

One line per decision, newest last. Date, decision, reason.

- 2026-09-21 — Ingest from primary sources (bundestag.de XML/XLSX, DIP API, abgeordnetenwatch), not from an existing corpus. All corpora stop in 2023 or are PDF-derived; the XML already carries speaker ids and separated interjections. (`landscape.md` §5)
- 2026-09-21 — The Bundestag MdB ID (`MDB/ID` = `redner/@id`) is the person primary key; DIP and abgeordnetenwatch ids are attributes. It is the only id that is present in both the protocol and the master data.
- 2026-09-21 — abgeordnetenwatch `ext_id_bundestagsverwaltung` is stored only when it agrees with a name+birth-year match. Verified wrong for 56/630 WP21 members.
- 2026-09-21 — Roll-call votes come from the bundestag.de XLSX, not from abgeordnetenwatch. Official, same-day, exact sitting/vote number; abgeordnetenwatch lags and has no structured Drucksache link.
- 2026-09-21 — Drucksache metadata and authorship come from DIP; the full author list via `/aktivitaet?f.drucksache=`, because `autoren_anzeige` is capped at 4. bundestag.de Open Data has PDFs only for WP21 Drucksachen.
- 2026-09-21 — Non-MdB speakers (ministers without mandate, Bundesrat, guests) get `person` rows with `is_mdb = 0`. They have ids in the XML anyway and are interesting for the fact sheet.
- 2026-09-21 — SQLite, single file, stdlib `sqlite3`; raw files kept unchanged under `data/raw/`. Home server, weekly batch, readers only need `sqlite3`. DuckDB not needed for join-heavy small-table queries.
- 2026-09-21 — No `fraction` table; fraction is a normalised string with one shared normalisation function. A table would add a join and nothing else.
- 2026-09-21 — `speech_paragraph` and `individual_vote` inherit provenance from their parent row (same raw file); every other table carries `source_url`, `source_document_id`, `retrieved_at`. Avoids repeating three columns 630× per vote.
- 2026-09-21 — One `rede` element with several speakers is split into several `speech` rows (`<rede id>-2`, …); presidency remarks inside a `rede` become `chair` paragraphs, not speeches. Keeps the clean-text unit one speaker, one voice.
- 2026-09-21 — Personal DIP API key requested by e-mail; the public key rotates and both known ones are expired. Until it arrives, DIP code is written against recorded fixtures only.
- 2026-09-21 — Name matching looks at Wahlperiode mandate holders first and at all known persons second. Nachrücker who joined after the Stammdaten file date have no mandate row yet but may already exist as persons.
- 2026-09-21 — Raw files get a `.meta.json` sidecar (url, retrieved_at); `ingest` reads provenance from there and never from the network. Keeps the store rebuildable offline.
- 2026-09-21 — DIP author activities are stored with their `activity_type`; the co-authored query does not filter on it yet. Which activity types `f.drucksache` returns is unverified until a key is available.
- 2026-09-21 — Ruff line length 120 rather than 100. SQL and f-strings otherwise wrap into unreadable fragments.
- 2026-09-21 — abgeordnetenwatch is fetched per Wahlperiode via a fixed `PERIOD_BY_WAHLPERIODE` map ({21: 161}); raw files are named `wp21-*.json`. One explicit mapping beats deriving the WP from mandate labels.
- 2026-09-21 — DIP calls stay sequential and `ingest` re-parses every raw file each run. Parallel fetching and change detection are cheap to add later; neither can be tuned without a key or a full Wahlperiode of data.
- 2026-09-21 — The protocol parser repairs one known upstream quirk: a speaker with two ids in one attribute and doubled name elements (Svenja Schulze in 21/18, 21/24, 21/25, 21/28, 21/44–46). First id wins, doubled strings are halved. Seen in 7 of 94 WP21 files; no other merged ids exist.
- 2026-09-23 — `bdf update` derives every fetch window from `data/raw/` instead of storing a state file. The raw files already are the state; a separate file could drift from them, and a missed week is caught up automatically.
- 2026-09-23 — `update` re-reads votes and DIP 14 days back and refetches Stammdaten and abgeordnetenwatch in full each run. Late publications are common, the full refetches are one file and ~15 requests, and ingest upserts idempotently.
- 2026-09-23 — Container image runs as uid 1000 with `BDF_DATA_DIR=/data`. Matches the host user, so a bind-mounted data directory is writable from both sides without chown.
