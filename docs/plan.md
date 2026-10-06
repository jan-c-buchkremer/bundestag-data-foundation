# Plan

## Current goal

By the v0.2.0 release (target: the weekend of 2026-10-10), anyone using plenar-radar.de or the open-data export can
rely on the foundation: the facts it holds are checked against DIP, the gaps it cannot close are written down with
numbers, the schema stays as it is, and the nightly run reports by itself when something breaks. After that the
foundation is frozen: it changes only when a concrete page or question needs data that is missing.

v0.2.0 also carries Fragen as text (below; decided 2026-10-06): the research platform's Fragen subpages are the page
that needs it, and the release waits for it.

Every change is reviewed against this goal. Ideas that don't serve it go on "Not now", not into code.

## Steps to v0.2.0

1. **Merge and release the open work.** Foundation #23 (side jobs), #24 (speaker group, fraction vocabulary),
   #25 (speech parts), #26 (decision_vorgang, `decision-check`), #27 (person_alias); the release switch-over
   (`docs/release.md`, tag `v0.1.0` as the baseline, infra `:release`). The consumers (research platform #43–#46,
   radar #13) after one nightly ingest.
2. **Fix the known errors.**
   - The 10 decisions whose result DIP records differently (`bdf query decision-check`), e.g. Wahlvorschläge in 21/25
     shown as "angenommen" with only the AfD in favour: check each against the protocol, fix the parser.
   - The 110 DIP decisions without a decision row: tell missing decisions from elections and procedure; fix what the
     parser misses, leave the rest out of the check.
   - The 31 Beratungen without an agenda item (`bdf query protocol-gaps`): cause per case, fixed or documented.
   - The vote lists' name matcher can match a `pdf-` placeholder person: leave placeholders out of it.
3. **Store what existing pages already ask for** (requirements in the research platform's `docs/plan.md` 11.8 and 12.5):
   - the asker of each Schriftliche and Mündliche Frage (DIP's `vorgangsbezug` on the "Frage" activities, already in
     `data/raw/dip/aktivitaet/`, dropped by the ingest today);
   - the Ressort of a Frage, from its Sammeldrucksache;
   - the Land of a Nachrücker's list mandate.
4. **Report problems automatically.** A health report after each ingest (protocol gaps, `decision-check`, stale roles,
   unmatched persons and Drucksachen, rows per source against the night before). The run fails, and Gatus alerts via
   ntfy, only when something clearly gets worse (a source at zero rows, a count jumping past a threshold).
5. **Write the schema contract.** In `docs/design.md`: which tables and columns consumers may rely on and what NULL
   means in each; the export's datapackage with sources for the new tables (`party_fraction`, `decision_vorgang`,
   `person_alias`); a "Known gaps" section with the measured numbers. **Stable decision ids:** a show-of-hands
   decision's id counts its position in the sitting (`21/31/h7`), so a decision found later renumbers the ones after
   it, and their page addresses with them (seen in #29); an id from the protocol position instead. Release v0.2.0.

## Status (2026-10-06)

- Step 1: foundation #23–#27 merged; release switch-over done (release workflow #37, tag `v0.1.0`, infra #12: the
  live pipeline runs `:release`, first built by the nightly run of 2026-10-05). Open: the consumers (research
  platform #43–#46, radar #13).
- Step 2: done. Decision results against DIP 10 → 1, DIP decisions without a row 110 → 3 (#26, #29); protocol gaps
  31 → 23, each with its cause (#31); the vote matcher leaves `pdf-` placeholders out (#30).
- Step 3: done. `question_activity` (asker and Ressort of every Frage, #32), `mandate_successor` (the Land of a
  Nachrücker, #33). The research platform still has to read them.
- Step 4: done. `bdf/health.py`, failing the run on a clear regression (#34; decision-check counts in #26).
- Step 5: done. Stable decision ids (#35, with research platform #50); the schema contract, the export's sources for
  every table and "Known gaps" in `docs/design.md` (#36). The version stays 0.x like the other repos (decided
  2026-10-04, #38); the contract says what is stable.
- Also in v0.2.0: `vorgang_referral` (#40), the committees a Vorgang was referred to, for the research platform's
  EU-Vorlagen page (its #56: the committee each EU-Vorlage went to). Additive, under "Unreleased" in the CHANGELOG.
- Left: Fragen as text (below), then release v0.2.0 after a clean nightly run with all of it.

## Fragen as text (in v0.2.0)

The page that needs it: the Fragen subpages of the research platform (its `docs/plan.md`, goal 4 "Fragen as
text"). A reader reads every question to the government and its answer on the site, not in a PDF. The store has the
Vorgänge, askers, Ressorts and Drucksachen, but no texts and no structure of the spoken turns. Measured on a store copy
(sittings to 2026-09-25):

| Kind | In the store | Missing |
|---|---|---|
| Regierungsbefragung | 26 agenda items, 3,047 turns as `speech.kind = rede` | which turn is the minister's statement, a question, an answer, a follow-up, and which belong together |
| Mündliche Fragen | 1,624 Vorgänge; 1,536 turns as `kind = fragestunde`; asker and answerer in `question_activity` | the question text (only in the 26 "Fragen" Drucksachen); the link from a turn to its Frage |
| Schriftliche Fragen | 9,038 Vorgänge; asker, Ressort, numbers in `question_activity` | question and answer text, split out of 78 Sammeldrucksachen; who answered and when |
| Kleine / Große Anfragen | 2,749 / 14 Vorgänge, the Fraktion in `drucksache.originator_groups`, the answer Drucksache | every text: preliminary remarks, numbered questions, answers, tables |

**The contract.** Four new tables, additions under the schema contract (a minor release). The platform parses
nothing itself; what it misses comes back here as a requirement.

- **`question_turn`**, the spoken questions from the protocol: `speech_id` (PK), `role` (`einleitung` | `frage` |
  `antwort` | `nachfrage` | `zusatzfrage`), `thread_id` (the first turn of a question; the turns that follow on it
  share it), `vorgang_id` (Fragestunde: the DIP Mündliche Frage; NULL in the Regierungsbefragung). Who was questioned
  in a Regierungsbefragung is read from the turns with role `einleitung` or `antwort`, not stored apart.
- **`question_text`**, the written texts, from DIP's `drucksache-text` or the PDF: `id`, `vorgang_id`,
  `drucksache_id`, `position`, `part` (`vorbemerkung_fragesteller` | `frage` | `vorbemerkung_bundesregierung` |
  `antwort`), `number` ("1", "3a", or the number in a Sammeldrucksache), `text`, `answerer_person_id` and
  `answer_date` for answers.
- **`question_table`**, a table inside an answer, as cells (JSON), referring to its `question_text` row, so the text
  holds no broken tables; a table that cannot be extracted gets a row saying so, with its page.
- **`question_parse`**, one row per Vorgang: `status` (`complete` | `partial` | `unanswered` | `failed`), so a page
  can tell "not answered yet" from "answered, but not read".

**Order**, one kind at a time, each merged and checked on the preview before the next; all four in v0.2.0:

1. `question_turn` for the Regierungsbefragung. Done: 3,047 turns of 26 Befragungen, rules and accuracy in
   `docs/design.md` "Question turns".
2. `question_turn` for the Fragestunde, linked to the DIP Frage; `question_text` for the Mündliche Fragen. Done,
   from the protocols rather than the "Fragen" Drucksachen: they print every question, called in the Fragestunde or
   answered in writing in the annex, and the written answers too; `question_table` came with them (74 tables in
   answers). 1,613 questions, 1,580 linked to their DIP Vorgang; `docs/design.md` "Question texts".
3. `question_text`, `question_table`, `question_parse` for Kleine and Große Anfragen. First measure on 20 random
   Anfragen how DIP's `drucksache-text` compares to the PDF, tables above all. Measured 2026-10-06 on the answers
   to 20 random Kleine Anfragen (the Antwort-Drucksache reprints every question and preliminary remark):
   - DIP's text is the PDF's text layer, line by line. It loses what tells a question from its answer (the
     question is set smaller: 9.6 pt against 10.7 pt, in all 20), flattens tables into lines of numbers ("deutsch
     5 711 5 375 1 493 …", ambiguous with spaces as thousands separators) and puts page footers mid-text.
   - The PDF read with pdfplumber gives both: font sizes, and tables with correct cells (7 of 20 answers have tables,
     160 in all, every one ruled; no numeric lines left outside them). 40 tables continue on the next page and need
     joining; blank padding pages carry nothing; no answer was a scan.
   - So the texts come from the PDF, not from `drucksache-text`. That means fetching the PDF of every answer:
     2,660 now, about 1.1 GB at about 420 KB each, from dserver.bundestag.de at the DIP rate (2 per second, about 25
     minutes once), then only the new ones each night. Reading them takes about 80 minutes once (cached after).
   Done (`bdf/parse_answers.py`, `docs/design.md` "Question texts"): on 149 random answers and the 7 answers to
   Große Anfragen online, every answer gives its Anfrage, ministry and date and its questions 1 … N, each with an
   answer; 108 of 1,542 tables (without ruling) are marked as not read, with their page. `question_parse` came
   with it, for the Mündliche Fragen too.
4. `question_text` split out of the Schriftliche Fragen Sammeldrucksachen.

## Not now

Ideas that don't serve the current goal. Each names what it would give a reader.

- **Lobbyregister** (the lobby half of roadmap #17; API key since 2026-10-04): who lobbies on which topic, next to
  the side jobs. A new source with its own duties (personal data, full replacement on every pull, no republishing of
  Stellungnahmen); its own release once a research platform page needs it.
- **Place mentions in speeches** (gazetteer and a small local model, designed in the research platform's plan 11.8):
  which speeches talk about my Wahlkreis.
- **Postcode search** (OpenPLZ): find my Wahlkreis by postcode.
- **Earlier Wahlperioden**, and search across periods: what did a member say about a topic over the years.
- **Initiator groups of Vorgänge** (`vorgang.initiator_groups`): the radar could drop its own name mapping.
- Stable topic ids are the radar's task, not the foundation's.
