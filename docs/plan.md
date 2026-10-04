# Plan

## Current goal

By the v0.2.0 release (target: the weekend of 2026-10-10), anyone using plenar-radar.de or the open-data export can
rely on the foundation: the facts it holds are checked against DIP, the gaps it cannot close are written down with
numbers, the schema stays as it is, and the nightly run reports by itself when something breaks. After that the
foundation is frozen: it changes only when a concrete page or question needs data that is missing.

Every change is reviewed against this goal. Ideas that don't serve it go on "Not now", not into code.

## Steps to v0.2.0

1. **Merge and release the open work.** Foundation #23 (side jobs), #24 (speaker group, fraction vocabulary),
   #25 (speech parts), #26 (decision_vorgang, `decision-check`), #27 (person_alias); the release switch-over
   (`docs/release.md`, tag `v0.1.0` as the baseline, infra `:release`). The consumers (cards #43–#46, landscape #13)
   after one nightly ingest.
2. **Fix the known errors.**
   - The 10 decisions whose result DIP records differently (`bdf query decision-check`), e.g. Wahlvorschläge in 21/25
     shown as "angenommen" with only the AfD in favour: check each against the protocol, fix the parser.
   - The 110 DIP decisions without a decision row: tell missing decisions from elections and procedure; fix what the
     parser misses, leave the rest out of the check.
   - The 31 Beratungen without an agenda item (`bdf query protocol-gaps`): cause per case, fixed or documented.
   - The vote lists' name matcher can match a `pdf-` placeholder person: leave placeholders out of it.
3. **Store what existing pages already ask for** (requirements in the cards' `docs/plan.md` 11.8 and 12.5):
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

## Status (2026-10-04)

- Step 1: foundation #23–#27 merged; release switch-over done (release workflow #37, tag `v0.1.0`, infra #12: the
  live pipeline runs `:release`). Open: the consumers (cards #43–#46, landscape #13) after the nightly ingest.
- Step 2: done. Decision results against DIP 10 → 1, DIP decisions without a row 110 → 3 (#26, #29); protocol gaps
  31 → 23, each with its cause (#31); the vote matcher leaves `pdf-` placeholders out (#30).
- Step 3: done. `question_activity` (asker and Ressort of every Frage, #32), `mandate_successor` (the Land of a
  Nachrücker, #33). The cards still have to read them.
- Step 4: done. `bdf/health.py`, failing the run on a clear regression (#34; decision-check counts in #26).
- Step 5: stable decision ids (#35, with cards #50); the schema contract, the export's sources for every table and
  "Known gaps" in `docs/design.md`. Left: release v0.2.0 after a clean nightly run. The
  version stays 0.x like the other repos (decided 2026-10-04); the contract says what is stable.

## Not now

Ideas that don't serve the current goal. Each names what it would give a reader.

- **Lobbyregister** (the lobby half of roadmap #17; API key since 2026-10-04): who lobbies on which topic, next to
  the side jobs. A new source with its own duties (personal data, full replacement on every pull, no republishing of
  Stellungnahmen); its own release once a cards page needs it.
- **Place mentions in speeches** (gazetteer and a small local model, designed in the cards' plan 11.8): which speeches
  talk about my Wahlkreis.
- **Postcode search** (OpenPLZ): find my Wahlkreis by postcode.
- **Full text of Kleine Anfragen and Schriftliche Fragen** (from the PDFs): read and search what was asked.
- **Earlier Wahlperioden**, and search across periods: what did a member say about a topic over the years.
- **Initiator groups of Vorgänge** (`vorgang.initiator_groups`): the landscape could drop its own name mapping.
- Stable topic ids are the landscape's task, not the foundation's.
