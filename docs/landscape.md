# Landscape: existing Bundestag data sources and corpora

Survey date: 2026-09-21. Current Wahlperiode: 21 (constituted 2025-03-25); latest
plenary sitting at survey time: 21/94 on 2026-09-11. Everything below was checked
live (HTTP calls, sample downloads, GitHub API) unless marked "not verified".

Verdict key: **reuse** = take data or code as-is · **borrow** = copy ideas/logic,
not data · **ignore** = not useful for this project.

## 1. Primary sources

### 1.1 bundestag.de Open Data — Plenarprotokolle (XML)

| | |
|---|---|
| Covers | All Plenarprotokolle WP 1–21. WP 19+ follow the structured DTD `dbtplenarprotokoll.dtd` (Stand 2023-09-05). Older WPs are flat text-in-XML. |
| Format | One XML file per sitting. Predictable URL `https://dserver.bundestag.de/btp/{wp}/{wp}{nnn}.xml` (e.g. `btp/21/21094.xml`, 510 KB), identical bytes to the blob linked from the Open Data page. PDF at the same path with `.pdf`. Listing endpoint: `https://www.bundestag.de/ajax/filterlist/de/services/opendata/1058442-1058442?limit=N` (WP21; `866354` = WP20, `543410` = WP19). |
| Structure (verified on 21/94) | `sitzungsverlauf > tagesordnungspunkt[top-id] > rede[id]`. Each `rede` starts with `<p klasse="redner"><redner id="11004006"><name>…<fraktion>…</fraktion>`. **`redner/@id` is the official MdB ID**, identical to `MDB/ID` in the Stammdaten XML. Interjections are `<kommentar>`; presidency remarks *inside* a `rede` are `<name>Vizepräsident …:</name>` + following `<p>`; questions from other MdBs (Zwischenfragen) appear as further `<p klasse="redner">` inside the same `rede` (3 of 57 `rede` in 21/94 have >1 speaker). Paragraph classes: `J`, `J_1`, `O` (speech text), `T_*` (procedural: Drucksache refs, Überweisung). No page anchors inside `sitzungsverlauf`; page/quadrant only in the table of contents. |
| Licence | No licence statement on the Open Data page. bundestag.de Nutzungsbedingungen: free of charge for parliamentary reporting, education and cultural purposes; not for commercial advertising; source to be cited as "Deutscher Bundestag". Plenarprotokolle and Drucksachen are generally treated as amtliche Werke (§ 5 Abs. 2 UrhG: free use with source attribution and no alteration). See `docs/licences.md` (Phase 2). |
| Last update | Continuous; 21/94 (2026-09-11) was online at survey time. XML typically appears a few days after the sitting (the "vorläufiges Protokoll" PDF comes first). |
| Maintained | Yes (official). |
| Verdict | **reuse — primary source for speeches.** |

### 1.2 bundestag.de Open Data — MdB Stammdaten (XML)

| | |
|---|---|
| Covers | All 4,614 MdBs since 1949, incl. all 635 persons who held a WP21 mandate so far (with `MDBWP_VON/BIS`, Wahlkreis, Liste, Mandatsart, `INSTITUTIONEN` = Fraktion, committees with `FKT_LANG` role and dates). File header "Erstellt am 29.04.2026". |
| Format | ZIP `https://www.bundestag.de/resource/blob/472878/MdB-Stammdaten.zip` → `MDB_STAMMDATEN.XML` (15 MB) + DTD. |
| Licence | As 1.1. |
| Last update | Irregular, roughly every few months; the "Erstellt am" comment inside the file is the version marker. |
| Maintained | Yes (official). |
| Verdict | **reuse — the person/mandate/committee master table and the ID anchor for everything else.** |

### 1.3 bundestag.de Open Data — Drucksachen

