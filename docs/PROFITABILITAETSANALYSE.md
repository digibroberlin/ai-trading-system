# Profitabilitätsanalyse der Strategien und der sechs Agenten (AP-20)

**Stand:** 2026-10-17 · Arbeitszweig `ap/20-profitabilitaet` · Protokoll (vorab festgelegt): [`AP20_PROTOKOLL.md`](AP20_PROTOKOLL.md)
· alle Tabellen: [`ap20/ergebnisse.md`](ap20/ergebnisse.md) (Hauptauswertung) und
[`ap20/ergebnisse_streng.md`](ap20/ergebnisse_streng.md) (strenger Not-Aus-Modus) · eingefrorener Testumfang:
[`ap20/freeze.json`](ap20/freeze.json).

**Es wurde nicht gehandelt.** Keine Order, kein Demo- oder Paper-Betrieb, keine Börsen-Schlüssel, keine bezahlte KI,
keine kostenpflichtigen Daten. Alles sind Backtests auf öffentlichen historischen Daten (nur lesend abgerufen).

## Kurzfassung

1. **Keine Strategie ist belastbar profitabel.** Nach den vorab festgelegten Regeln hat keine der 92 geprüften
   Kombinationen aus Strategie, Agenten-Variante und Markt die Validierung bestanden; damit gibt es **keinen Kandidaten**
   und keine Strategie mit der Einstufung „statistisch ausreichend belastbar“.
2. **Die ursprünglichen sieben Strategien (Stundenkerzen) verdienen heute nach Kosten nichts.** Im Training (2017–2022)
   waren Momentum und Trendfolge profitabel (Momentum, Futures-Näherung: +6.337 USD auf 10.000 USD in 6 Jahren,
   ≈ 8,5 % pro Jahr bei 9,4 % größtem Rückgang). In der Validierung (2023–2024) und im unberührten Test (2025 bis
   Oktober 2026) liegen **alle sieben** im Minus. Der Vorteil ist über die Jahre geschwunden (Momentum, Futures-Näherung, Variante C: 2017 +3,7 Tsd.,
   2022 −0,9 Tsd. USD).
3. **Am besten schneidet `trend-4h` ab** (Trendfolge auf 4-Stunden-Kerzen, eine der nach dem Training festgelegten
   Verbesserungen): in **jedem** Abschnitt leicht im Plus (Spot: +213 / +251 / +243 USD Handelsnetto in Training,
   Validierung, Test), nie ein Not-Aus, größter Rückgang 2–6 %. Aber: nur 37–38 Trades je Abschnitt, rund 1 % Ertrag
   pro Jahr, der Vertrauensbereich schließt 0 ein, und nach Serverkosten bleibt bei 10.000 USD Kapital fast nichts.
   Einstufung: **„historisch profitabel (Validierung und Test), nicht belastbar“** – kein Beleg für einen echten Vorteil.
4. **Kosten sind der Hauptgegner.** Gebühren und Spread/Slippage verbrauchen bei den Stunden-Strategien 45 % (Training)
   bis über 100 % (Validierung) des Ergebnisses vor Kosten. Mit Gebühren wie bei Coinbase für Einsteiger (0,6 %) lehnt RIN
   fast jeden Trade ab – ein sinnvoller Schutz, aber dann gibt es auch nichts zu verdienen.
5. **Agenten:** KIBO und YUZU bringen den Trendfolgern keinen messbaren Nutzen. KIBO verhindert bei Mean Reversion in allen
   drei Abschnitten zuverlässig Verluste (+1,35 bis +3,85 USD je Tag) – die Strategie bleibt trotzdem unprofitabel.
   Die optimierte Variante E (ohne KIBO/YUZU, SORA halbiert das Risiko ab 5 % Rückgang) senkt Verluste und Rückgänge,
   macht aber keine Strategie profitabel.
6. **KI:** Es wurde **keine echte KI** eingesetzt (kein freigegebenes Budget). Die KI-Kosten des ursprünglichen Designs
   wären verschwindend gering (typisch höchstens 0,06 USD, im ungünstigsten Fall 0,36 USD je Strategie in zwei Jahren
   bei Haiku-Preisen), aber ein Nutzen ist nicht
   belegt – die KI bleibt aus.
7. **Scalping lohnt sich mit den verfügbaren Daten nicht:** Die typische Kursbewegung in 5 Minuten (0,10 %) ist kleiner als
   die Kosten einer Rundreise (0,18–0,26 %). Für Scalping im Sekundenbereich fehlen Tick- und Orderbuchdaten; dazu wird
   keine Aussage gemacht.
8. **Kaufen-und-Halten** war in Training und Validierung absolut weit besser (Spot +1.606 % bzw. +463 %), allerdings mit
   Zwischenverlusten bis 83 %; im Test verlor es 12 % (Zwischenverlust 53 %). Die Strategien sind viel vorsichtiger
   (Rückgänge meist 3–20 %), verdienen aber nach Kosten nichts.
