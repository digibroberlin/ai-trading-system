# AP-20 – Protokoll der historischen Profitabilitätsanalyse (vorab festgelegt)

**Festgelegt am 2026-10-17, bevor ein Lauf auf Training, Validierung oder Test ausgewertet wurde.** Änderungen nach diesem
Zeitpunkt sind nur als Nachtrag mit Datum und Begründung erlaubt und werden im Bericht genannt. Einzige Ausnahme vorher:
technische Probeläufe auf dem ersten Halbjahr 2017 (Teil des Trainings) zur Prüfung der Werkzeuge.

Ziel: ehrlich feststellen, ob eine der vorhandenen Strategien – mit welcher Agenten-Kombination – **nach sämtlichen
Kosten** einen belastbaren Vorteil hat. „Kein belastbarer Vorteil“ ist ein gültiges Ergebnis. Es wird **nicht gehandelt**:
nur Backtests auf historischen Daten.

## 1. Daten

* **Quelle:** Bitstamp BTC/USD, 1-Minuten-Kerzen (Spot), öffentliche Kopie
  `github.com/ff137/bitstamp-btcusd-minute-data`, Commit `163afe666e556b8a556103565eb1515acb06b92b` (Stand 2026-10-17
  01:40 UTC). Erzeugt per täglicher GitHub Action aus der öffentlichen Bitstamp-OHLC-Schnittstelle; Altbestand bis
  2025-01-07 aus dem Kaggle-Datensatz „Bitcoin Historical Data“ (Zielak, Version 706). Lizenz: CC BY-SA 4.0
  (Altbestand), Aktualisierungen laut Repository.
* **Abruf** (lesend, kostenlos, über `raw.githubusercontent.com` – die Börsen-Schnittstellen selbst sind in der Cloud
  gesperrt, B-008):
  ```bash
  C=163afe666e556b8a556103565eb1515acb06b92b; D=data/external/bitstamp-btcusd-1m/$C; mkdir -p $D
  for p in data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz data/updates/btcusd_bitstamp_1min_latest.csv \
           data/original/source-manifest.json data/provenance/btcusd_bitstamp_1min.csv; do
    curl -sS --fail -o $D/$(basename $p) https://raw.githubusercontent.com/ff137/bitstamp-btcusd-minute-data/$C/$p; done
  ```
  Prüfsumme des Altbestands: SHA-256 `1be152060b39327b669cbed236eeb283191fadaf3862f76c1e974be54ceb1a20` (= Angabe im
  Herkunftsnachweis der Quelle). Die Dateien liegen nicht im Repository (`data/` ist ausgeschlossen).
* **Qualität (geprüft):** keine Dubletten, keine formal fehlerhaften Kerzen, lückenloses Minutenraster. Die Quelle füllt
  Minuten ohne Handel mit flachen Kerzen ohne Umsatz auf; diese Füllwerte werden **nicht** verwendet. Ab 2017 fehlen je
  Jahr höchstens 4 Stunden ohne jeden Handel (belegte Wartungen/Ausfälle); sie bleiben Lücken (ausgewiesen, nicht
  aufgefüllt). Zwei echte Kurssprünge > 20 % in einer Stunde (10.03.2017, 13.03.2020) bleiben drin.
* **Verdichtung:** 1 Minute → 1 Stunde (Hauptuntersuchung); nur Minuten mit Umsatz. Für die Scalping-Prüfung werden die
  1-Minuten-Daten direkt ausgewertet (Abschnitt 7). Tick-, Trade- oder Orderbuchdaten sind **nicht verfügbar**.
* **Nicht verfügbar (dokumentierte Einschränkung):** ETH und andere Coins, Coinbase- und Bitget-Kurse, echte
  Futures-Kurse und echte Funding-Sätze. Ursache: Netzsperre der Cloud (B-008); ohne Freigabe keine Aussage dazu.

## 2. Zeitabschnitte (fest, nie verschoben)