| | |
|---|---|
| Covers | All Drucksachen. |
| Format | For WP21 the Open Data list (`…/opendata/854776-854776`) links **PDF only** (`https://dserver.bundestag.de/btd/21/080/2108063.pdf`); no structured XML for current Drucksachen, despite the page text. |
| Verdict | **ignore for metadata; keep PDF URL as source pointer.** Use DIP (1.5) for Drucksache metadata, authors and text. |

### 1.4 bundestag.de Open Data — Namentliche Abstimmungen (XLSX/PDF)

| | |
|---|---|
| Covers | Every roll-call vote since ~2010, one file per vote. |
| Format | List endpoint `https://www.bundestag.de/ajax/filterlist/de/parlament/plenum/abstimmung/liste/462112-462112?limit=N` (HTML fragment; each row has date, title, PDF and XLSX blob URLs). XLSX columns (verified on `20260710_7-xls.xlsx`): `Wahlperiode, Sitzungnr, Abstimmnr, Fraktion/Gruppe, Name, Vorname, Titel, ja, nein, Enthaltung, ungültig, nichtabgegeben, Bezeichnung, Bemerkung`; one row per MdB (630 rows). |
| Missing | No MdB ID (name + Fraktion only), no Drucksache number, no TOP. The subject is only in the list title / PDF header. |
| Licence | As 1.1. |
| Last update | Same day or next day after the vote. |
| Verdict | **reuse — the authoritative per-person vote record**; needs name→MdB-ID matching and vote→Drucksache linking via DIP (see §4). |

### 1.5 DIP API (`https://search.dip.bundestag.de/api/v1`)

| | |
|---|---|
| Covers | Vorgang, Vorgangsposition, Aktivität, Person, Drucksache (+ `-text`), Plenarprotokoll (+ `-text`), WP 8–21 (Drucksachen/Protokolle metadata further back). |
| Format | JSON or XML, OpenAPI 3.0.1 spec v1.5 at `/api/v1/openapi.yaml` (82 KB, fetched). Cursor pagination (repeat request with `cursor` until it stops changing), max 100 entities per page, 10 for `-text` endpoints. Filters: `f.wahlperiode`, `f.datum.start/end`, `f.aktualisiert.start`, `f.drucksache`, `f.plenarprotokoll`, `f.vorgang`, `f.person`… |
| Relevant fields | `Drucksache.autoren_anzeige` = **only the first 4 authors** (`id` = DIP person id, not MdB ID); full author list via `/aktivitaet?f.drucksache={id}` (one Aktivität per author, `person_id`). `Aktivitaet` with `aktivitaetsart: "Rede"` has `fundstelle.seite` (e.g. `9800D`) and `person_id`. `Vorgangsposition.beschlussfassung.abstimmungsart` includes `"Namentliche Abstimmung"` with `seite` and `dokumentnummer` → tells *which* Drucksache had a roll-call vote and where in the protocol, but not individual votes. `Fundstelle.xml_url` points to the dserver XML. |
| API key | Required (`Authorization: ApiKey …` header or `?apikey=`). A **public key rotates roughly yearly** and is only shown on the JS-rendered help page (`https://dip.bundestag.de/über-dip/hilfe/api`); the two most recent public keys found online (valid to May 2025 / May 2026) both return 401 now. Personal keys with 10-year validity by e-mail to parlamentsdokumentation@bundestag.de — **request one; do not rely on the public key.** |
| Rate limits | Official info sheet (Stand März 2023): "nicht mehr als 25 gleichzeitige API-Anfragen"; further limits deliberately unpublished. Updates visible in the API with ~15 min delay. |
| Licence | DIP Nutzungsbedingungen (`https://dip.bundestag.de/über-dip/nutzungsbedingungen`) — page is JS-rendered and could not be read in this survey; **not verified**, must be read manually before publication. |
| Maintained | Yes (official). Third-party wrappers exist (bundesAPI/dip-bundestag-api Python, maschinenlesbar-org/dip-bundestag-cli TS); none needed. |
| Verdict | **reuse — source for Drucksachen, Vorgänge, authorship, and the vote↔Drucksache link.** |