9. **Empfehlung:** Für einen Demo-Betrieb **mit Gewinnerwartung** ist keine Strategie geeignet. Eine Demo **zur technischen
   Erprobung** (Abläufe, Daten, Alarme, Kosten – nicht Gewinn) ist mit `trend-4h` auf BTC-Spot, Variante V0 oder E und
   kleinster Größe vertretbar – erst nach Ihrer Freigabe und nach einer Gegenprobe auf Daten der Zielbörse (Abschnitt 11).

**Einstufung im ganzen Bericht** (vorab festgelegt, Protokoll Abschnitt 8):
*technisch erfolgreich getestet* = Lauf fehlerfrei, Buchhaltung stimmt · *historisch profitabel* = Netto A und B > 0 im
genannten Abschnitt · *statistisch ausreichend belastbar* = Kandidat in der Validierung **und** im Test positiv
(auch unter Kosten-Stress) **und** 95-%-Vertrauensbereich des Tagesergebnisses über Validierung+Test über 0 ·
*noch nicht überprüft* = dafür fehlen Daten.

---

## 1. Datenquellen

| Punkt | Inhalt |
|---|---|
| Quelle | Bitstamp BTC/USD, 1-Minuten-Kerzen (Spot); öffentliche Kopie `github.com/ff137/bitstamp-btcusd-minute-data`, Commit `163afe6…`; Altbestand bis 2025-01-07 aus dem Kaggle-Datensatz „Bitcoin Historical Data“ (Zielak, Version 706), danach täglich aus der öffentlichen Bitstamp-Schnittstelle |
| Lizenz, Kosten | CC BY-SA 4.0 (Altbestand), Aktualisierungen CC BY 4.0 – **kostenlos**, nur lesend über `raw.githubusercontent.com` |
| Prüfsummen | Altbestand SHA-256 `1be15206…a20` (= Angabe der Quelle), Aktualisierung SHA-256 `861a1eb3…439`; Ergebnisse sind mit denselben Dateien genau reproduzierbar |
| Umfang | 2016-06-01 bis 2026-10-15 23:00 UTC: 5.456.160 Minuten, davon 5.190.612 mit Umsatz (95,1 %); verdichtet zu 90.926 Stundenkerzen |
| Qualität | keine Dubletten, keine fehlerhaften Kerzen, lückenloses Minutenraster. Minuten ohne Handel füllt die Quelle mit flachen Kerzen ohne Umsatz – diese Füllwerte werden **nicht** verwendet. Stunden ganz ohne Handel bleiben Lücken (6 Stück, höchstens 4 h: 2020-04-25, 2022-01-05, 2022-07-13, 2023-07-03, 2024-12-07, 2025-03-23) und sind ausgewiesen, **nichts wird aufgefüllt oder erfunden** |
| Verfügbar? Tick/Trade/Orderbuch | **nein** – kostenlos und erlaubt nicht beschaffbar; deshalb keine Sekunden-Simulation |
| **Nicht verfügbar** | Coinbase- und Bitget-Kurse, ETH und andere Coins, echte Futures-Kurse und echte Funding-Sätze. Ursache: Die Cloud-Umgebung sperrt die Börsen-Schnittstellen (B-008). Ein technischer Umweg über einen anderen Server-Namen wurde bewusst **nicht** genutzt |

Futures wurden deshalb **genähert**: Bitstamp-Spotkurse als Perpetual-Kurs, Funding als Szenario +0,01 % je 8 Stunden
(Long zahlt). Aussagen zu Futures sind damit „Näherung“, nicht „gemessen“.

## 2. Marktphasen

| Abschnitt | Zeitraum (UTC) | Verlauf | Kaufen-und-Halten Spot (Rendite / größter Rückgang) | Zweck |
|---|---|---|---|---|
| Training | 2017-01-01 – 2023-01-01 | Hausse 2017, Baisse 2018, Erholung 2019, Crash März 2020, Hausse 2020/21, Baisse 2022 | +1.606 % / 83 % | Diagnose, Ableitung der Verbesserungen |
| Validierung | 2023-01-01 – 2025-01-01 | Erholung 2023, starker Anstieg 2024 | +463 % / 27 % | Auswahl nach festen Regeln |
| Test (unberührt) | 2025-01-01 – 2026-10-16 | Seitwärts bis abwärts, hohe Schwankung | −12 % / 53 % | einmalige Prüfung, danach nichts geändert |

Jeder Abschnitt startet mit 10.000 USD; am Ende wird alles mit allen Kosten geschlossen. Zusätzlich wird jede Einstiegs-
kerze einer Marktphase zugeordnet (Trend auf/ab/seitwärts × Schwankung niedrig/normal/hoch; Tabellen „Marktphase“).
Ergebnis: Die Trendfolger verlieren vor allem in **seitwärts laufenden Märkten mit normaler Schwankung** (z. B. Breakout,
Test, Futures: −1.645 USD aus 197 Trades in dieser Phase) und verdienen nur in klaren Trends. Der Testteil **ab
2026-07-01** (nach dem Wissensstand des Entwicklers, also der sauberste Teil) ist in der Einstufungstabelle gesondert
ausgewiesen.