| Abschnitt | Zeitraum (UTC) | Inhalt (grob) | Zweck |
|---|---|---|---|
| Vorlauf | ab 2016-12-01 | – | nur Historie für Indikatoren (300 Kerzen vor jedem Abschnitt) |
| Training | 2017-01-01 – 2023-01-01 | Hausse 2017, Baisse 2018, Erholung 2019, Crash und Hausse 2020/21, Baisse 2022 | Diagnose, Ableitung von Variante E |
| Validierung | 2023-01-01 – 2025-01-01 | Erholung 2023, Anstieg 2024 | Auswahl, Prüfung der Kandidaten |
| Test | 2025-01-01 – 2026-10-16 | unberührt | einmalige Prüfung eingefrorener Kandidaten |

Jeder Abschnitt startet mit 10 000 USD; am Ende werden alle Positionen zum letzten Kurs **mit allen Kosten** geschlossen.
**Hinweis zur Unabhängigkeit:** Der Entwickler (ein KI-Modell) kennt den groben Kursverlauf bis etwa Mitte 2026 aus seinen
Trainingsdaten. Deshalb werden Strategien und Parameter nur aus Fachliteratur-Standards und dem Trainingsabschnitt
abgeleitet, Kandidaten vor dem Test eingefroren, und der Testteil **ab 2026-07-01** wird zusätzlich gesondert ausgewiesen
(nach dem Wissensstand des Entwicklers – der sauberste, aber kurze Teil).

## 3. Kostenszenarien (Annahmen – vor einer Demo gegen die aktuellen Tarife prüfen)

| Szenario | Markt | Gebühren Taker/Maker | Spread | Slippage (Stop) | Funding |
|---|---|---|---|---|---|
| **F** Futures-Näherung (wie Bitget USDT-M Standard) | Futures, Long und Short | 0,06 % / 0,02 % | 2 bp | 2 bp (5 bp) | Szenario +0,01 % je 8 h (Long zahlt) |
| **S** Spot (wie Bitget Spot Standard) | Spot, nur Long | 0,10 % / 0,10 % | 2 bp | 2 bp (5 bp) | – |
| **K** Spot teuer (wie Coinbase Advanced Einstiegsstufe) | Spot, nur Long | 0,60 % / 0,40 % | 2 bp | 2 bp (5 bp) | – (nur Empfindlichkeit) |
| **Stress** | wie F bzw. S | × 1,5 | × 1,5 | × 1,5 | × 1,5 |

Futures-Näherung heißt: Bitstamp-Spotkurse als Perpetual-Kurs, Funding als Szenario (kein echter Satz).
**Betriebskosten (Annahme):** Server 6 USD/Monat (B-005), Marktdaten 0 (öffentlich), Sonstiges 0.
**KI-Kosten:** Es gibt keine freigegebene KI (B-004) – also auch **keine echte KI-Entscheidung**. Variante D rechnet die
Kosten der KI-Zweitmeinungen, die die Regeln angefragt hätten (unsicheres Urteil ohne harte Ablehnung), mit den Preisen
der Preistabelle (Stand 2026-10-06): typisch 600 Eingabe- / 60 Ausgabe-Tokens, Obergrenze 2 000 Bytes / 400 Tokens.

## 4. Strategien und Vergleichsmaßstäbe

Vorhandene Strategien mit **Standardeinstellungen** (keine Optimierung): `trend`, `momentum`, `mean_reversion`,
`breakout`, `volatility`, `scalping` (RSI(2)-Rücksetzer – auf Stundenkerzen), `sma_cross` (Referenz). Vergleich:
Nicht-Handeln (0), Kaufen-und-Halten (gleiche Kosten).

## 5. Agenten-Varianten (unter identischen Bedingungen)