### 1.6 abgeordnetenwatch.de API v2 (`https://www.abgeordnetenwatch.de/api/v2`)

| | |
|---|---|
| Covers | Parliaments, periods, politicians, candidacies-mandates (with `fraction_membership`, `electoral_data`), polls (roll-call votes with editorial intro), votes (per mandate: yes/no/abstain/no_show), committees, fractions. Bundestag WP21 = `parliament_period=161`, 630 mandates, 671 Bundestag polls in total. |
| Format | JSON, no key, `range_start/range_end` paging (100 per page, related data capped at 1,000). **30 requests/min/IP** (HTTP 429), bulk pulls asked to run 22:00–06:00. API version 2.9.0. |
| Licence | **CC0 1.0** — stated in every response `meta.abgeordnetenwatch_api.licence` and on the API page. Cleanest licence of all sources. |
| Entity linking | `politician.ext_id_bundestagsverwaltung` (MdB ID) and `qid_wikidata` exist. **Verified against Stammdaten for all 630 WP21 mandates: 2 politicians have no ID, and 56 have a *wrong* ID** — newcomers were matched to a historical namesake (e.g. Marcel Bauer → 11000105 = Hannsheinz Bauer, WP2–6; Desiree Becker and Carsten Becker both → 11000125 = Curt Becker). `candidacies-mandates.id_external_administration` is a different, undocumented id ("mdbID aus der info-xml-Datei"), not the Stammdaten ID. So the field is a hint, not a key. |
| Provenance | A poll has `field_poll_date`, title, editorial `field_intro` HTML (often with a dserver Drucksache link), topics, committees — **no structured Drucksache/Sitzung/Abstimmnr field**. Votes reference `mandate`, not `politician` (one more hop). Latest poll at survey time: 2026-07-10 (matches the last sitting before the summer break). |
| Maintained | Yes (NGO, editorial team; polls appear days after the vote). |
| Verdict | **reuse for cross-IDs (with our own validation) and as a secondary/derived vote source; primary vote record stays bundestag.de XLSX** (see §4). |

## 2. Corpora and derived datasets

### 2.1 Open Discourse (open-discourse.de, GitHub open-discourse/open-discourse)

| | |
|---|---|
| Covers | Speeches WP 1–20 (data v4.0, released 2023-06-04 on Harvard Dataverse `doi:10.7910/DVN/FIKIBO`). Nothing after mid-2023, no WP21. |
| Format | 6 tables as CSV/feather/pickle/RDS: `speeches` (id, session, electoral_term, politician_id, faction_id, position_short/long, date, document_url, speech_content), `politicians`, `factions`, `electoral_terms`, `contributions_extended` (interjections with speaker/faction), `contributions_simplified`. PostgreSQL schema in repo. |
| Speaker matching | For WP 19–20 they read `redner/@id` from the XML directly → `politician_id` = Stammdaten ID (verified in `05_electoral_term_19_20/01_extract_speeches…py`). Older WPs: regex name matching against Stammdaten. |
| Licence | Code MIT; data **CC0 1.0** (Dataverse). |
| Last update | Code 2025-02-12 (dependency refactor); README: "currently not under active development". Data 2023-06. |
| Verdict | **borrow** — their table shape (speeches / contributions / politicians / factions) is close to what we need, and their WP19+ XML extraction (incl. stripping presidency `<name>` blocks, splitting Zwischenfragen, regex faction normalisation) is worth reading. Data is stale; do not depend on it. |

### 2.2 GermaParl2 (PolMine, Zenodo 12794676)