## 3. Strategien

Alle Strategien mit **Standardeinstellungen**, nicht optimiert. Stop-Loss = 2 × ATR (mind. 0,5 %), Ziel = 2,5 × Stop
(soweit die Strategie nichts anderes vorgibt), Risiko je Trade 0,5 % des Kapitals, Short nur bei Futures.

| Strategie | Idee | Training (F / S) | Validierung (F / S) | Test (F / S) | Befund |
|---|---|---|---|---|---|
| `momentum` | Veränderungsrate über 20 Kerzen steigt über 3 %, Kurs über dem Trend | **+6.337 / +2.391** | −308 / −316 | −1.690 / −1.015 | früher profitabel, Vorteil verschwunden; im Test schon **vor** Kosten negativ |
| `trend` | EMA 20 kreuzt EMA 50 | +2.228 / +267 | −2.047 / −1.497 | −824 / −762 | verliert in Seitwärtsphasen |
| `breakout` | Donchian-Ausbruch (20/10) | +132 / +24 | −1.323 / −445 | −2.505 / −1.853 | viele Fehlausbrüche, hohe Kosten (542 Trades im Test) |
| `sma_cross` (Referenz) | SMA-Kreuzung | +169 / −901 | −1.294 / −1.070 | −1.073 / −1.102 | kein Vorteil |
| `mean_reversion` | Bollinger + RSI, Gegenbewegung | −7.199 / −4.355 | −3.080 / −679 | −1.456 / −926 | handelt gegen starke Trends, 13 Not-Aus im Training |
| `volatility` | Ausbruch nach engen Bändern | −873 / −262 | −768 / +141 | −38 / −350 | um null, nach Kosten negativ |
| `scalping` | RSI(2)-Rücksetzer im Trend, auf Stundenkerzen | −735 / −425 | −709 / −192 | −1.469 / −343 | Trefferquote oft über 50 %, aber kleine Gewinne, Kosten überwiegen |
| `momentum-4h` | wie momentum, 4-h-Kerzen | +3.272 / +2.264 | +419 / +658 | −619 / −521 | Verbesserung trug in der Validierung, nicht im Test |
| `trend-4h` | wie trend, 4-h-Kerzen | +1.133 / +213 | +510 / +251 | **+116 / +243** | als einzige in allen Abschnitten positiv – aber klein und nicht belastbar |
| `breakout-4h` | wie breakout, 4-h-Kerzen | +4.772 / +2.917 | +838 / +1.105 | −895 / −629 | Verbesserung trug in der Validierung, nicht im Test |

Handelsnetto (A) in USD auf 10.000 USD je Abschnitt, Variante V0; F = Futures-Näherung, S = Spot (Abschnitt 6).
Die 4-h-Varianten wurden **nach** dem Training festgelegt; ihre Trainingszahlen sind deshalb nicht unabhängig.

**Long und Short:** In der Validierung (Anstieg) verdienten die 4-h-Strategien mit Long-Trades und verloren mit Shorts
(breakout-4h F: Long +1.367, Short −529). Im Test verloren Long- **und** Short-Trades fast überall. Ein stabiler Vorteil
einer Richtung ist nicht erkennbar; Spot (nur Long) war im Test nicht schlechter als die Futures-Näherung.

**Scalping und kürzere Zeitrahmen.**
* *Kostenhürde* (Training, 3,05 Mio. Minuten mit Umsatz): Die mittlere Kursbewegung beträgt in 1 Minute 0,04 %, in
  5 Minuten 0,10 %, in 15 Minuten 0,18 %, in 60 Minuten 0,39 %. Eine Rundreise kostet 0,18 % (Futures, Taker), 0,12 %
  (Futures, Maker), 0,26 % (Spot) bzw. 1,26 % (Coinbase-Einsteiger). In 1 Minute übersteigt die Bewegung die
  Futures-Kosten nur in 9 % der Fälle (in beide Richtungen zusammen), in 5 Minuten in 29 %. Man müsste also nicht nur die
  Richtung, sondern auch die seltenen großen Bewegungen treffen – mit Minutenkerzen ist das nicht erreichbar.
* *Diagnose* (nur Trainingsjahr 2022, keine Auswahl): Dieselben Regeln auf 5- und 15-Minuten-Kerzen – 28 von 32 Läufen
  negativ, die vier positiven sehr klein (+29 bis +186 USD); Gebühren übersteigen meist das Ergebnis vor Kosten, und RIN
  verwirft die meisten Scalping-Signale, weil das Ziel die Kosten nicht trägt (5-Minuten-Scalping, Futures: 150 Trades bei
  über 5.000 verworfenen Signalen). Breakout auf 5/15 Minuten: bis 41 % Rückgang.