| Variante | Ablauf |
|---|---|
| V0 | MOMO → RIN → SORA → Executor (KIBO/YUZU neutral) – Grundgerüst |
| A | MOMO → RIN → SORA → **KIBO** → Executor |
| B | MOMO → RIN → SORA → **YUZU** → Executor |
| C | MOMO → RIN → SORA → **KIBO/YUZU** → Executor (Regel-Architektur) |
| D | ursprüngliche Architektur = C + KI-Zweitmeinung; Entscheidungen wie C (keine echte KI), KI-Kosten als Szenario |
| S | Diagnose: Entscheidungen wie V0, KIBO/YUZU urteilen nur im Schatten (Nutzen je Trade messbar) |
| E | optimiert – nach dem Training, vor der Validierung festgelegt (Abschnitt 6) |

AKI wertet jeden Lauf aus. RIN, SORA, Risikomanager, Executor und alle Sicherheitsgrenzen (2 % Gesamtrisiko, 0,5 % je
Trade, 100 % Exposure, Stresstest, Not-Aus) bleiben in **jeder** Variante unverändert.

## 6. Ableitung von Variante E (Regeln gegen Überanpassung)

* Nur aus dem **Training** (Diagnose: Blockaden je Stufe, Schatten-Urteile, Marktphasen, Kosten).
* Höchstens **6 Kandidaten**, jeder mit schriftlicher Begründung, **vor** dem ersten Validierungslauf in diesem Protokoll
  nachgetragen (Abschnitt 10). Keine Parametersuche über Raster.
* Jeder Kandidat zählt als Versuch (Abschnitt 8). Sicherheitsgrenzen werden nie gelockert.

## 7. Scalping-Prüfung

Mit den 1-Minuten-Daten wird für 1/5/15/60 Minuten die Verteilung der Kursbewegung und der größten günstigen Bewegung
mit den Rundreise-Kosten jedes Szenarios verglichen (Kostenhürde). Es wird **keine** sekundengenaue Ausführung
vorgetäuscht: Ausführung frühestens zum Eröffnungskurs der nächsten Minute; ohne Tick- und Orderbuchdaten keine Aussage
über Warteschlange und Teilausführungen.

## 8. Bewertung

**Kennzahlen je Lauf:** Signale, freigegebene und blockierte Setups je Stufe, Trades, Gewinn/Verlust, Gebühren,
Funding, Spread/Slippage (geschätzt), Handelsnetto (A), wirtschaftliches Netto (B, nach Betriebs- und KI-Kosten),
Erwartungswert je Trade, Ø Gewinn, Ø Verlust, Profit-Faktor, Trefferquote, max. Drawdown, Sharpe/Sortino (Tageswerte),
CAGR, Calmar, Long/Short, Marktphase, Jahr, Not-Aus-Zeitpunkt, Entscheidungslatenz je Agent (Rechenzeit der Regeln).

**Validierung – „Kandidat“** (alle Bedingungen):
1. Handelsnetto (A) **und** wirtschaftliches Netto (B) > 0;
2. mindestens 30 Trades;
3. auch unter Kosten-Stress > 0 (A);
4. Sharpe je Tag über der deflationierten Schwelle (z = 1,645) für **N = alle** Validierungsversuche der Reihe.

**Test (einmal, nur eingefrorene Zellen):** alle ursprünglichen Varianten (V0, A, B, C, D) aller Strategien in F und S
plus die E-Kandidaten werden vor dem Test in `docs/ap20/freeze.json` eingefroren (Prüfsumme) und je **genau einmal**
gerechnet. Danach wird nichts mehr verändert.

**Einstufung im Bericht:**
* *technisch erfolgreich getestet* – Lauf fehlerfrei, Buchhaltung stimmt;
* *historisch profitabel* – Netto (A und B) > 0 im jeweiligen Abschnitt;
* *statistisch ausreichend belastbar* – Kandidat in der Validierung **und** im Test Netto (A, B, Stress) > 0 **und**
  untere Grenze des 95-%-Block-Bootstrap-Vertrauensbereichs des mittleren Tagesergebnisses über Validierung+Test > 0;
* *noch nicht überprüft* – alles, wofür Daten fehlen (ETH, Altcoins, echte Futures/Funding, echte KI).

