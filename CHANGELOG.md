# Changelog

One entry per release (`docs/release.md`). Each starts with one sentence: what can a reader do now that they could
not before? If that sentence is hard to write, the release is not a finished vertical slice yet.

## Unreleased

A consumer can now list what "heute im bundestag" (hib) reported on each day of the Wahlperiode, find the hib
items on a Drucksache, and see which committee an Ausschuss or Anhörung report is about.

- New tables `hib_item` and `hib_drucksache` (additive): every hib article of the Wahlperiode with its number,
  date, Ressort, kind, author code and, for committee reports, the committee as the text names it; the
  Drucksachen each article links. Fetched by `bdf fetch hib` and in the nightly update (newest first, until a
  list page holds only known items). In the export with source hib, without the article text (protected,
  `docs/licences.md`).
- Health: `hib_reports_without_committee`.
- `docs/licences.md`: the bundestag.de terms corrected (the Impressum allows private use only unless a rule says
  otherwise); a section on hib.

## v0.2.0 (2026-10-06)

A consumer can now read every Kleine and Große Anfrage, every Mündliche and Schriftliche Frage with its answer –
questions, preliminary remarks, tables – follow each exchange of a Befragung der Bundesregierung and a Fragestunde, and
say which committees a Vorgang was referred to.

- New table `question_turn` (additive): the role of every turn of a Befragung der Bundesregierung (`einleitung`,
  `frage`, `antwort`, `nachfrage`, `zusatzfrage`) and the question it belongs to (`thread_id`), from the presidency's
  words in the protocol; `vorgang_id` is reserved for the Fragestunde. In the export with source bundestag.de.
- New table `question_text` (additive): the text of every Mündliche Frage the Plenarprotokolle print (`frage`) and of
  its written answer (`antwort`), with the DIP Vorgang, the answerer and the Fragen-Drucksache; tables in an answer in
  the new table `question_table`, as cells. In the export with sources bundestag.de and DIP.
- `question_text` and `question_table` cover the Kleine and Große Anfragen too, read from the PDF of the answer: the
  askers' and the government's preliminary remarks, every question (sub-questions answered one by one as "3a") and its
  answer, annexes; tables without ruling are marked as not read, with their page.
- `question_text` and `question_table` cover the Schriftliche Fragen too, read from the weekly Sammeldrucksachen
  (fetched with the answers to Anfragen): every question with its asker and answer, linked to its DIP Vorgang.
- New table `question_parse` (additive): per Vorgang of an Anfrage, Mündliche or Schriftliche Frage whether its texts
  are complete, partial, unanswered, or answered but not read (failed).
- New raw source: the PDFs of the answers to Kleine and Große Anfragen and of the Sammeldrucksachen of Schriftliche
  Fragen (`bdf fetch answers`, and in `update` after DIP), about 1.2 GB; the first run downloads all of them (about 25
  minutes) and reads them (about 95 minutes).
- `question_turn` covers the Fragestunde too: each answer, Nachfrage and Zusatzfrage with the question it belongs to and
  its DIP Vorgang (`vorgang_id`).
- Health report: new count `questions_without_vorgang`.
- The answer PDFs are read page by page, each page closed after use: the largest answer (21/2974) needs
  385 MB instead of more than 6 GB (#48).
- New table `vorgang_referral` (additive): one row per committee per Vorgangsposition, with DIP's committee name and
  short name, `lead` (federführend) and `kind` (ueberweisungsart); in the export with source DIP.

## v0.1.0 (2026-10-04)

Anyone can download the data behind plenar-radar.de as open data – members, speeches, votes and decisions, Drucksachen
and Vorgänge, election results – with the source of every row.

The first tag: the store as it stands after the merges of 2026-10-04, first built by the nightly run of 2026-10-05.