* Sekunden-Scalping erfordert Tick- und Orderbuchdaten; die gibt es hier nicht – dazu wird **keine** Aussage gemacht.

**Eignung je Markt:** Nur BTC/USD ist geprüft. Für ETH, andere Coins, Coinbase- und Bitget-Kurse gilt **„noch nicht
überprüft“** (keine Daten, B-008).

## 4. Agenten-Kombinationen

Alle Varianten liefen unter **identischen** Bedingungen (gleiche Daten, Kosten, Grenzen). RIN, SORA, Risikomanager,
Executor und alle Sicherheitsgrenzen waren in jeder Variante unverändert aktiv.

| Variante | Ablauf | Ergebnis |
|---|---|---|
| V0 | MOMO → RIN → SORA → Executor (KIBO/YUZU neutral) | Grundgerüst; Maßstab für den gepaarten Vergleich |
| A | + KIBO (technische Prüfung) | Nutzen nur bei Mean Reversion (in allen drei Abschnitten, Vertrauensbereich über 0); sonst ≈ 0 |
| B | + YUZU (Marktumfeld) | ≈ 0; im Test half YUZU beim Scalping (+0,37 USD/Tag, F) – in Training/Validierung nicht → Zufall nicht ausgeschlossen |
| C | + KIBO und YUZU (Regel-Architektur) | ≈ V0; bei Trendfolgern kein messbarer Unterschied |
| D | ursprüngliche Architektur = C + KI-Zweitmeinung | Entscheidungen wie C (keine echte KI); KI-Kosten nur als Szenario (Abschnitt 6) |
| E | ohne KIBO/YUZU, SORA halbiert ab 5 % Rückgang das Risiko je Trade | kleinere Rückgänge und Verluste in schlechten Phasen, etwas weniger Gewinn in guten; macht keine Strategie profitabel |
| S | Diagnose: Entscheidungen wie V0, KIBO/YUZU urteilen nur „im Schatten“ | zeigt je Trade, was KIBO/YUZU verhindert hätten |

**Gepaarter Vergleich** (gleiche Tage, mittlere Differenz je Tag mit 95-%-Block-Bootstrap; Futures-Näherung):

| Strategie | KIBO (A − V0) Training / Validierung / Test | KIBO+YUZU (C − V0) Test |
|---|---|---|
| mean_reversion | +2,62 / +3,85 / +2,30 (alle über 0) | +2,30 [+0,87; +3,82] |
| momentum | +0,08 / +0,02 / −0,02 | −0,00 [−0,10; +0,11] |
| trend | −0,22 / +0,12 / +0,13 | +0,16 [−0,22; +0,55] |
| breakout | +0,07 / −0,06 / −0,01 | +0,99 [−0,20; +2,42] |
| scalping | +0,02 / +0,13 / +0,21 | +0,56 [+0,15; +1,00] |

**Schatten-Auswertung (Variante S):** KIBO hätte bei Mean Reversion 336 von 360 Trades verhindert (Validierung, F;
Ø −8,66 USD je verhindertem Trade) – richtig, aber die übrigen Trades verlieren ebenfalls. Bei den Trendfolgern
blockieren KIBO/YUZU nur 0–5 % der Trades; wegen der kleinen Zahl lässt sich deren Netto nicht verlässlich bewerten.

**Signale und Blockaden** (Beispiel Test, F, Variante C): Momentum 240 Signale → 231 ausgeführt (SORA 5, KIBO 2, YUZU 2);
Breakout 758 → 553 (RIN 143, SORA 53); Mean Reversion 766 → 32 (RIN 317, KIBO 417). Vollständige Zählung je Stufe und
Grund in `ap20/ergebnisse.md` („Agenten“).

**Entscheidungszeit** (Rechenzeit der Regeln je Kerze, gemessen auf der geteilten Cloud-Maschine, nur Richtwert):
V0 ≈ 1,5–3,4 ms, mit KIBO/YUZU ≈ 3,5–8 ms (KIBO ≈ 1,2 ms, RIN ≈ 1,1 ms, YUZU ≈ 1 ms). Für Stunden- und 4-h-Kerzen
bedeutungslos. Die Antwortzeit einer echten KI wurde **nicht gemessen** (keine KI eingesetzt).

**Schluss:** Die einfachste Kette **MOMO → RIN → SORA → Executor (→ AKI)** ist nicht schlechter als die komplexeren.
KIBO lohnt sich nur als Schutz für Gegen-Trend-Strategien; YUZU ohne belegten Nutzen. Nach der vorab festgelegten Regel
„im Zweifel die einfachere Variante“ wird für Trendfolger V0 bzw. E empfohlen.

## 5. Nettogewinn je Strategie

**Wirtschaftliches Netto (B)** = Handelsnetto (A) − Serverkosten (6 USD/Monat) − KI-Kosten. Beste Agenten-Variante je
Zeile in Klammern, sonst V0. USD auf 10.000 USD je Abschnitt.