**Agentennutzen:** gepaarter Vergleich der Tagesergebnisse (Variante gegen V0, gleiche Tage, Block-Bootstrap) und je Trade
über die Schatten-Urteile (Netto der Trades, die KIBO/YUZU verhindert hätten). Im Zweifel gilt die einfachere Variante.

## 9. Was nicht gemacht wird

Keine Order, kein Demo- oder Paper-Betrieb, keine bezahlte KI, keine kostenpflichtigen Daten, keine Änderung an den
Testdaten, keine Wiederholung eines Tests, keine Lockerung von Sicherheitsgrenzen.

## 10. Nachträge

**Nachtrag 1 (2026-10-17 09:47 UTC, vor jeder Validierung) – Not-Aus über mehrjährige Abschnitte.** Erste Trainingsläufe zeigten:
Die 10-%-Drawdown-Bremse löst bei mehreren Strategien aus (z. B. Breakout am 01.02.2019); danach wurde bis zum Ende des
sechsjährigen Abschnitts nichts mehr eröffnet. Das Ergebnis misst dann vor allem den Zeitpunkt der Bremse, nicht die
Strategie. Im Betrieb prüft der Eigner einen Not-Aus und gibt ihn mit Namen und Bestätigung frei (vorhandener Ablauf
`release_halt`: gemessen wird ab dann neu, **mit denselben Grenzen**). Festlegung:
* **Hauptauswertung („monatlich“):** simulierte Prüfung des Eigners – nach einem Not-Aus Freigabe am Ersten des
  Folgemonats (00:00 UTC). Jeder Not-Aus wird mit Zeitpunkt, Grund und Freigabe gezählt und im Bericht ausgewiesen; viele
  Not-Aus-Ereignisse sind selbst ein negatives Ergebnis.
* **Zusätzlich („streng“):** ohne jede Freigabe – nach dem ersten Not-Aus keine neue Position bis zum Ende des Abschnitts.
* **Stiller Stillstand** (gefunden im Training): Liegt der Drawdown einen Hauch unter der Grenze, lehnt SORA jede neue Order ab (schon die Gebühr risse die Grenze), der Not-Aus löst aber nicht aus – im Betrieb gäbe es keinen Alarm. In beiden Modi wird das als Ereignis `BREMSE_OHNE_NOT_AUS` gezählt; „monatlich“ behandelt es wie einen Not-Aus (Prüfung am Folgemonatsersten), „streng“ lässt es bestehen. Für den Betrieb ist das ein Befund (Bericht, Abschnitt Empfehlungen).
* Keine Grenze wird verändert; Agenten lösen nichts frei. Die Trainingsläufe wurden in beiden Modi neu gerechnet.

**Nachtrag 2 (2026-10-17 10:43 UTC, vor jedem Validierungslauf) – Variante E und Umfang der Validierung.**
Befunde aus dem Training (Hauptauswertung, Szenarien F und S; Einzelheiten im Bericht):
* KIBO/YUZU ohne messbaren Nutzen für die Trendfolger (gepaarte Tagesdifferenzen um 0); KIBO nützt nur bei Mean
  Reversion (verhindert Gegen-Trend-Einstiege), die aber auch so deutlich verliert. YUZU blockiert fast nie.
* Kosten fressen 40–60 % des Bruttoergebnisses (Momentum: Gebühren 3,4 Tsd. + Spread/Slippage 1,8 Tsd. bei 11,7 Tsd.
  vor Kosten); viele Not-Aus-Ereignisse durch die 10-%-Drawdown-Bremse.
* Der Vorteil der Trendfolger nimmt über die Jahre ab (Momentum F: 2017 +3,7 Tsd. → 2022 −0,9 Tsd.).

**E-Kandidaten (fachlich begründet, keine Rastersuche; je Kandidat nur eine Einstellung):**
1. **Zeitrahmen 4 h** für `momentum`, `trend`, `breakout` mit Standard-Einstellungen (`momentum-4h`, `trend-4h`,
   `breakout-4h`): weniger Trades, geringerer Kostenanteil, weniger Rauschen.
