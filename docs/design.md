# Design

Scope: a weekly-updated store of Bundestag data for the current Wahlperiode (21),
built from bundestag.de Open Data, the DIP API and abgeordnetenwatch.de, plus the Bundeswahlleiterin's results,
bundestag.de portraits and the Wikidata government roster —
with a source pointer on every fact. See `landscape.md` for why these sources.

## Storage

- **SQLite**, one file `data/bundestag.sqlite`, Python stdlib `sqlite3`, WAL mode.
  Chosen over DuckDB because the workload is joins over small tables, a single
  writer once a week, and readers in other repos that only need `sqlite3`.
  FTS5 can be added later for full-text search; not needed for the acceptance test.
- **Raw files are kept on disk unchanged** under `data/raw/`, one folder per source.
  The store can always be rebuilt from `data/raw/` without network access.

```
data/
  raw/
    bundestag/
      stammdaten/MdB-Stammdaten.zip          # + extracted MDB_STAMMDATEN.XML
      protocols/21/21094.xml                 # dserver.bundestag.de/btp/21/21094.xml
      votes/20260710_7-xls.xlsx (+ .pdf)     # + votes/index.json (list rows: date, title, urls)
    dip/
      drucksache/2026-07-06_2026-07-10.json  # list responses, one file per fetched range
      aktivitaet/drucksache-<id>.json        # authors of one Drucksache
      plenarprotokoll/wp21.json              # all BT Plenarprotokolle of the Wahlperiode
      aktivitaet/plenarprotokoll-<id>.json   # Aktivitäten of one protocol (Mündliche Fragen, speeches); fetched
                                             # again while the protocol is younger than 180 days
      vorgangsposition/2026-07-06_2026-07-10.json  # all BT Vorgangspositionen dated in the range
      vorgangsposition_other/2025-03-25_2026-07-10-BR.json  # BR/BV/EK Vorgangspositionen, one file per
                                                              # zuordnung and range (Bundesrat steps: 1./2.
                                                              # Durchgang, Zustimmung, Einspruch, Vermittlungs-
                                                              # ausschuss, …)
      vorgang/drucksache-<id>.json           # all Vorgänge linked to one Drucksache
      person/wp21.json
    abgeordnetenwatch/
      wp21-mandates.json
      wp21-politicians.json
    bundeswahlleiterin/
      btw25/btw25_gewaehlte_utf8.zip         # + extracted btw25_gewaehlte_utf8.csv: the elected
      btw25/kerg2.csv                        # results per Wahlkreis, party and vote
      btw25/btw25_nachfolger.pdf             # Mandatsnachfolger, downloaded again on every run
    bundestag/biografien/page-000.html …     # the MdB card list, 12 cards per page, replaced on every fetch
    bundestag/fotos/<image id>.jpg           # portraits as downloaded (864×1152), never re-downloaded
    wikidata/
      positions.json                         # SPARQL: position items under Bundesminister / PStS / beamteter StS
      government.json                        # SPARQL: P39 statements since 2025-05-06 with dates, department, person
      commons.json                           # Commons imageinfo (thumbnail URL, author, licence) of the P18 images
      fotos/<Commons file name>              # 864 px thumbnails
  bundestag.sqlite
```

Every raw file has a sidecar `<name>.meta.json` = `{"url", "retrieved_at"}` written by
`fetch`; `ingest` takes provenance from it.

## Identifiers

- `person.id` = the Bundestag MdB ID (`MDB/ID` in the Stammdaten, `redner/@id` in the
  protocol XML), as TEXT, e.g. `11004006`. Non-MdB speakers (ministers without a
  mandate, Bundesrat members, guests) also carry an id from the same namespace in the
  XML and get a `person` row with `is_mdb = 0`.
- `sitting.id` = `"<wp>/<nr>"`, e.g. `21/94` — the same string the Bundestag prints as
  "Plenarprotokoll 21/94".
- `speech.id` = the XML `rede/@id` (`ID219400100`), suffixed `-2`, `-3`… when one
  `rede` element contains several speakers (Zwischenfrage) and is split.
- `drucksache.id` / `vorgang.id` = DIP ids (TEXT); `drucksache.number` = `21/7300`.
- `roll_call_vote.id` = `"<wp>/<sitting>/<abstimmnr>"`, e.g. `21/90/7`.
- Cross-IDs live on `person`: `dip_person_id`, `aw_politician_id`, `wikidata_qid`.
- Placeholder person ids (a Wikidata QID for a government member without a match, `pdf-<name>` for a speaker read
  from a PDF) disappear once the person is matched. **person_alias** `*alias_id, person_id →person, recorded_at`
  keeps where they went, so a consumer can redirect a page address built from the old id: written when
  `ingest_government` drops a matched QID row and when `retire_pdf_speakers` (the last ingest step) drops a
  `pdf-` row nothing refers to any more, aliased to the one other person of that name. Rows are never removed.

## Provenance

Every fact table has three columns:

| column | meaning |
|---|---|
| `source_url` | the exact file or API URL the row was parsed from |
| `source_document_id` | the citable document, e.g. `BT-PlPr. 21/94`, `BT-Drs. 21/7300`, `NA 21/90/7`, `MDB_STAMMDATEN 2026-04-29`, `DIP aktivitaet 1493545`, `aw politician 79455` |
| `retrieved_at` | ISO timestamp of the download of the raw file |

Child rows that are parsed from the *same* raw file as their parent
(`speech_paragraph` ← `speech`, `individual_vote` ← `roll_call_vote`) inherit provenance
through the foreign key and do not repeat the three columns. Every other row carries them.

## Tables

Types are SQLite affinities. `*` = primary key. `→` = foreign key.