| Strategie | Validierung F | Validierung S | Test F | Test S | Test F unter Kosten-Stress (A) | Einstufung |
|---|---|---|---|---|---|---|
| trend-4h | +366 | +107 | −13 (C: +10) | +114 (C: +215) | +33 (C: +62) | historisch profitabel (Validierung und Test), nicht belastbar |
| momentum-4h | +274 (C: +350) | +514 | −748 | −649 | −779 | nur in einem Abschnitt profitabel |
| breakout-4h | +694 | +961 | −1.023 | −758 | −1.114 | nur in einem Abschnitt profitabel |
| volatility | −912 | −3 (C: +14) | −167 | −479 | −45 | nicht profitabel |
| momentum | −452 | −460 | −1.819 | −1.143 | −1.880 | nicht profitabel |
| trend | −2.191 | −1.641 | −953 | −891 | −1.401 | nicht profitabel |
| scalping | −853 | −336 | −1.598 | −471 | −737 | nicht profitabel |
| sma_cross | −1.438 | −1.214 | −1.201 | −1.231 | −1.578 | nicht profitabel |
| breakout | −1.467 | −589 | −2.634 | −1.982 | −2.445 | nicht profitabel |
| mean_reversion | −3.224 (C: −409) | −823 (C: −371) | −1.585 (C: −83) | −1.054 (C: −173) | −1.309 | nicht profitabel |

Weitere Kennzahlen je Lauf (Trefferquote, Ø Gewinn/Verlust, Erwartungswert je Trade, Profit-Faktor, Sharpe, Sortino,
CAGR, Calmar) stehen in `ap20/ergebnisse.md`. Beispiel `trend-4h`, Test, Spot, V0: 37 Trades, Trefferquote 38 %,
Ø Gewinn 78 USD, Ø Verlust 37 USD, Erwartungswert +6,56 USD je Trade, Profit-Faktor 1,28, Sharpe 0,54, größter
Rückgang 3,4 %.

## 6. Handels- und KI-Kosten

**Kostenszenarien** (Annahmen, vor einer Demo gegen die aktuellen Tarife prüfen):

| Szenario | Gebühren Taker/Maker | Spread | Slippage (Stop) | Funding |
|---|---|---|---|---|
| F – Futures-Näherung (wie Bitget USDT-M Standard) | 0,06 % / 0,02 % | 2 bp | 2 bp (5 bp) | +0,01 % je 8 h (Szenario) |
| S – Spot (wie Bitget Spot Standard) | 0,10 % / 0,10 % | 2 bp | 2 bp (5 bp) | – |
| K – Spot teuer (wie Coinbase Advanced Einstieg) | 0,60 % / 0,40 % | 2 bp | 2 bp (5 bp) | – |
| Fx / Sx – Kosten-Stress | alles × 1,5 | | | |
| Betrieb (Annahme) | Server 6 USD/Monat (B-005), Marktdaten 0 USD (öffentlich), Sonstiges 0 | | | |

**Wohin das Geld geht** (Futures-Näherung, V0, USD):

| Strategie / Abschnitt | vor Kosten | Gebühren | Spread+Slippage | Funding | Handelsnetto A | Betrieb | Netto B |
|---|---|---|---|---|---|---|---|
| momentum, Training | 11.716 | 3.443 | 1.852 | 84 | 6.337 | 432 | 5.905 |
| momentum, Validierung | 773 | 687 | 378 | 16 | −308 | 144 | −452 |
| momentum, Test | −816 | 563 | 313 | −3 | −1.690 | 129 | −1.819 |
| trend-4h, Validierung | 661 | 105 | 59 | −13 | 510 | 144 | 366 |
| trend-4h, Test | 289 | 119 | 69 | −15 | 116 | 129 | −13 |

(Spread+Slippage aus dem Kostenmodell geschätzt; sie stecken in den Ausführungskursen. Buchhaltungsprobe: In allen
552 Läufen der Hauptauswertung stimmt das Kapital-Ergebnis auf den Cent mit der Summe der Trades überein.)

* Bei den Stunden-Strategien fressen Kosten 45 % (Training) bis über 100 % (Validierung) des Ergebnisses vor Kosten.
  Weniger Trades (4-h-Kerzen) senken die Kosten auf ein Fünftel bis ein Drittel – das war die wirksamste Verbesserung.
* **Coinbase-Einsteigergebühren (K):** RIN lehnt fast alles ab, weil das Ziel die Kosten nicht mehr trägt (0–12 Trades je
  Abschnitt). Das ist der Schutz, der wirken soll – handeln lohnt sich zu diesen Gebühren nicht.
* **Kosten-Stress (× 1,5):** Im Test bleiben nur `trend-4h` (+33 USD Futures-Näherung, +138 USD Spot) und Mean Reversion
  mit KIBO (+10 USD, nach Serverkosten negativ) über 0.

