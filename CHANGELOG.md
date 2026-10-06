# Changelog

One entry per release (`docs/release.md`). Each starts with one sentence: what can a reader do now that they could
not before? If that sentence is hard to write, the release is not a finished vertical slice yet.

## Unreleased

A consumer can now follow each exchange of a Befragung der Bundesregierung – which turn asks, which answers, which
follow-ups belong to which question – and say which committees a Vorgang was referred to.

- New table `question_turn` (additive): the role of every turn of a Befragung der Bundesregierung (`einleitung`,
  `frage`, `antwort`, `nachfrage`, `zusatzfrage`) and the question it belongs to (`thread_id`), from the
  presidency's words in the protocol; `vorgang_id` is reserved for the Fragestunde. In the export with source
  bundestag.de.
- New table `vorgang_referral` (additive): one row per committee per Vorgangsposition, with DIP's committee name and
  short name, `lead` (federführend) and `kind` (ueberweisungsart); in the export with source DIP.

## v0.1.0 (2026-10-04)

Anyone can download the data behind plenar-radar.de as open data – members, speeches, votes and decisions, Drucksachen
and Vorgänge, election results – with the source of every row.

The first tag: the store as it stands after the merges of 2026-10-04, first built by the nightly run of 2026-10-05.