**person** `*id, first_name, last_name, name_prefix (Adel/Präfix), academic_title, birth_date, birth_place, gender, party, is_mdb, role (non-MdB speakers: rolle_lang), dip_person_id, aw_politician_id, wikidata_qid, source_url, source_document_id, retrieved_at`

**mandate** `*id, person_id →person, wahlperiode, from_date, to_date, mandate_type (Direktwahl/Landesliste), constituency_number, constituency_name, state, source_url, source_document_id, retrieved_at`

**membership** `*id, person_id →person, wahlperiode, kind (fraction | committee | other), name, role (FKT_LANG, e.g. Vorsitzende), from_date, to_date, source_url, source_document_id, retrieved_at`
— from Stammdaten `INSTITUTIONEN`; `kind` is derived from `INSART_LANG`.

**sitting** `*id, wahlperiode, number, date, start_time, end_time, xml_url, pdf_url, source_url, source_document_id, retrieved_at, preliminary (0 | 1), final_announced, final_fetched_at, first_page, last_page`
— `preliminary = 1`: the XML on disk is the preliminary version (below, "Preliminary protocols"); `final_announced` is the date its note gives for the final version, `final_fetched_at` the `retrieved_at` of the XML once it is final (NULL while preliminary). `first_page` is `start-seitennr`. `last_page` is set only for a preliminary sitting whose PDF part was read: the Druckseite the XML's text ends on, found in the final PDF; the agenda items, paragraphs and speeches after it come from the PDF and have it as their `source_url` (`source_document_id` "BT-PlPr. 21/31 (PDF)").

**agenda_item** `*id, sitting_id →sitting, position, top_id (XML top-id attribute), title, drucksache_numbers (JSON array of "21/7300"), source_url, source_document_id, retrieved_at, no_debate (0 | 1, default 0)`
— `no_debate = 1`: the chair said that no Aussprache is provided ("zu denen keine Aussprache vorgesehen ist", "ohne Debatte", "Eine Aussprache ist nicht vorgesehen"). For a block item (below) it means the block was called up that way. Speeches of the item stay as they are.

**speech** `*id, sitting_id →sitting, agenda_item_id →agenda_item, position (order within sitting), person_id →person, speaker_name (as printed), speaker_role (rolle_lang or NULL), fraction (as printed, normalised), text (clean speech text: paragraphs of kind text only, joined by blank lines), source_url, source_document_id, retrieved_at, kind (rede | fragestunde, default 'rede'), sub_item_id →agenda_sub_item`
— `kind = 'fragestunde'`: a question, answer or Nachfrage from a Fragestunde (below), id `"<agenda_item_id>/f<n>"`. Shown like any other speech, but left out of speech counts and speaking shares by callers (it is not a debate contribution).

**speech_paragraph** `*id, speech_id →speech, position, kind (text | comment | chair | procedural), text`
— `text` = the speaker's words (`p` classes J, J_1, O, …); `comment` = `<kommentar>` (applause, interjections); `chair` = presidency remarks inside the `rede` (`<name>Vizepräsident…</name>` and the paragraphs until the speaker resumes); `procedural` = `T_*` classes.

**drucksache** `*id (DIP), number, wahlperiode, type (drucksachetyp), title, date, pdf_url, publisher (herausgeber), originators (JSON of urheber titles), author_count (DIP `autoren_anzahl`; the number of distinct persons in the fetched activities where the two disagree), source_url, source_document_id, retrieved_at`

**drucksache_author** `*id, drucksache_id →drucksache, dip_person_id, person_id →person (NULL until resolved), name (DIP titel), activity_type (aktivitaetsart: Antrag, Kleine Anfrage, Große Anfrage, Entschließungsantrag, Änderungsantrag, Gesetzentwurf, Frage count as authorship; Berichterstattung and Antwort do not), source_url, source_document_id, retrieved_at`
— from `/aktivitaet?f.drucksache=<id>`; DIP's `autoren_anzeige` alone is truncated to 4. One row per person and Drucksache: a member with three questions in one Sammeldrucksache has one row; the single questions are in `question_activity`.

**question_activity** `*id (DIP Aktivität), vorgang_id (the single question), question_type (Schriftliche Frage | Mündliche Frage), activity_type (Frage | Zusatzfrage | Antwort), dip_person_id, person_id →person (NULL until resolved), name (DIP titel), ressort (Antwort only), document_kind (Drucksache | Plenarprotokoll), document_number, question_numbers (Drucksache: frage_nummer, "93, 94"), page (Plenarprotokoll: "683D"), source_url, source_document_id, retrieved_at`
— who asked and who answered each Frage: the Aktivitäten of the Sammeldrucksachen (types Schriftliche Fragen and Fragen) and of every Plenarprotokoll (`/aktivitaet?f.plenarprotokoll=<id>`), kept where `vorgangsbezug` names exactly one Vorgang of a question type. The asker of a Frage is its `Frage` row, its Ressort the `ressort` of its `Antwort` row: the part of the answerer's title after name and function ("Ulrich Lange, Parl. Staatssekr., Bundesministerium für Verkehr"), as DIP spells it at the time (the ministries before May 2025 have their old names). DIP's Vorgangspositionen of Fragen carry no `ressort`. A Mündliche Frage answered in writing has its Frage and Antwort on the protocol (the Anlage); one asked only on the Fragen-Drucksache has no Antwort. Live store, 2026-10-04: all 9,038 Schriftliche and 1,624 Mündliche Fragen have one asker; 9,037 and 1,584 a Ressort (the rest not answered or withdrawn); 4 askers (191 activities) have no `person_id` because their DIP person is not matched.