**KI-Kosten.** Es gibt keine freigegebene KI (B-004), deshalb gab es **keine echte KI-Entscheidung**. Variante D zählt,
wie oft die Regeln eine KI-Zweitmeinung angefragt hätten (unsicheres Urteil ohne harte Ablehnung), und rechnet mit der
Preistabelle (Claude Haiku, Stand 2026-10-06; typisch 600 Eingabe- und 60 Ausgabe-Tokens, Obergrenze 2.000 Bytes/400 Tokens):

| Beispiel (Validierung, 2 Jahre) | Anfragen | KI typisch | KI höchstens |
|---|---|---|---|
| breakout | 703 | 0,06 USD | 0,36 USD |
| momentum | 305 | 0,03 USD | 0,16 USD |
| mean_reversion | 54 | 0,005 USD | 0,03 USD |

Die KI wäre also **nicht** der Kostentreiber (Server: 144 USD in derselben Zeit). Sie bleibt trotzdem aus, weil ein Nutzen
nicht belegt ist. **Kostensparende Bausteine sind bereits eingebaut** (D-016/AP-31–33): Regeln zuerst, KI nur bei
bestimmten Ereignissen und Unsicherheit, Zwischenspeicher für gleiche Fragen, kompakte Eingaben, günstigste Modellstufe
zuerst, Wertschranke (eine Analyse darf höchstens 10 % ihres erwarteten Nutzens kosten), harte Tages- und Monatsbudgets
(Standard 0).

**Neu in AP-20 (Sicherheitslücke geschlossen):** Ein Anbieter mit Preis 0 – etwa ein lokales Modell – wäre bisher auch
ohne Freigabe und ohne erwarteten Nutzen aufgerufen worden. Jetzt gilt: **Budget 0 = kein Aufruf, auch zum Preis 0**
(Anwendung **und** Datenbank-Grenzen), und **ohne erwarteten Nutzen kein Aufruf**. Tests und Gegenproben Z50, Z51, G94,
G95.

**Ollama / lokale Modelle:** technisch über die vorhandene Anbieter-Schnittstelle anschließbar (ein Adapter, nur
`127.0.0.1`). Kosten sind nicht 0: Ein Server mit genug Speicher für ein brauchbares Modell kostet deutlich mehr als
6 USD/Monat. Empfehlung: erst einbauen, wenn eine KI-Zweitmeinung in einem gepaarten Vergleich einen Nutzen zeigt. Hinweis:
Ein Claude-Code-Abonnement ist **kein** API-Zugang; echte Claude-Aufrufe brauchen einen API-Schlüssel und Ihre
Budget-Freigabe (B-004).

## 7. Drawdown und Risiko

* **Sicherheitsgrenzen unverändert:** 2 % Gesamtrisiko, 0,5 % je Trade, 100 % Exposure, Lücken-Stresstest, Stop-Loss und
  Ziel, Schutz vor doppelten Orders, unteilbare Risikoprüfung, 10-%-Drawdown- und Tagesverlust-Bremse mit Not-Aus.
  Kein Agent kann sie abschalten; E macht SORA nur **strenger**.
* **Größte Rückgänge** (Tageswerte): Stunden-Trendfolger 9–33 %, Mean Reversion bis 72 % (Training, F, mit monatlicher
  Freigabe nach jedem Not-Aus); 4-h-Strategien 2–11 %; `trend-4h` höchstens 5,7 %.
* **Not-Aus-Ereignisse:** Die 10-%-Bremse löste häufig aus (Training F: Mean Reversion 13×, Breakout 5×, Volatility 3×).
  Hauptauswertung: simulierte Prüfung des Eigners mit Freigabe am Monatsersten (Protokoll, Nachtrag 1); zusätzlich
  „streng“ ohne jede Freigabe – das Gesamtbild ist in beiden Modi gleich (`ap20/ergebnisse_streng.md`).
  `trend-4h` hatte in keinem Abschnitt einen Not-Aus.
* **Befund „stiller Stillstand“ (für den Betrieb wichtig):** Liegt der Rückgang knapp unter der 10-%-Grenze, lehnt SORA
  jede neue Order ab (schon die Gebühr würde die Grenze reißen), der Not-Aus löst aber nicht aus – der Handel stünde still,
  ohne dass jemand es merkt. Aufgetreten in 38 Läufen der Hauptauswertung (alle Varianten zusammen; z. B. sma_cross,
  Spot, Validierung) und in 12 Läufen des strengen Modus. **Behoben für den Betrieb:**
  Der Gesundheitscheck warnt jetzt „Notbremse fast erreicht“, sobald 90 % einer Grenze erreicht sind (Gegenprobe OP12).

## 8. Belegte Verbesserungen

