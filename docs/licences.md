# Licences and terms of the data sources

Status 2026-09-21. This is a summary for engineering purposes, not legal advice.
Before a public broadcaster publishes anything built on this store, have the original
terms checked.

## Summary table

| Source | What we take | Terms | Attribution required | Commercial use | Verdict |
|---|---|---|---|---|---|
| bundestag.de Open Data — Plenarprotokolle XML/PDF | speeches, agenda, sittings | amtliche Werke (§ 5 Abs. 2 UrhG); bundestag.de Nutzungsbedingungen | "Deutscher Bundestag", document number (BT-PlPr. 21/94) | yes for reporting; **not for advertising** | permissive with attribution |
| bundestag.de Open Data — MdB Stammdaten XML | persons, mandates, memberships | bundestag.de Nutzungsbedingungen | "Deutscher Bundestag" | as above | permissive with attribution |
| bundestag.de — Namentliche Abstimmungen XLSX/PDF | roll-call votes, individual votes | as above | "Deutscher Bundestag" | as above | permissive with attribution |
| DIP API | Drucksachen, Vorgänge, authorship, persons | DIP Nutzungsbedingungen (27 Feb 2023, PDF in this folder) | "Deutscher Bundestag/Bundesrat – DIP" + BT-Drs./BT-PlPr. number; commercial use must note that the data is free at dip.bundestag.de | yes, explicitly | permissive with attribution |
| abgeordnetenwatch.de API v2 | cross-IDs, mandates, side jobs (Nebentätigkeiten), (optionally) polls/votes | **CC0 1.0** | none (courtesy attribution recommended) | yes | public domain |
| Bundeswahlleiterin — Open Data BTW 2025 | elected candidates, results per Wahlkreis, Gemeinden per Wahlkreis | **Datenlizenz Deutschland – Namensnennung 2.0** | "© Die Bundeswahlleiterin, Wiesbaden 2025" | yes | permissive with attribution |
| bundestag.de — MdB biography portraits | one portrait per MdB | bundestag.de Nutzungsbedingungen; photos are third-party works | the credit printed under each photo (`person_photo.credit`) | not verified per photo | show with credit; rights not checked |
| Wikidata | government roster (offices, dates, departments, birth dates) | **CC0 1.0** | none | yes | public domain |
| Wikimedia Commons | portraits of government members without a bundestag.de portrait | per file, mostly CC BY-SA 4.0 | author + licence (`person_photo.credit`) and a link to the file page (`source_url`) | yes, share-alike on the image | permissive with attribution |

Nothing in the store depends on a share-alike or non-commercial source. Data from
Open Discourse (CC0), GermaParl (CC BY-SA), Machtblick (AGPL code) is **not** used;
only ideas were borrowed (see `landscape.md`).

## bundestag.de (Plenarprotokolle, Stammdaten, Namentliche Abstimmungen)