**vorgang** `*id (DIP), wahlperiode, type (vorgangstyp), title, status (beratungsstand), subjects (JSON sachgebiet), initiators (JSON initiative), verkuendung (JSON DIP verkuendung objects: BGBl reference, Ausfertigungs-/Verkündungsdatum, or NULL), inkrafttreten (JSON array of {datum, erlaeuterung}, or NULL), source_url, source_document_id, retrieved_at`
— Verkündung/Ausfertigung is not a vorgangsposition step under any zuordnung (checked against the whole BT-only Wahlperiode so far: no "Verkündung"/"Ausfertigung" position exists); DIP carries it only on the Vorgang record itself.

**vorgang_drucksache** `vorgang_id →vorgang, drucksache_id →drucksache` (composite PK)

**vorgang_position** `*id (DIP), vorgang_id (DIP; not every Vorgang is in vorgang), date, position (vorgangsposition: "Gesetzentwurf", "1. Beratung", "Antwort", …), chamber (zuordnung), document_kind (Drucksache | Plenarprotokoll), document_number, document_type (drucksachetyp), pdf_url, pages ("1234-1236", protocols), originators (JSON urheber titles), ressort (JSON [{titel, federfuehrend}] or NULL), decisions (JSON beschlussfassung as in DIP, or NULL), source_url, source_document_id ("DIP Vorgangsposition <id>"), retrieved_at`
— one row per step of a Vorgang, from the same date-range lists as the vote linking (deduplicated by id, latest copy wins). `chamber` (zuordnung) is BT, BR, BV or EK: BT positions come from the main range file, BR/BV/EK positions from the separate `vorgangsposition_other` files (one per zuordnung and range), so a step such as "1. Durchgang", "Zustimmung", "Kein Einspruch eingelegt" or "Anrufung des Vermittlungsausschusses" is a BR row like any BT row. Vote linking (`_link_votes_to_dip`) still reads BT positions only.

**roll_call_vote** `*id, sitting_id →sitting, number (Abstimmnr), date, title (from the bundestag.de list), drucksache_number (NULL until linked), vorgang_id →vorgang (NULL until linked), link_method (dip_beschluss | title_regex | manual | NULL), yes, no, abstain, invalid, absent (totals computed from individual_vote), xlsx_url, pdf_url, source_url, source_document_id, retrieved_at, agenda_item_id →agenda_item (see Chair text and decisions)`

**individual_vote** `*id, vote_id →roll_call_vote, person_id →person (NULL if unmatched), last_name, first_name, fraction, vote (yes | no | abstain | invalid | absent)`

Fraction majority per vote is a query, not a column:
`SELECT fraction, vote, COUNT(*) … GROUP BY fraction, vote`.

No separate `fraction` table: fraction is a normalised string (`CDU/CSU`, `SPD`, `AfD`,
`BÜNDNIS 90/DIE GRÜNEN`, `Die Linke`, `fraktionslos`) with one normalisation function
shared by the XML, XLSX and Stammdaten parsers. A table would add a join and nothing else.

### Fraction and speaker group

Derived columns, recomputed in full by `ingest_groups` at the end of every ingest, so that the consumers (cards,
landscape, the export's users) don't each work them out again in their own way. The vocabulary is in `bdf/names.py`
and nowhere else.

- **`speech.speaker_group`**: who a speech counts for. A speech given in a federal government office (the printed
  role parses with `government.parse_role`, or "Beauftragte der Bundesregierung …") counts for the
  **Bundesregierung**, whatever the speaker's fraction; a Land office ("Staatsministerin (Hessen)") for the
  **Bundesrat**; otherwise the printed fraction (`speech.fraction`), else **Sonstige** (the Wehrbeauftragte).
  The chair's words are not speeches, so the Präsidium never appears.
- **`speech.member_fraction`**: the speaker's fraction on the sitting day from `membership` (for a Nachrücker not
  yet in the Stammdaten, the printed one), also when the speech counts for the government. NULL for non-members.
- **`person.fraction`**: the fraction in the newest Wahlperiode (the open membership, else the last one; without
  one, as for a Nachrücker not yet in the Stammdaten, the latest one printed in protocols and vote lists, else
  "fraktionslos"). NULL for everyone else. *The* current fraction for consumers.
- **`drucksache.originator_groups`**: JSON array of the fractions and "Bundesregierung" among the DIP Urheber
  ("Fraktion der SPD" → SPD, "Gruppe …" likewise, a ministry → Bundesregierung); committees, the Bundesrat and the
  President are not groups.
- **`party_fraction`** `*party, fraction`: every party name in `person.party`, `election_candidacy.party`,
  `constituency.seat_party` and `constituency_result.party` that belongs to a fraction ("CSU" → CDU/CSU, "GRÜNE"
  → BÜNDNIS 90/DIE GRÜNEN, "DIE LINKE." → Die Linke), so a consumer joins instead of keeping a map. Parties without
  a fraction (SSW) have no row.

### Speech parts

Also derived, by `ingest_speech_parts` after `ingest_groups`:

- **`agenda_item.kind`**: `befragung` ("Befragung der Bundesregierung"), `fragestunde` (by title, or an item with
  Fragestunde speeches), `aktuelle_stunde`; NULL for every other item.
- **`speech.rede_id`**: the speech a part belongs to, the id without its "-2", "-3" … (a Fragestunde turn is its own).
- **`speech.interruption`**: for a part by someone other than the rede's first speaker, `kurzintervention` when the
  chair's words among the last six paragraphs of the part before it announce one ("Kurzintervention",
  "Zwischenbemerkung"), else `zwischenfrage`. The same person interrupting again counts as a new interruption only
  after at least 30 words of the main speaker (`NEW_INTERRUPTION_AFTER`), so "Gestatten Sie …? – Bitte." between
  two parts of one question does not split it; a part right after another interrupter takes that person's earlier
  kind, else `zwischenfrage`. NULL for the main speaker's parts and for every part in a Befragung or Fragestunde,
  where each question and answer is a turn of its own.
