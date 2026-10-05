# Changelog

One entry per release (`docs/release.md`). Each starts with one sentence: what can a reader do now that they could
not before? If that sentence is hard to write, the release is not a finished vertical slice yet.

## Unreleased

A consumer can now say which committees a Vorgang was referred to and which one leads it – EU-Vorlagen and bills
alike – from DIP's Überweisung on each Vorgangsposition.

- New table `vorgang_referral` (additive): one row per committee per Vorgangsposition, with DIP's committee name and
  short name, `lead` (federführend) and `kind` (ueberweisungsart); in the export with source DIP.

## v0.1.0 (2026-10-04)

Anyone can download the data behind plenar-radar.de as open data – members, speeches, votes and decisions, Drucksachen
and Vorgänge, election results – with the source of every row.

The first tag: the store as it stands after the merges of 2026-10-04, first built by the nightly run of 2026-10-05.