| | |
|---|---|
| Covers | 1949 – 2023-07-07 (v2.1.0, 2024-07-22); 4,461 protocols. |
| Format | TEI-inspired XML + CWB binary, 2.5 GB tar. R-centric tooling (polmineR). |
| Licence | **CC BY-SA 4.0** — share-alike is awkward for a broadcaster product. |
| Maintained | Being migrated into ParlaMint-DE (LREC 2026 paper); GermaParl itself not updated since 2024. |
| Verdict | **ignore** (stale, share-alike, R). |

### 2.3 ParlaMint (CLARIN) — ParlaMint-DE

| | |
|---|---|
| Status | ParlaMint 5.0 (CLARIN.SI 11356/2004) has 29 corpora; a German component "ParlaMint-DE" is announced (PolMine, LREC 2026 workshop paper "Towards ParlaMint-DE") to cover 1949–2025, built from GermaParl. Not yet a current, weekly-updated source. Licence CC BY 4.0 (ParlaMint standard). |
| Verdict | **ignore** for data; **borrow** their speaker metadata conventions if we ever export TEI. |

### 2.4 "A Parliamentary Discourse Dataset from the German Bundestag" (RWTH Aachen, Zenodo 21258818)

| | |
|---|---|
| Covers | WP 1–21, Sept 1949 – April 2026, 1,033,723 speeches, v2 published 2026-07-08. |
| Format | Parquet/CSV: `speeches`, `persons`, `sessions`, `speakers`; has `stammdaten_id`. Built from **PDFs via pdftotext**, regex speaker split; **interjections are embedded in the speech text**, not separated. |
| Licence | CC BY 4.0. |
| Verdict | **ignore** — PDF-derived when the XML with explicit `<kommentar>` and `redner/@id` exists; not weekly. |

### 2.5 bundestag.io / DEMOCRACY Deutschland (demokratie-live/democracy-development)

| | |
|---|---|
| Covers | Vorgänge ("procedures") scraped from DIP, roll-call results, plus the app's own citizen votes. No speeches. GraphQL, MongoDB, Apache-2.0. The old `demokratie-live/bundestag.io` repo is archived (2021); code lives on in the monorepo (`bundestag.io/api`, `services/scrapers`, `services/procedures`), last push 2026-09-21. |
| Verdict | **ignore** — a full app stack around DIP; no hosted API guarantee; nothing we cannot get from DIP directly. |

### 2.6 Bundestags-Mine (bundestag-mine.de, TheItCrOw/Bundestags-Mine, TTLab Frankfurt)

| | |
|---|---|
| Covers | WP 19–20 protocols from bundestag.de XML; NER, sentiment, summarisation, topic maps per Fraktion; speaker profiles; download centre. WP21 apparently not covered (site listing ends at WP20 — not fully verified, the site is JS-heavy). Repo: JavaScript/C#, last commit 2024-12-03, **no licence file**. Tokens/annotations pushed to GerParCor. |
| What they did / did not do | Did: per-speech NLP annotations, topic distribution per party, speaker pages. Did not: speaker-level clean-text export as a corpus, embedding/clustering map of one week, cross-source IDs, roll-call votes per person. Their topic modelling is per-speech category labels, not an embedding map. |
| Verdict | **borrow** the idea list (what a "speech landscape" UI shows), **ignore** code/data (no licence, stale). |

### 2.7 Machtblick (machtblick.de, soliblue/machtblick) — found during the survey

| | |
|---|---|
| Covers | The closest thing to this project: ETL from Bundestag Open Data (Reden-XML, Stammdaten, Namentliche-Abstimmungen XLSX), DIP, abgeordnetenwatch, Wikidata → **SQLite**, weekly systemd refresh, web + iOS app. TypeScript, **AGPL-3.0**, created 2026, 0 stars, generated DB not published. |
| Useful details in code | `etl/bundestag/votes/import-namentlich.ts`: uses the same XLSX list; name-matches vote rows to members with an alias table; takes the MdB ID from abgeordnetenwatch mandates (`id_external_administration`, which is the wrong field, see 1.6). `etl/dip/linkVotes.ts`: links votes to DIP Vorgänge. `etl/bundestag-reden-xml/parse.ts`: agenda + speech split with a fallback agenda-item list. |
| Verdict | **borrow** (read their matching edge cases: `MEMBER_ALIASES`, Handzeichen repairs) — **do not reuse** (AGPL, app-shaped, no data release). |