- **`speech.interruption_start`**: the first part of the interruption a part belongs to, so a question over two
  parts counts once: count distinct `interruption_start` values, not parts.

### Chair text and decisions

**agenda_item_paragraph** `*id ("<agenda_item_id>/<position>"), agenda_item_id →agenda_item, position, kind (chair | comment | procedural), text, source_url, source_document_id, retrieved_at`
— the text directly under `<tagesordnungspunkt>` outside any `<rede>` and outside a Fragestunde speech: the presidency calling items, putting questions to the vote and reading out results. `procedural` = the `T_*` agenda lines. The roll-call name lists (`AL_Namen`…) are not kept.

Every WP21 Fragestunde (25 agenda items, not just early ones) has no `<rede>` at all: the question, the answer and every Nachfrage are `<p klasse="redner">` paragraphs directly under `<tagesordnungspunkt>`. `parse_protocol._is_fragestunde` detects such an item structurally (no `<rede>`, at least one direct-child `<p klasse="redner">`) and `_split_fragestunde` turns each run starting at one of those paragraphs into a `speech` of `kind = 'fragestunde'`, the same way a normal `<rede>` is split; the presidency's own framing text between them stays `agenda_item_paragraph` (`chair`).

**agenda_sub_item** `*id ("<agenda_item_id>/<label>", e.g. "21/96/6/41b"), agenda_item_id →agenda_item, label ("41b"; Zusatzpunkte "ZP8", "ZP10a"), position (order within the item), title (this sub-item's title lines joined with " | "), drucksache_numbers (JSON array), first_paragraph, last_paragraph (positions in agenda_item_paragraph), source_url, source_document_id, retrieved_at, no_debate (0 | 1)`
— one `<tagesordnungspunkt>` is sometimes a block of many real items voted one by one ("Ich rufe auf die Tagesordnungspunkte 41b bis 41s. Es handelt sich um die Beschlussfassung zu Vorlagen, zu denen keine Aussprache vorgesehen ist."; 33 such items in the 97 sittings to 25 September 2026). Each is announced by a chair paragraph that is only the call-up ("Tagesordnungspunkt 41c:", "Zusatzpunkt 8:", "Wir kommen zu Tagesordnungspunkt 12k:"); `bdf/parse_sub_items.py` cuts the item there, the sub-item taking the procedural title lines and Drucksache lines up to the next call-up. Only an item with at least two distinct call-ups gets sub-items; all other items stay the unit, and the parent row is unchanged either way (title, Drucksachen, ids). Not split: a debated pair or triple ("Tagesordnungspunkte 7a und 7b", call-ups with words after them) and lettered lists that no call-up announces ("40 a) … b) …", Überweisungen im vereinfachten Verfahren). `no_debate` is the chair's statement made before the call-up (or in the sub-item), so the whole block carries it.

**agenda_item_vorlage** `*id ("<agenda_item_id or sub_item_id>/<drucksache_number>"), agenda_item_id →agenda_item, sub_item_id →agenda_sub_item (NULL when the Drucksache is listed on the item itself), drucksache_number, vorgang_id →vorgang, source_url, source_document_id, retrieved_at, via (title | decision)`
— the Drucksachen of `agenda_item.drucksache_numbers` as rows (`via = 'title'`), plus each Drucksache a decision taken under the item names that the item's title does not (`via = 'decision'`, provenance of the decision): mostly the Entschließungsanträge to a bill, debated with it and voted on after it, which the printed title leaves out. An item with sub-items lists each Drucksache under its sub-item (and any Drucksache outside all of them under the item), so one query over `agenda_item_id` sees every Drucksache once. `vorgang_id` is the Vorgang of `vorgang_drucksache` when the Drucksache has exactly one, else NULL; Bundesrat Drucksachen (their numbers collide with the Bundestag's) and Unterrichtungen shared by several Vorgänge (the § 80 GO-BT lists) are left out of that lookup. Rebuilt in full by `ingest_vorlagen` after the DIP ingest.