The Open Data page states that the files "können zur maschinellen Weiterverarbeitung
genutzt werden" but names no licence. The site-wide Nutzungsbedingungen
(https://www.bundestag.de/nutzungsbedingungen) say, in substance:

- material may be used free of charge for parliamentary reporting and for educational
  and cultural purposes;
- it may not be used for commercial or advertising purposes ("gewerbliche oder
  kommerzielle Werbezwecke");
- the source must be given as "Deutscher Bundestag";
- the Bundestag accepts no liability for third-party rights (e.g. photos).

Plenarprotokolle and Drucksachen are official works under § 5 Abs. 2 UrhG (the DIP terms
say so explicitly for the PDFs): free to use, with source attribution and without
altering the text (§ 62 UrhG). Excerpts that are recognisable as excerpts are allowed;
annotations must be marked as such. Our store keeps the raw files unchanged and stores
extracted text with a pointer to the document — that is an excerpt with attribution.

Personal data: the Stammdaten contain biographical data published by the Bundestag for
public use (birth date, place, profession, religion, family status). Store what the
applications need; the fact-sheet application should think twice before displaying
religion or family status.

## DIP (Dokumentations- und Informationssystem für Parlamentsmaterialien)

Source: "Nutzungsbedingungen für das DIP", Deutscher Bundestag, Parlamentsdokumentation,
Berlin 27 February 2023 — `docs/nutzungsbedingungen_dip.pdf`. Key points:

1. Data is provided free of charge (Nr. 1).
2. Machine access must use the API with a valid key. The public key is time-limited and
   may be rotated at short notice; a **personalised, permanently valid key** can be
   requested by informal e-mail to parlamentsdokumentation@bundestag.de, giving a valid
   e-mail address, a contact person and, if applicable, the institution (Nr. 3).
3. For use beyond personal use, including commercial reuse and redistribution (Nr. 4):
   - a. PDFs of Drucksachen and Plenarprotokolle are amtliche Werke (§ 5 Abs. 2 UrhG)
     and must not be altered; recognisable excerpts with source are allowed; markings
     and annotations are allowed if marked as changes.
   - b. **Machine-readable data from the API "dürfen umfassend in jeglicher Form genutzt
     und weiterverarbeitet werden"**; the same applies to data extracted from single PDFs.
     Redistribution must carry a source attribution; changes must be marked.
   - c. Attribution: "Deutscher Bundestag/Bundesrat – DIP"; when quoting or reproducing
     Drucksachen or Plenarprotokolle also "BT-Drs." / "BT-PlPr." plus the document number.
   - d. Commercial use must include a note, with the link dip.bundestag.de, that the data
     is available free of charge in DIP.
4. No use in a distorting context or one that could denigrate members of the Bundestag,
   Bundesrat, federal government or other persons; no use in unlawful, violent,
   pornographic, racist or antisemitic surroundings (Nr. 5).
5. No warranty for correctness; the user is responsible for third-party rights (Nr. 6).

Our `source_document_id` values (`BT-PlPr. 21/94`, `BT-Drs. 21/7300`) are chosen so that
Nr. 4c is satisfied automatically wherever a fact is displayed.

## abgeordnetenwatch.de

API page (https://www.abgeordnetenwatch.de/api) and every API response
(`meta.abgeordnetenwatch_api.licence`): **CC0 1.0 Universal**. No key, 30 requests per
minute per IP, bulk downloads requested outside 06:00–22:00. Attribution is not required;
we keep `source_url` anyway.

Side jobs (`side_job`, `/sidejobs`): the CC0 licence covers abgeordnetenwatch's own
republication, not the underlying obligation. The facts themselves are the "Veröffentlichungspflichtige
Angaben" every Bundestag member must file under the Verhaltensregeln für Mitglieder des Deutschen
Bundestages (Anlage 1 GO-BT, § 44a AbgG); the Bundestag publishes them on bundestag.de per member,
and abgeordnetenwatch republishes the same facts, structured, under CC0. We store what abgeordnetenwatch
publishes (label, category, Stufe/range, dates, organisation) as facts only, with a source link to the
abgeordnetenwatch record for each row.

## Bundeswahlleiterin

Open data page https://www.bundeswahlleiterin.de/bundestagswahlen/2025/ergebnisse/opendata.html;
the licence is named in the first two lines of every CSV: "(c) Die Bundeswahlleiterin, Wiesbaden 2025",
"Datenlizenz Deutschland – Namensnennung – Version 2.0" (https://www.govdata.de/dl-de/by-2-0). Use,
change and redistribution are allowed, commercially too, with the attribution and a link to the licence;
changes must be marked. Our `source_document_id` names the file and its "Stand" date.

The Wahlkreiseinteilung by Gemeinde (https://www.bundeswahlleiterin.de/bundestagswahlen/2025/wahlkreiseinteilung/downloads.html,
`btw25_wkr_gemeinden_20241130_utf8.csv`) carries the same licence with the attribution
"(c) Die Bundeswahlleiterin, Statistische Ämter des Bundes und der Länder, Wiesbaden 2024".

## Portraits (bundestag.de, Wikimedia Commons)

bundestag.de shows every MdB with a portrait and a caption "© <rights holder>": the member, the fraction, the
Bundestag's photographers ("DBT/ Inga Haar") or a named photographer. The Nutzungsbedingungen say the Bundestag
accepts no liability for third-party rights such as photos, so the portraits are not covered by the free-use
clause for Bundestag material. Decision of 2026-09-28: use them without a per-photo licence check and always show
the credit as stored (without the "©", which the display adds). A publisher should clear this before wider use.

Government members without a bundestag.de portrait get their Wikidata image (P18) from Wikimedia Commons. Each file
has its own licence; `person_photo.credit` stores "<author>, <licence short name>" from the file's metadata and
`source_url` the file page, which carries the full licence. CC BY-SA requires the author, the licence with a link,
and share-alike for adaptations (a downscaled thumbnail is fine to publish under the same licence).

## Wikidata

Wikidata's structured data is **CC0 1.0** (https://www.wikidata.org/wiki/Wikidata:Licensing). No attribution is
required; `source_url` points at the item anyway. The SPARQL service asks for a descriptive User-Agent, which
`bdf` sends (`config.USER_AGENT`).

## What this repository publishes

- Code: MIT (see `LICENSE`).
- `tests/fixtures/`: small excerpts of one Plenarprotokoll XML, one roll-call XLSX,
  a Stammdaten excerpt, recorded API responses, and three portraits with their credits — all under the terms above, with
  source attribution in `tests/fixtures/README.md`. Excerpts are marked as excerpts.
- No bulk data is committed.
