# E-mail template: personal DIP API key

The DIP Nutzungsbedingungen (Nr. 3) require only: a valid e-mail address, the name of a
contact person, and — if applicable — the institution. Keep it short.

---

**An:** parlamentsdokumentation@bundestag.de
**Betreff:** Anfrage eines personalisierten API-Schlüssels für die DIP-API

Sehr geehrte Damen und Herren,

hiermit bitte ich um einen personalisierten, dauerhaft gültigen API-Schlüssel für die
DIP-Anwendungsschnittstelle (https://search.dip.bundestag.de/api/v1) gemäß Nr. 3 der
DIP-Nutzungsbedingungen.

Ansprechperson: Jan Buchkremer
E-Mail-Adresse: jan.buchkremer.jb@gmail.com
Institution: [ggf. Name der Institution, sonst Zeile streichen]

Verwendungszweck: Aufbau einer wöchentlich aktualisierten Datenbasis über Drucksachen,
Vorgänge und Aktivitäten der laufenden Wahlperiode für journalistische Recherche
(Faktenblätter zu Abgeordneten, Themenübersichten zu Sitzungswochen). Die Abfragen
erfolgen lesend, einmal wöchentlich, in Batches mit wenigen parallelen Anfragen; die
Quellenangabe „Deutscher Bundestag/Bundesrat – DIP" wird bei jeder Weiterverwendung
angegeben.

Vielen Dank und freundliche Grüße
Jan Buchkremer

---

After the key arrives: put it in the environment variable `DIP_API_KEY` (never in the
repository). `bdf fetch dip` reads it from there.