### 2.8 Hugging Face / GitHub odds and ends

| Item | Note | Verdict |
|---|---|---|
| HF `threite/Bundestag-v2` (CC0, 2023) | Open Discourse re-cut, train/val/test parquet | ignore |
| HF `wirthual/dip-bundestag` (2025-05, no licence) | Single parquet dump of DIP entities | ignore |
| HF `hannahsteinbach/bundestag-20` (2026-07, no licence) | One CSV of WP20 speeches | ignore |
| HF `D4ve-R/bundestag-asr` | Audio/ASR pairs, off-topic | ignore |
| `bundestag/plpr-scraper` (MIT, 2017) | Pre-DTD text parser | ignore |
| `bundestag/dip21-daten`, `okfde/offenesparlament.de` | Old DIP21 scrapes, stale since ≤2024 | ignore |
| `Nolram567/Namentliche_Abstimmungen` (2025) | Network analysis of XLSX votes; confirms the XLSX parsing approach | ignore |
| Haider et al. 2026, "Linking Speakers of the German Parliament to Wikidata" (arXiv 2609.18289, OSF 5s3nc) | 4,325 speakers 1949–2021 → Wikidata QIDs, CC BY 4.0 | borrow later if Wikidata enrichment is wanted; not WP21 |
| GerParCor (TTLab) | Annotated protocol corpus behind Bundestags-Mine | ignore |

## 3. Entity linking: who already maps protocol speaker → DIP person → abgeordnetenwatch politician?

**Nobody does all three, and the one published mapping (abgeordnetenwatch → MdB ID) is ~9 % wrong for WP21.**

What exists, verified:

1. **Protocol speaker → MdB ID: solved by the Bundestag itself.** `redner/@id` in the WP19+ XML equals `MDB/ID` in the Stammdaten. No matching needed for MdBs. Non-MdB speakers (ministers who are not MdB, Bundesrat members, guests) also carry an id from the same namespace.
2. **MdB ID → abgeordnetenwatch politician: partially solved, unreliable.** `ext_id_bundestagsverwaltung` is present for 628/630 WP21 politicians but wrong for 56 (historical namesakes). Correct approach: match Stammdaten (`VORNAME`, `NACHNAME`, `GEBURTSDATUM` year, WP21 membership) to abgeordnetenwatch (`first_name`, `last_name`, `year_of_birth`, mandate in period 161), and *accept* `ext_id_bundestagsverwaltung` only when it agrees. Expect a handful of manual aliases (Machtblick needed four). Roughly 30 lines of code plus a small alias table.
3. **MdB ID → DIP person id: not solved anywhere.** DIP `Person` has no MdB ID; its ids are DIP-internal (e.g. `1728`). DIP `Person` carries `vorname`, `nachname`, `namenszusatz`, `fraktion`, `wahlperiode[]`, `bundesland`, `wahlkreiszusatz` — enough for a deterministic name+Fraktion+WP match against Stammdaten with the same alias table. Cross-check when ambiguous: DIP Aktivität "Rede" gives protocol number + page; the XML table of contents gives page per speech. Open Discourse never touched DIP; Machtblick bootstraps DIP persons by name (`_oneshot/bootstrapDipPersons.ts`).

Conclusion: the MdB ID from the Stammdaten is the hub. Two name-based matchers (→ DIP, → abgeordnetenwatch), each validated against birth year / WP membership, are the whole entity-linking problem for the current Wahlperiode. This is cheaper than adopting anyone's corpus.

## 4. Roll-call votes: is there anything cleaner than the bundestag.de XLSX?