Vorgehen gegen Selbsttäuschung: Verbesserungen nur aus dem Training abgeleitet (höchstens 6, schriftlich begründet, vor der
Validierung festgelegt), auf der Validierung geprüft, dann **einmal** auf dem eingefrorenen Test; keine Parametersuche;
deflationierte Sharpe-Schwelle für alle 92 Versuche; Kosten-Stress; Buchhaltungsprobe; Vorlauf von 300 Kerzen ohne Handel.

| Verbesserung | Training | Validierung | Test | Urteil |
|---|---|---|---|---|
| 4-h-Kerzen statt 1 h (weniger Trades, weniger Kosten) | besser | **besser** (alle drei positiv) | nur trend-4h positiv | teilweise; nicht belastbar |
| Variante E: SORA halbiert ab 5 % Rückgang das Risiko | Rückgang kleiner | Rückgang kleiner (trend F 22 → 17 %) | Verlust kleiner (momentum F −1.690 → −1.311) | **senkt Risiko** – kein Gewinnbringer |
| Variante E: ohne KIBO/YUZU | gleich | gleich | gleich | einfacher, schneller, spart mögliche KI-Kosten |
| KIBO vor Mean Reversion | hilft | hilft | hilft | belegt – aber die Strategie bleibt unprofitabel |

**Technisch verbessert in AP-20 (getestet):**
* lokaler Datei-Import mit Herkunft, Prüfsummen, Lückenprüfung, Zwischenspeicher (`marketdata/localfile.py`);
* Backtest: Vorlauf ohne Handel, Schließen am Ende mit allen Kosten, simulierte monatliche Prüfung nach einem Not-Aus,
  Erkennung des stillen Stillstands, Zählung je Agenten-Stufe und Grund, Entscheidungszeit je Agent, Schattenmodus;
* Auswertung: Netto A/B, Tageskennzahlen, Block-Bootstrap, deflationierte Sharpe-Schwelle, Einfrieren mit Prüfsumme und
  Sperre gegen einen zweiten Blick auf die Testdaten;
* Betrieb: Warnung „Notbremse fast erreicht“; KI-Budget 0 sperrt auch Anbieter zum Preis 0.

## 9. Ergebnisse auf dem unberührten Testzeitraum

Eingefroren am 2026-10-17 10:56:55 UTC (223 Zellen, SHA-256 `08183e75…d1ba`, vor dem ersten Testlauf eingecheckt),
dann je Zelle **genau einmal** gerechnet. Es gab keinen Kandidaten; der Test beschreibt deshalb nur.

| Strategie (V0) | Test F: Netto A | Test S: Netto A | davon ab 2026-07-01 (F / S) | größter Rückgang F / S |
|---|---|---|---|---|
| trend-4h | +116 | +243 | +96 / +182 | 3,5 % / 3,4 % |
| volatility | −38 | −350 | +67 / +58 | 7,3 % / 4,8 % |
| mean_reversion | −1.456 | −926 | −168 / +90 | 16,1 % / 10,2 % |
| scalping | −1.469 | −343 | −153 / −6 | 14,8 % / 4,0 % |
| momentum-4h | −619 | −521 | −282 / −2 | 7,2 % / 7,0 % |
| breakout-4h | −895 | −629 | −189 / +42 | 11,1 % / 8,7 % |
| trend | −824 | −762 | −25 / −40 | 14,3 % / 9,8 % |
| sma_cross | −1.073 | −1.102 | −252 / −4 | 11,6 % / 13,1 % |
| momentum | −1.690 | −1.015 | −69 / +83 | 17,5 % / 11,2 % |
| breakout | −2.505 | −1.853 | −250 / +159 | 27,4 % / 21,1 % |
| *Kaufen-und-Halten* | *−3.089* | *−1.224* | | *62,8 % / 53,2 %* |

(Spalte „ab 2026-07-01“: Handelsnetto dieses Teilabschnitts = Summe der täglichen Kapitaländerungen, aus der
Einstufungstabelle.)
Über Validierung und Test zusammen hat **keine** Kombination einen Vertrauensbereich des Tagesergebnisses über 0; am
nächsten kommt `trend-4h` (Futures C: +0,47 USD/Tag [−0,33; +1,33]; Spot C: +0,43 [−0,24; +1,07]).

## 10. Grenzen der Untersuchung

* **Ein Markt, eine Quelle:** nur BTC/USD von Bitstamp. Coinbase, Bitget, ETH und andere Coins: noch nicht überprüft (B-008).
* **Futures nur genähert:** Spot-Kurse statt Perpetual-Kurs, Funding als fester Szenario-Satz statt echter Sätze;
  Liquidationen und Hebel wurden nicht simuliert (das System handelt ohne Hebel über 100 % Exposure).
* **Ausführung auf Kerzenbasis:** Ausführung zum nächsten Eröffnungskurs bzw. Stop-/Zielkurs plus Spread und Slippage
  aus dem Kostenmodell; keine Warteschlange, keine Teilausführung, keine Sekunden-Simulation (keine Tick-/Orderbuchdaten).
