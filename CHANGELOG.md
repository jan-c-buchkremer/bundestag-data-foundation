# Changelog

One entry per release (`docs/release.md`). Each starts with one sentence: what can a reader do now that they could
not before? If that sentence is hard to write, the release is not a finished vertical slice yet.

## Unreleased

A consumer can now read every Mündliche Frage and its answer – spoken in the Fragestunde or given in writing – follow
each exchange of a Befragung der Bundesregierung and a Fragestunde, and say which committees a Vorgang was referred
to.

- New table `question_turn` (additive): the role of every turn of a Befragung der Bundesregierung (`einleitung`,
  `frage`, `antwort`, `nachfrage`, `zusatzfrage`) and the question it belongs to (`thread_id`), from the
  presidency's words in the protocol; `vorgang_id` is reserved for the Fragestunde. In the export with source
  bundestag.de.
- New table `question_text` (additive): the text of every Mündliche Frage the Plenarprotokolle print (`frage`) and
  of its written answer (`antwort`), with the DIP Vorgang, the answerer and the Fragen-Drucksache; tables in an answer
  in the new table `question_table`, as cells. In the export with sources bundestag.de and DIP.
- `question_turn` covers the Fragestunde too: each answer, Nachfrage and Zusatzfrage with the question it belongs to
  and its DIP Vorgang (`vorgang_id`).
- Health report: new count `questions_without_vorgang`.
- New table `vorgang_referral` (additive): one row per committee per Vorgangsposition, with DIP's committee name and
  short name, `lead` (federführend) and `kind` (ueberweisungsart); in the export with source DIP.

## v0.1.0 (2026-10-04)

Anyone can download the data behind plenar-radar.de as open data – members, speeches, votes and decisions, Drucksachen
and Vorgänge, election results – with the source of every row.

The first tag: the store as it stands after the merges of 2026-10-04, first built by the nightly run of 2026-10-05.