Checked: bundestag.de Open Data page and list endpoint, DIP OpenAPI schema, abgeordnetenwatch, Machtblick's importer (whose methodology page claims "XML" but whose code reads the XLSX), GitHub.

- **No official machine-readable vote record beyond XLSX/PDF exists.** DIP records *that* a roll-call vote happened (`beschlussfassung.abstimmungsart = "Namentliche Abstimmung"`, with `dokumentnummer` and protocol page) but not who voted how. The Plenarprotokoll XML contains the result lists only as prose in the Anlagen.
- **bundestag.de XLSX** is the primary record: exact `Wahlperiode/Sitzungnr/Abstimmnr`, one row per MdB, result columns as 0/1, available same day. Parsing is trivial (openpyxl, or zip + XML from the standard library). Cost: name → MdB ID matching (Name, Vorname, Fraktion against Stammdaten WP21 members — unambiguous except for a few duplicated surnames, resolvable with Vorname) and no Drucksache number in the file (link via date + title to DIP Vorgangsposition on the same sitting, or to the `T_Drs` paragraphs in the protocol XML around the vote).
- **abgeordnetenwatch** is the practical *convenience* source — CC0, JSON, per-person votes, fraction attached — but it is editorially curated (a few days' lag, occasionally missing procedural votes), its poll↔Drucksache link is only an HTML link in prose, and its MdB IDs are wrong for ~9 % of WP21 members.

Recommendation: ingest the XLSX as the fact source (source pointer = XLSX/PDF blob URL + `WP/Sitzung/Abstimmnr`, which also locates the vote in the Plenarprotokoll), link the subject through DIP, and use abgeordnetenwatch only for cross-IDs and as an optional consistency check. Fraction majority is computed from our own rows, not taken from anyone.

## 5. Recommendation: build on an existing corpus, or ingest primary sources?

**Ingest from primary sources.** Reasons:

1. **Freshness.** The only corpus with a schema we would want (Open Discourse) stops in 2023; GermaParl in 2023; ParlaMint-DE is announced, not delivered. Both applications need the *current* sitting week, days after it happened. Only bundestag.de + DIP + abgeordnetenwatch deliver that.
2. **The hard part is already done upstream for WP19+.** The XML gives speech boundaries, speaker MdB ID, Fraktion, agenda item and separated interjections. A parser for that DTD is a few hundred lines; Open Discourse's WP19/20 extractor is the reference to check against.
3. **Provenance.** Ingesting primary files means every fact points at a file we hold on disk unchanged (XML, XLSX, DIP JSON) with a public URL. A third-party corpus would put a CC0/CC-BY-SA layer between the newsroom and the source.
4. **Licence clarity for a broadcaster.** Bundestag material (attribution "Deutscher Bundestag") + CC0 (abgeordnetenwatch) is a short, defensible licence page. CC BY-SA (GermaParl) and AGPL code (Machtblick) are not.
5. **Nothing to give up.** Open Discourse's table design (speeches / contributions / politicians / factions / electoral_terms) can be adopted almost as-is, extended by sitting, agenda item, Drucksache, Vorgang, roll-call vote and individual vote.

What to borrow, concretely, in Phase 2/3:
- Open Discourse: table shape; XML handling of `<name>` presidency blocks and multi-speaker `rede`; faction regex normalisation.
- Machtblick: alias table for vote-list names; the DIP vote-linking idea; weekly-refresh runbook shape.
- abgeordnetenwatch: use as ID bridge with validation; do not trust `ext_id_bundestagsverwaltung` blindly.

Open items before Phase 2:
- Obtain a personal DIP API key (e-mail; 10-year validity). Without it nothing in DIP can be tested.
- Read the DIP Nutzungsbedingungen in a browser and paste the text into `docs/licences.md`.
- Decide whether non-MdB speakers (ministers without mandate, Bundesrat) get their own person rows (they have `redner/@id` too, so it costs nothing to store them).