* **Kosten sind Annahmen** (Tarife der Standardstufe, Server 6 USD/Monat); vor einer Demo gegen die aktuellen Tarife prüfen.
* **Keine echte KI:** KIBO/YUZU sind Regeln; die KI-Variante D rechnet nur Kosten. Über die Qualität echter KI-Urteile
  sagt dieser Bericht nichts.
* **Statistik:** Wenige Trades bei den 4-h-Strategien (37–171 je Abschnitt); 92 Versuche heben die Hürde für
  Belastbarkeit (deflationierte Sharpe-Schwelle ≈ 3,3 pro Jahr). Kein Lauf erreichte sie.
* **Unabhängigkeit:** Der Entwickler (ein KI-Modell) kennt den groben Kursverlauf bis etwa Mitte 2026. Gegenmaßnahmen:
  nur Standardeinstellungen, Verbesserungen nur aus dem Training, Einfrieren vor dem Test, Teil ab 2026-07-01 gesondert.
* **Kleines Kapital:** Bei 10.000 USD frisst schon der Server (72 USD/Jahr ≈ 0,7 %) einen kleinen Vorteil auf.
* **Latenz** auf einer geteilten Cloud-Maschine gemessen, nur Richtwert.

## 11. Empfehlungen für den späteren Demo-Betrieb

1. **Keine Gewinnerwartung.** Keine Strategie hat einen belastbaren Vorteil. Ein Demo-Betrieb dient nur der technischen
   Erprobung (Daten, Abläufe, Alarme, Kostenmessung) – nicht dem Geldverdienen.
2. **Falls Demo zur Erprobung:** `trend-4h` auf **BTC Spot**, Variante **V0** (oder E für vorsichtigeres Risiko), Risiko
   je Trade 0,5 % (unverändert), kleinstes Kapital. Begründung: einzige Strategie, die in allen drei Abschnitten leicht
   positiv war, wenige Trades (geringe Kosten), nie ein Not-Aus, kleine Rückgänge. Spot statt Futures, weil Futures hier
   nur genähert sind.
3. **Nicht verwenden:** mean_reversion, breakout (1 h), sma_cross, scalping auf Stundenkerzen; Börsen-/Gebührenstufen wie
   Coinbase-Einsteiger (0,6 %).
4. **KI bleibt aus**, bis ein gepaarter Vergleich (Regeln vs. Regeln+KI auf denselben Setups, AP-33) einen Nutzen nach
   Kosten zeigt. KIBO nur zusammen mit Gegen-Trend-Strategien sinnvoll.
5. **Vor jeder Demo nachholen** (braucht Ihre Netzfreigabe, kostenlos, ca. 2 Minuten – B-008):
   *WAS:* Börsen-Schnittstellen nur lesend erreichbar machen. *WO:* Claude-Code-Cloud → Umgebung bearbeiten (Menü der
   Umgebung in der Titelleiste → „Edit“) → „Network access“ → „Allowed domains“. *WIE:* `api.exchange.coinbase.com`,
   `api.bitget.com` (für die Live-Überwachung zusätzlich `ws.bitget.com`, `ws-feed.exchange.coinbase.com`) eintragen;
   Anleitung: https://code.claude.com/docs/en/cloud-environments#network-access. *WAS DANACH:* Die nächste Sitzung lädt
   Kerzen und **echte Funding-Sätze** von Coinbase und Bitget (BTC, ETH), rechnet `trend-4h` und die Vergleichsvarianten
   dort nach (`python -m trading.research.study …`) und ergänzt diesen Bericht – ohne zu handeln.
6. **Betriebshinweise aus dieser Analyse:** Die Warnung „Notbremse fast erreicht“ im Gesundheitscheck ernst nehmen
   (stiller Stillstand). Die SORA-Einstellung der Variante E (`drawdown_cut`) ist im Paper-Dienst noch nicht als
   Einstellung herausgeführt – falls E gewünscht ist, ist das ein kleines Folgepaket.
7. **Erst danach** gemeinsame Demo-Einrichtung (AP-29, B-003) – nur nach Ihrer ausdrücklichen Freigabe; Echtgeld nur nach
   einer separaten Freigabe und zusätzlichen Sicherheitsprüfungen.

---

### Anhang: Reproduktion

```bash
# Daten (öffentlich, kostenlos; Befehl im Protokoll, Abschnitt 1) nach data/external/bitstamp-btcusd-1m/<Commit>/
PYTHONPATH=src python -m trading.research.ap20 run --period training --scenarios F,S        # ebenso validierung
PYTHONPATH=src python -m trading.research.ap20 run --period validierung --mode streng      # strenger Not-Aus-Modus
PYTHONPATH=src python -m trading.research.ap20 report --out docs/ap20/ergebnisse.md
# Der Test ist abgeschlossen: Jede eingefrorene Zelle darf nur einmal gerechnet werden (das Werkzeug verweigert einen
# zweiten Lauf). Ergebnisdateien liegen in data/ap20/ (nicht im Repository).
```