2. **Variante E** (optimierte Zusammenarbeit): MOMO → RIN → SORA → Executor ohne KIBO/YUZU (kein belegter Nutzen,
   spart Rechenzeit und mögliche KI-Kosten) **und** SORA senkt ab 5 % Drawdown das Risiko je Trade auf die Hälfte
   (`SoraConfig.drawdown_cut=0,05`, `drawdown_factor=0,5` – nur strenger, nie lockerer).

**Validierungsläufe (alle werden gerechnet und berichtet):**
* Ursprüngliche Strategien (7) × Varianten V0, A, B, C, D × Szenarien F, S – und dieselben Zellen unter Kosten-Stress
  (Fx, Sx); Variante S (Schatten) in F und S zur Diagnose.
* E: `momentum` und `trend` mit Variante E; `momentum-4h`, `trend-4h`, `breakout-4h` mit V0, C und E – in F, S, Fx, Sx.
* Empfindlichkeit (nicht für die Auswahl): Szenario K für V0 und C; strenger Modus für F und S.
* **Versuchszahl N für die deflationierte Sharpe-Schwelle = 92** (70 ursprüngliche + 22 E-Zellen in F und S).


**Nachtrag 3 (2026-10-17 10:56 UTC, nach der Validierung, vor jedem Testlauf) – Ergebnis der Validierung, Testumfang.**
* **Kein Lauf erfüllt alle vier Kandidaten-Bedingungen.** Die deflationierte Sharpe-Schwelle (N = 92, z = 1,645) liegt
  bei rund 0,172 je Tag (≈ 3,3 pro Jahr) und wird von keinem Lauf erreicht (bester: `breakout-4h` in S mit 0,069).
* Die drei 4-h-Kandidaten erfüllen die Bedingungen 1–3 in F und S (Netto A und B > 0, ≥ 30 Trades, Stress > 0).
  Alle ursprünglichen 1-h-Strategien sind in F negativ; in S ist nur `volatility` knapp positiv (A), aber B bzw. Stress ≤ 0.
* Folge nach Protokoll: Es gibt **keinen Kandidaten**; „statistisch ausreichend belastbar“ ist für diesen Test
  ausgeschlossen. Der Test wird trotzdem wie festgelegt **einmal** für alle eingefrorenen Zellen gerechnet – zur ehrlichen
  Beschreibung, nicht zur Auswahl. Danach wird nichts mehr verändert oder nachgerechnet.
* **Eingefroren (`docs/ap20/freeze.json`):** ursprüngliche Strategien (7) × V0, A, B, C, D × F, S, Fx, Sx; E-Zellen
  (`momentum|E`, `trend|E`, `momentum-4h`/`trend-4h`/`breakout-4h` × V0, C, E) × F, S, Fx, Sx; Variante S (Schatten) der
  7 Strategien in F und S; Empfindlichkeit K für V0 und C der 7 Strategien sowie die E-Zellen – zusammen 223 Zellen.
  Der strenge Modus wird für dieselben Zellen in F und S beschreibend gerechnet (eigener Ergebnisordner).
  Prüfsumme der eingefrorenen Liste: SHA-256 `08183e756b095c5fc9b727135e2214a2b85cec4ae69bf683004f4cf3a724d1ba`,
  erstellt 2026-10-17 10:56:55 UTC (die Notiz in `freeze.json` nennt versehentlich 10:58 UTC; maßgeblich ist
  `created_at`). Liste und Protokoll wurden vor dem ersten Testlauf eingecheckt.

**Durchführung des Tests (2026-10-17, ca. 10:57–11:07 UTC):** alle 223 eingefrorenen Zellen je einmal gerechnet, ohne
Fehler; danach der strenge Modus für dieselben Zellen in F und S (beschreibend). Keine Zelle wurde wiederholt, nichts am
Protokoll, an den Strategien oder an den Daten verändert. Ergebnis und Einstufung: `docs/PROFITABILITAETSANALYSE.md`.