**decision** `*id, sitting_id →sitting, agenda_item_id →agenda_item, n, position, kind (namentlich | handzeichen), subject, drucksache_number, result (angenommen | abgelehnt | NULL), roll_call_vote_id →roll_call_vote, text, source_url, source_document_id, retrieved_at, sub_item_id →agenda_sub_item, vorgang_id →vorgang, dip_position_id, dip_result`
— one row per decision on substance announced by the chair (`bdf/parse_decisions.py`). Roll-call rows take the id of their `roll_call_vote` (`21/90/7`; `21/90/n<k>` without one), show-of-hands rows `<sitting>/p<paragraph>` (`21/90/p61`): the running number, over the sitting's chair text (`parse_decisions.chair_stream`), of the paragraph that asks the vote ("Wer stimmt dafür?", else its result sentence); a second or third vote asked in the same paragraph gets `-2`, `-3`. The id depends on where the vote stands in the protocol, not on how many decisions were found before it, so a parser that finds or drops a decision leaves the others' ids (and the cards' page addresses) alone; measured against the parser change of #29 (20 decisions found, 8 dropped), all 1,205 decisions both versions find keep their id, where the old count `h<n>` changed 148. An id changes only when the protocol's text changes: when the final version of a preliminary protocol replaces it (its paragraphs are counted anew), or when the chair text is split into paragraphs differently. Until 2026-10 the ids were `<sitting>/h<n>`, counting the decisions of the sitting. `n` counts per kind within the sitting, `position` orders all decisions of the sitting. `text` is the chair's words the row was read from. Procedure (Überweisung, Tagesordnung, Aufsetzung …) and elections are not decisions. `sub_item_id` is the sub-item whose call-up the chair had spoken last where the decision was read (NULL for items without sub-items, and for a decision moved to another item); it also supplies what the chair leaves out: the number of a Sammelübersicht ("Auch diese Sammelübersicht ist angenommen" is 305 under "Tagesordnungspunkt 41h") and a lone Drucksache. `vorgang_id` is the decision's only Vorgang in `decision_vorgang` (below), else NULL.

`dip_position_id`, `dip_result`: the DIP Vorgangsposition (BT, this sitting's Plenarprotokoll) whose `beschlussfassung` names the decision's Drucksache, and its `beschlusstenor`, when exactly one does; `bdf query decision-check` compares them with the protocol.

**decision_vorgang** `decision_id →decision, vorgang_id →vorgang, via` (composite PK)
— the Vorgänge a decision concerns, from the most precise source there is: `roll_call` (the roll call's Vorgang, from DIP's Namentliche Abstimmung paired by page), else `dip_step` (DIP's BT steps in the same sitting whose `beschlussfassung` names one of the decision's Drucksachen), else `drucksache` (every Vorgang of the decision's and its roll call's Drucksachen, with the same exclusions as `agenda_item_vorlage`). `decision.vorgang_id` is its only row, else NULL. Consumers read a decision's Vorgänge here and nowhere else. Rebuilt by `ingest_vorlagen`; inherits provenance from `decision`.

**decision_fraction** `decision_id →decision, fraction, position (yes | no | abstain)` (composite PK)
— fraction positions of show-of-hands decisions as the chair states them; inherits provenance from `decision`. For roll-call votes use `individual_vote`.

`roll_call_vote.agenda_item_id` is its decision's agenda item, else the agenda item of the same sitting listing one of its Drucksachen.

### Interjections

**interjection** `*id ("<speech_id>/<paragraph>/<part>/<actor>"), speech_id →speech, paragraph (speech_paragraph.position), part, kind (beifall | zuruf | gegenruf | lachen | heiterkeit | widerspruch | zustimmung | unruhe | other), actor (fraction | members | person | house | unknown), fraction, person_id →person, name, text (the words of a Zuruf), to_person_id →person, to_name (addressee named in the comment; NULL = the speaker)`

Parsed from the `comment` paragraphs (`bdf/parse_comments.py`) in the same transaction as the protocol's speeches,
so it inherits provenance through `speech_id`. "Beifall bei der SPD sowie bei Abgeordneten der CDU/CSU" is two
rows: `fraction` SPD and `members` CDU/CSU. A named interjection ("Name [Fraktion]: …") is a `zuruf` by a `person`.

### abgeordnetenwatch.de

**aw_profile** `*aw_politician_id, person_id →person (NULL if unmatched), url (public profile), questions, questions_answered (citizen questions on the profile, lifetime totals), source_url, source_document_id, retrieved_at`

**side_job** `*id (aw sidejob id), wahlperiode, person_id →person (via the aw mandate, NULL if unmatched), aw_mandate_id, label (the entry as published), job_title_extra, category (aw's Bundestag Verhaltensregeln category), income_level (Stufe 0..10, NULL if none published), income_range (the Stufe's range as published), income (exact amount if aw has one, NULL otherwise), interval (einmalig | monatlich | jährlich), additional_information, organization_id, organization, city, topics (JSON list of aw topic labels), created, data_change_date, source_url, source_document_id, retrieved_at`
— Nebentätigkeiten (side jobs) reported under the Bundestag's Verhaltensregeln, republished by abgeordnetenwatch as CC0. One row per aw sidejob record; `ingest_side_jobs` replaces a Wahlperiode's rows wholesale, so entries aw withdraws disappear. Facts as published only: no linking of income to speeches or votes, no ranking, no sums across members (`docs/decisions.md`).

### Election (Bundeswahlleiterin)

**constituency** `*id ("btw25/114"), election, number, name, state, seat_party (party whose candidate got the seat; NULL when the winner had no Zweitstimmendeckung), electorate, voters, source_url, source_document_id, retrieved_at`

**constituency_result** `*id ("btw25/114/<group order>/<vote>"), election, constituency_number, group_kind (party | individual), party, vote (1 Erststimme | 2 Zweitstimme), votes, percent, source_url, source_document_id, retrieved_at`

**election_candidacy** `*id ("btw25/<row>"), election, person_id →person (NULL if unmatched), last_name, first_names, birth_year, party, elected_via (constituency | list), constituency_number (won there, or stood there), first_vote_percent (constituency winners), list_state, list_position, occupation, source_url, source_document_id, retrieved_at`

**constituency_municipality** `*id ("btw25/<ags>/<number>"), election, ags (Amtlicher Gemeindeschlüssel, 8 digits), name, district (Kreisname), state, constituency_number, split (1 = the Gemeinde is split across Wahlkreise, one row per Wahlkreis), source_url, source_document_id, retrieved_at`

**mandate_successor** `*id ("btw25/<Bek.-Nr.>"), election, predecessor_person_id →person, predecessor_name, predecessor_party, predecessor_state, predecessor_seat ("LL 003" | "WK 044"), reason (Ablehnung, Mandatsverzicht, Tod, …), person_id →person, name (as printed, "Asghari, Dr. Reza"), birth_year, party, state, seat ("LL 018" | "WK 282"), from_date (Beginn der Mitgliedschaft), source_url, source_document_id ("… Mandatsnachfolger (abgerufen <date>)"), retrieved_at`
— the Bundeswahlleiterin's list "Veränderungen im 21. Deutschen Bundestag" (a one-page PDF, parsed by `parse_wahl.parse_successors`; rows that look like a row but do not parse are reported by ingest). `state` is the Land of the successor's seat, for Nachrücker the Land of the Landesliste; codes as in `mandate.state` (the list's "NRW" is NW). The list lags: on 2026-10-04 it was last changed on 2026-06-23 and has 9 rows, while Katrin Zschau (SPD) votes since 2026-09-24; a successor not yet on it has no row. The Stammdaten lag too (file of 2026-04-29), so for Glaser, Naser, Breilmann and Zschau `mandate` has no WP 21 row; the list is where their Land comes from.

Only the candidates elected on election day are in the Gewählte file; Nachrücker are in `mandate_successor`.
A list member's own first-vote share is the party's `vote = 1` row in `constituency_result`
for their `constituency_number`.

### Portraits and government

**person_photo** `*person_id →person, image_url, credit (photographer / rights holder as printed, without "©"), bio_url (bundestag.de biography; NULL for Commons), local_path (relative to data/raw/), source_url, source_document_id, retrieved_at`
— one portrait per person, replaced wholesale on ingest. MdBs: the bundestag.de biography card; the credit is the
caption of the card's image ("© Sanae Abdi/SPD-Fraktion"). Government members without a card (non-MdB ministers):
the Wikidata P18 image from Commons, credit "<author>, <licence>", `source_url` the Commons file page.

**government_role** `*id, person_id →person, wikidata_qid, name, office, department, kind (kanzler | minister | staatsminister | parl_sts | beamteter_sts), from_date, to_date, source_url, source_document_id, retrieved_at, source_kind (wikidata | stammdaten | protocol)`
— the federal government since 2025-05-06, replaced wholesale on ingest, merged (`bdf/government.py`) from three
sources:
- **wikidata**: one "position held" (P39) statement starting on or after 2025-05-06. `kind` comes from the
  position's class: Bundeskanzler; subclasses of Bundesminister and the Chef des Bundeskanzleramtes → minister;
  Staatsminister (Bund) and the BKM → staatsminister; subclasses of Parlamentarischer Staatssekretär and beamteter
  Staatssekretär. `department` is the statement's "of" qualifier or the position's "directs" (P2389).
- **stammdaten**: a membership of kind other since 2025-05-06 whose function (`role`) is Bundeskanzler(in),
  Bundesminister(in), Staatsminister(in) or Parlamentarische(r) Staatssekretär(in); the institution is the
  department. Exact dates, MdBs only.
- **protocol**: the role printed for a speaker (`speech.speaker_role`, else `person.role` of a non-MdB), parsed into
  kind and department ("Parl. Staatssekretärin bei der Bundesministerin für …", "Bundesminister des Auswärtigen" →
  Auswärtiges Amt, "Staatsminister beim Bundeskanzler" / "für Kultur und Medien" → Bundeskanzleramt; Land offices
  and Beauftragte are not government roles). `from_date`/`to_date` are the first and last sitting that prints the
  role for the person: **evidence, not appointment dates** (consumers: "belegt ab … (Plenarprotokoll)").
  Provenance is the protocol of the first sitting.

One row per person + kind + department (departments compared on a key that ignores "für"/"und", so the
Stammdaten's "Justiz und Verbraucherschutz" meets "Justiz und für Verbraucherschutz"). The row takes dates and
provenance from the best source present, wikidata > stammdaten > protocol, and names it in `source_kind`; `office`
is Wikidata's label, else a generic one ("Parlamentarischer Staatssekretär beim Bundesminister der Finanzen");
`department` is Wikidata's name, else the one read from the protocol, else the Stammdaten institution. `id` is the
Wikidata statement id when Wikidata is the source, else `<source_kind>:<person_id>:<kind>:<department key>`.
An open Kanzler or Bundesminister role from Wikidata or the Stammdaten is closed the day before the protocols first
show another person in that office (`ingest` prints each such inference). `queries.government(on=…)` treats a
protocol row as held after its last evidence until contradicted by a later role of the person or a later holder of
the same single-holder office.

**Reading `to_date` by `source_kind`.** For `wikidata` and `stammdaten`, `to_date` is the end of office (NULL:
still in office; possibly an end inferred from the protocols, see above). For `source_kind = 'protocol'` it means
**"last seen in a protocol"**: the date of the last sitting that prints the role, *not* the end of office, and it is never NULL.
Consumers must not show it as "bis …"; show it as "zuletzt belegt …" and treat the role as current unless another
row contradicts it (`government.held_on`). `bdf query government` prints protocol rows as "belegt <from>..<to>
(Plenarprotokoll)".

**Stale protocol roles.** A protocol-only role that is still counted as held at the newest sitting but whose
`to_date` lies more than 90 days (`government.STALE_AFTER_DAYS`) before that sitting is *stale*: probably ended,
but no source says so. It stays current (no automatic end date); `bdf query stale-roles [--days N]` lists these rows
with `days_since_seen` and `newest_sitting`, and `bdf update` prints them as a warning after ingest. The warning
does not change the exit code. The fix is upstream: once Wikidata or the Stammdaten carry the role, it is no longer
protocol-only; a successor in a Kanzler/Bundesminister office takes it out of the held roles too.

## Entity linking

1. **Speech → person**: `redner/@id` directly. Unknown ids (non-MdB speakers) create a
   `person` row from the XML name block (`is_mdb = 0`, role from `rolle_lang`).
2. **Vote row → person**: `(last_name, first_name)` against persons holding a mandate in
   that Wahlperiode, then against all known persons (Nachrücker without a mandate row yet);
   Ortszusatz "(Altötting)" and name prefixes ("dos", "von") are stripped; first-name
   fallback to the first given name; a small alias table in code for known spelling
   differences. Unmatched rows keep `person_id NULL` and are reported by `ingest`.
3. **DIP person → person**: `/person?f.wahlperiode=21` list, matched on
   `(nachname, vorname)` after normalisation with the same matcher as the votes.
   Unmatched DIP persons (mostly non-MdBs) are counted; `drucksache_author.person_id` stays NULL.
4. **abgeordnetenwatch politician → person**: `(last_name, first_name, year_of_birth)`
   against Stammdaten; `ext_id_bundestagsverwaltung` is only compared and disagreements
   are reported (it is wrong for ~9 % of WP21 members, see `landscape.md` §1.6).
5. **Roll-call vote → Drucksache/Vorgang**: DIP `vorgangsposition` on the sitting date
   whose `beschlussfassung.abstimmungsart = "Namentliche Abstimmung"`; ordered by
   protocol page, paired with the votes of that sitting ordered by `Abstimmnr` when the
   counts match; otherwise a Drucksache number found by regex in the vote title;
   otherwise unlinked (reported).
6. **Biography card → person**: printed name ("Aken, Jan van", "Schneider (Erfurt), Carsten", titles stripped)
   with the shared name index; if that is ambiguous, a unique WP member with the same surname, first given name
   and fraction. A card whose fraction differs from the store's is reported.
7. **Government role → person**: Stammdaten and protocol roles carry the person id; Wikidata roles: `person.wikidata_qid` (from abgeordnetenwatch), then the full name with an equal
   or unknown birth date (non-MdB speakers from the protocols have none), then the shared name index with the
   birth year. A match by name writes the QID onto the person. The rest get a person row with `id` = QID and
   `is_mdb = 0`; such a row is deleted again once the person is matched.

## Incremental updates

- Unit of work is a date range (a sitting week). `fetch` downloads anything in the range
  that is not already on disk (files are immutable; a re-fetch overwrites only with
  `--force`). `ingest` upserts by primary key, so re-running is safe and idempotent.
- Stammdaten and DIP persons are re-fetched whole (small) and upserted.
- Corrections to protocols are rare; `fetch --force` + `ingest` handles them.

### Health report

After every ingest `update` takes a snapshot of the store (`bdf/health.py`): the rows of every table and the known
problem counts, saved as `data/health/<date>.json` and compared with the newest earlier snapshot. The comparison is
printed in the run's log; `bdf health` prints it for the current store without saving. The run fails (exit code 1;
systemd reports it to Gatus, which alerts via ntfy) only when something clearly got worse:

- a table that had rows is empty or gone, or lost more than 5 % of its rows (rows normally only grow);
- a problem count rose by more than its threshold since the last snapshot:

| Problem | Fails at a rise of |
|---|---|
| `protocol_gaps`: Beratungen without an agenda item, not counting preliminary protocols without a PDF part | 10 |
| `decision_results_disputed`: decisions whose result DIP records differently (`bdf query decision-check`) | 5 |
| `decisions_missing`: DIP decisions with no decision row (`decision-check`) | 10 |
| `unmatched_votes`: roll-call vote rows without a person | 100 |
| `unlinked_roll_calls`: roll-call votes without a Vorgang | 5 |
| `vorlagen_without_vorgang`: Drucksachen on agenda items without a Vorgang (DIP links new ones days later) | 150 |
| `askers_without_person`: DIP persons asking Fragen without a person | 10 |
| `unmatched_elected`, `unmatched_successors`: candidates and Mandatsnachfolger without a person | 3 |

`protocol_gaps_preliminary`, `preliminary_sittings`, `stale_roles`, `placeholder_speakers` and `unresolved_authors`
are reported only. A first snapshot, a new table and a new problem count never fail. A source that cannot be
fetched fails the run as before. On 2026-10-04 the live store had 23 protocol gaps, 17 preliminary sittings, no
unmatched votes and no unlinked roll calls, 188 Vorlagen without a Vorgang, 30 unresolved DIP authors.

### Preliminary protocols

bundestag.de serves a protocol's XML at its final URL (`btp/21/21031.xml`) before the final version exists. The
preliminary version carries a note, "Der gesamte und damit endgültige Stenografische Bericht der 31. Sitzung wird
am … veröffentlicht", and may end hours before the sitting did. On 2026-10-01, 17 of 97 WP 21 protocols were still
preliminary, some for a year (21/14, announced for 2025-07-01); 21/31 ends after TOP 22, the PDF goes on to TOP 31.
DIP's `plenarprotokoll-text` was no help: for 21/31 it is the same preliminary text. The PDF at the same address
(`btp/21/21031.pdf`) is the final version.

- **Detect** (`protocol_status.status`): the note (also "vorläufiger Stenografischer Bericht") anywhere in the
  document text, and the announced date. Stored on `sitting`.
- **Re-fetch** (`fetch_bundestag.refetch_preliminary`, run by `update` on every run, by hand with
  `bdf fetch protocols --preliminary`): every preliminary XML on disk is downloaded again and replaces the file;
  `ingest` then replaces the sitting's speeches, items and paragraphs as for any re-ingested protocol (agenda items
  it no longer has are dropped).
- **PDF part** until then (`fetch_bundestag.fetch_preliminary_pdfs`, same runs): the PDF of each preliminary
  protocol, fetched again every run and written only when it changed. `parse_protocol.parse` hands a preliminary
  XML with a PDF beside it to `protocol_pdf.merge`, which reads the PDF column by column (pdfplumber; font size and
  weight tell speaker lines, body text, agenda titles and comments apart), finds where the XML's text ends (its last
  ~300 letters, normalized, in the PDF's text) and appends what follows as the elements the XML would have had:
  `<tagesordnungspunkt>` at each call-up ("Ich rufe den Tagesordnungspunkt 29 auf:") with `T_*` title paragraphs,
  `<rede>` with `<redner>`, `<name>` for the presidency, `<kommentar>`, `<p>`; it stops at "(Schluss: … Uhr)" and
  leaves out the printed name lists of roll-call votes. Speakers get the id another protocol's XML gives the same
  printed line or name; one never seen gets `pdf-<name>` (a non-MdB person row) and ingest names them. Everything
  downstream (decisions, sub-items, interjections, Vorlagen) reads these rows like any other. Reading a PDF takes
  about 30 s; the lines are kept beside it (`<pdf>.lines.json`) while the PDF is unchanged.
- **Check** (`bdf query protocol-gaps`): per sitting that is preliminary or has one, every Beratung DIP places in
  its Plenarprotokoll (BT `vorgang_position` of kind Plenarprotokoll with pages, position "…Beratung…") that no
  agenda item or sub-item of the sitting carries (a Drucksache of the Vorgang, or an `agenda_item_vorlage` row for
  it), with its cause: `preliminary` (no PDF part read), `after_xml_end` (after the XML's end, so the PDF part
  missed it) or `in_protocol` (the protocol has it: another Drucksache on the item, or the parser misses it).
  An item's Drucksachen include those only a decision under it names (`via = 'decision'`); DIP's
  "Geschäftsordnungsantrag …" positions are left out (a motion on the agenda, not a Beratung of the Vorgang).

  **Known gaps** (live store, 2026-10-04: 23 Beratungen, none a parser miss):

  | Cause | n | Sittings |
  |---|---|---|
  | A Vorlage from WP 20: the item names the WP 20 Drucksache, DIP's new WP 21 Vorgang only later ones | 10 | 21/6 (Wehrbeauftragte 20/15060), 21/10 (Entlastung 20/12195), 21/14, 21/16–21/19 (Finanzplan 20/12401), 21/40, 21/50, 21/65 |
  | Constitution and procedure without a Drucksache on the item: the constituent sitting, the Kanzlerwahl, a number of members fixed at the opening; the AfD's amendments to the Geschäftsordnung (21/4, 21/5), which DIP files under Vorgänge with only 21/2196 | 7 | 21/1 (5), 21/2, 21/21 |
  | A change of committee referral announced at the opening, not an agenda item | 3 | 21/49, 21/52, 21/82 |
  | Entschließungsanträge to the budget, debated with their Einzelplan (items without Drucksache), voted on in 21/25 | 2 | 21/23 |
  | An Anlage: a corrected vote from sitting 13 | 1 | 21/15 |

  Seven of these Vorgänge (three WP 20 reports, four procedure items) have no `vorgang` row: Vorgänge are
  fetched through WP 21 Drucksachen, and these have none.

## CLI

```
bdf fetch stammdaten
bdf fetch protocols --wp 21 --from 88 --to 90
bdf fetch protocols --wp 21 --preliminary           # the preliminary protocols on disk again, and their PDFs
bdf fetch votes --from 2026-07-06 --to 2026-07-10
bdf fetch dip --from 2026-07-06 --to 2026-07-10     # drucksachen, authors, vorgänge, vorgangspositionen, persons
bdf fetch aw --wp 21
bdf fetch photos                                    # bundestag.de biography list + portraits
bdf fetch government                                # Wikidata roster + Commons portraits
bdf ingest                                          # everything under data/raw → sqlite
bdf health                                          # rows per table and problem counts against the last update's snapshot
bdf query speeches   --person 11004006 --from … --to …
bdf query votes      --person 11004006 --from … --to …
bdf query drucksachen --person 11004006 --from … --to …
bdf query government [--date 2026-09-28]           # roles, optionally those held on a day
bdf query stale-roles [--days 90]                   # protocol-only roles not printed for >90 days before the newest sitting
bdf query photos     [--missing]                    # portraits with credit, or sitting members without one
bdf query decisions  --sitting 21/90                # decisions announced by the chair, fraction positions / roll-call totals
bdf query protocol-gaps                             # preliminary protocols, DIP Beratungen without an agenda item
bdf query decision-check                            # decisions DIP records with another result, DIP decisions not found
bdf query corpus     --from … --to …                # JSONL: one clean speech per line with speaker id, fraction, date, source
bdf export data/export                              # open data: one CSV.gz per table, datapackage.json, README.md
```

Every query prints the source pointer on each row.

## Open-data export

`bdf export <dir>` writes every table as `<table>.csv.gz` (UTF-8, header row, rows ordered by primary key, NULL as
an empty field, gzip without a timestamp so unchanged data gives identical files), a Frictionless
`datapackage.json` and a German `README.md` with the attribution each source requires. The data package lists per
table the fields with type (`string` / `integer` / `number` from the SQLite type) and the description from the
`-- …` comment in `db.SCHEMA` (parsed at export time, so the schema stays the only place to document a column),
the primary key, the single-column foreign keys, and the sources and licences (`export.TABLE_SOURCES`); at the top
the created timestamp and `latest_sitting_date`. Provenance columns are exported; `person_photo.local_path` (a path
on the build machine) is not, and no images are. The export is built in a hidden sibling directory and renamed into
place, so a failed run keeps the previous export. On the dev store (95 sittings) it is ~37 MB, two thirds of it
`speech_paragraph` and `speech`, and takes ~10 s; `frictionless validate datapackage.json` passes.
