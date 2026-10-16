"""Gegenprobe der Tests ("Mutationstest"):  python scripts/mutation_check.py [kern] [backtest] [datenbank] [--only K01 ...]

Die Frage dahinter: Wuerden die Tests einen Fehler ueberhaupt bemerken? Dazu baut dieses Skript nacheinander je EINEN
bekannten Fehler in eine KOPIE des Projekts ein (in einem temporaeren Ordner - das Projekt selbst bleibt unveraendert)
und laesst die Tests laufen. Schlagen sie fehl, wurde der Fehler ENTDECKT. Laufen sie durch, fehlt ein Test.

Gruppen:
  kern       Risikoregeln, Executor, Kosten, Datenpruefung, SORA, RIN, Pipeline, Statistik, Limits (ohne Datenbank)
  backtest   Backtesting-Engine, Datensatz-Pruefung, Vergleich
  datenbank  Sperren, Trigger, Protokollkette, Migrationen (braucht TEST_ADMIN_URL und TEST_APP_PASSWORD, siehe README)

Regeln:
  * Eine neue Sicherheitsregel bekommt hier einen Eintrag, der sie ausbaut oder abschwaecht.
  * Wird ein Eintrag NICHT entdeckt, wird ein Test ergaenzt - nie der Eintrag entfernt (ausser der Code war nachweislich tot).
  * Passt die gesuchte Textstelle nicht mehr (Code wurde geaendert), bricht das Skript ab: Eintrag anpassen.

Rueckgabewert 0 = alle eingebauten Fehler entdeckt, 1 = mindestens einer nicht.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
Edit = Tuple[str, str, str]  # (Datei, alter Text, neuer Text)
Mutant = Tuple[str, str, List[Edit], List[str]]  # (Kennung, Beschreibung, Aenderungen, Testdateien)

OR = "src/trading/safety/order_risk.py"
PE = "src/trading/execution/paper_executor.py"
CO = "src/trading/execution/costs.py"
DV = "src/trading/safety/data_validator.py"
CA = "src/trading/marketdata/candles.py"
SO = "src/trading/agents/sora_rules.py"
RI = "src/trading/agents/rin_rules.py"
PI = "src/trading/pipeline.py"
ST = "src/trading/audit/stats.py"
PF = "src/trading/audit/performance.py"
LI = "src/trading/safety/limits.py"
EL = "src/trading/audit/event_log.py"
CF = "src/trading/config.py"
IN = "src/trading/__init__.py"
KS = "src/trading/safety/kill_switch.py"
PK = "src/trading/persistence/kill_switch.py"
DB = "src/trading/persistence/db.py"
EN = "src/trading/backtest/engine.py"
DA = "src/trading/backtest/data.py"
CM = "src/trading/backtest/compare.py"
EX = "src/trading/persistence/pg_executor.py"
PL = "src/trading/persistence/pg_event_log.py"
MG = "src/trading/persistence/migrate.py"
S1 = "db/migrations/001_initial.sql"
S2 = "db/migrations/002_instruments_and_exposure.sql"
S3 = "db/migrations/003_kill_switch.sql"
S4 = "db/migrations/004_order_replay.sql"
S5 = "db/migrations/005_account_rights.sql"
AC = "src/trading/persistence/accounts.py"
AIP = "src/trading/ai/pricing.py"
AIB = "src/trading/ai/budget.py"
AIO = "src/trading/ai/orchestrator.py"
AIA = "src/trading/ai/activation.py"
AUC = "src/trading/audit/costs.py"
AUV = "src/trading/audit/ai_value.py"
PAB = "src/trading/persistence/ai_budget.py"
S6 = "db/migrations/006_ai_budget.sql"

ALL_TESTS: List[str] = []  # leer = ganze Testsuite
BT = ["tests/test_backtest_data.py", "tests/test_backtest_engine.py"]
STT = ["tests/test_strategies.py"]  # Strategie-Bibliothek (AP-16)
KYT = ["tests/test_kibo_yuzu.py", "tests/test_backtest_engine.py"]  # Marktphasen, KIBO, YUZU (AP-17)
DBT = ["tests/test_dashboard.py"]  # Dashboard (AP-23/24)
RTT = ["tests/test_runtime_engine.py"]  # Laufzeit-Engine (AP-21)
RST = ["tests/test_research.py"]  # Forschungsrahmen (AP-18/19)
BRT = ["tests/test_exchange_rules.py"]  # Boersenregeln (AP-13)
BXR = "src/trading/domain/exchange_rules.py"
RSP, RSR, RSE, RSV = ("src/trading/research/splits.py", "src/trading/research/registry.py",
                      "src/trading/research/experiment.py", "src/trading/research/evaluation.py")
RTE = "src/trading/runtime/engine.py"
DSH_API, DSH_STATE = "src/trading/dashboard/api.py", "src/trading/dashboard/state.py"
KB, YZ, RG, AR = ("src/trading/agents/kibo_rules.py", "src/trading/agents/yuzu_rules.py",
                  "src/trading/analysis/regime.py", "src/trading/agents/ai_review.py")
SI, SL, SB = ("src/trading/analysis/indicators.py", "src/trading/strategies/library.py",
              "src/trading/strategies/benchmarks.py")
BT_PG = BT + ["tests/test_backtest_pg.py"]
# Schnelle Dateien zuerst: pytest bricht beim ersten Fehlschlag ab (-x), die Mehrprozess-Tests dauern am laengsten.
PG = ["tests/test_pg_accounts.py", "tests/test_pg_schema.py", "tests/test_pg_executor.py", "tests/test_pg_restart.py",
      "tests/test_pg_kill_switch.py", "tests/test_backtest_pg.py", "tests/test_pg_multiprocess.py"]
RC = ["tests/test_db_keeper.py", "tests/test_pg_reconnect.py"]  # Wiederverbindung (AP-11)
AIT = ["tests/test_ai_orchestration.py", "tests/test_operating_costs.py"]  # KI-Kosten (D-016), ohne Datenbank
PGB = ["tests/test_pg_ai_budget.py"]  # KI-Budget in der Datenbank

KERN: List[Mutant] = []


def m(key: str, name: str, path: str, old: str, new: str) -> None:
    KERN.append((key, name, [(path, old, new)], ALL_TESTS))


# ---- Risikorechnung
m("K01","Risiko je Trade nicht begrenzt",OR,"if risk_pct > limits.max_risk_per_trade:","if False:")
m("K02","Gesamtrisiko gegen Equity VOR Kosten statt danach",OR,"total_risk_after = (portfolio.open_risk + worst - entry_fee) / equity_after","total_risk_after = (portfolio.open_risk + worst - entry_fee) / portfolio.equity")
m("K03","Gesamtrisiko 1 % zu locker",OR,"if total_risk_after > limits.max_portfolio_risk:","if total_risk_after > limits.max_portfolio_risk * Decimal('1.01'):")
m("K04","Gesamtrisiko-Grenze abgeschaltet",OR,"if total_risk_after > limits.max_portfolio_risk:","if False:")
m("K05","Exposure-Grenze abgeschaltet",OR,"if exposure_after > limits.max_gross_exposure:","if False:")
m("K06","Stress-Grenze abgeschaltet",OR,"if stress_after > limits.max_stress_loss:","if False:")
m("K07","Stress: Liquidation im Schock ignoriert",OR,"if liquidation is not None and is_beyond(side, shocked, liquidation):","if False:")
m("K08","Stress: Schock in die falsche Richtung",OR,"shocked = mark * (1 - side.sign * shock)","shocked = mark * (1 + side.sign * shock)")
m("K09","Stress: ohne Ausstiegsgebuehr",OR,"return cost_up(side.sign * (mark - exit_price) * quantity) + costs.fee(quantity * exit_price)","return cost_up(side.sign * (mark - exit_price) * quantity)")
m("K10","Veraltete Bewertung wird hingenommen",OR,"if not portfolio.valuation_complete:","if False:")
m("K11","Margin-Pruefung ohne Einstiegsgebuehr",OR,"if margin + entry_fee > portfolio.free_margin:","if margin > portfolio.free_margin:")
m("K12","Liquidationspuffer ignoriert (Faktor 1)",OR,"limits.liquidation_buffer * abs(fill - stop)","abs(fill - stop)")
m("K13","Drawdown-Grenze: >= wird zu >",OR,"if drawdown >= limits.max_drawdown:","if drawdown > limits.max_drawdown:")
m("K14","Tagesverlust-Grenze: >= wird zu >",OR,"if day_loss >= limits.max_daily_loss:","if day_loss > limits.max_daily_loss:")
m("K15","Futures ohne Funding-Satz erlaubt",OR,"if market is MarketType.FUTURES and funding is None:","if False:")
m("K16","Equity danach ohne Spread-Kosten",OR,"equity_after = portfolio.equity - entry_fee - spread_cost","equity_after = portfolio.equity - entry_fee")
m("K17","Short-Liquidationskurs ohne Erhaltungsmargin",OR,"return entry * (1 + 1 / leverage - maintenance_margin_rate)","return entry * (1 + 1 / leverage)")
m("K18","Zweite Position im selben Instrument erlaubt",OR,"if setup.instrument in portfolio.open_keys:","if False:")
m("K19","Eine Position zu viel erlaubt",OR,"if portfolio.open_count >= limits.max_open_positions:","if portfolio.open_count > limits.max_open_positions:")
m("K20","Spot mit Hebel erlaubt",OR,"if leverage != 1:","if False:")
m("K21","Short: 'jenseits' nie erkannt (Stop/Liquidation)",OR,"return price <= level if side is Side.LONG else price >= level","return price <= level if side is Side.LONG else False")
m("K22","Gesamtrisiko ohne Abzug der bezahlten Gebuehr (zu streng) ",OR,"(portfolio.open_risk + worst - entry_fee) / equity_after","(portfolio.open_risk + worst) / equity_after")
m("K23","Exposure danach gegen Equity vor Kosten",OR,"exposure_after = (portfolio.gross_exposure + quantity * mark) / equity_after","exposure_after = (portfolio.gross_exposure + quantity * mark) / portfolio.equity")
m("K24","Hebel ueber Limit erlaubt",OR,"    if leverage > limits.max_leverage or (rules","    if False or (rules")
m("K25","Spot-Short erlaubt (Risikomanager)",OR,"        if side is Side.SHORT:\n            reasons.append(RejectReason.SPOT_SHORT_NOT_ALLOWED)","        if False:\n            reasons.append(RejectReason.SPOT_SHORT_NOT_ALLOWED)")
m("K26","NaN/Infinity wird nicht abgefangen",OR,"    if not all(is_finite_decimal(x) for x in numbers):","    if False:")
# ---- Executor
m("P01","Hoechststand faellt mit (Drawdown-Bremse wirkungslos)",PE,"self._peak_equity = max(self._peak_equity, equity)","self._peak_equity = equity")
m("P02","Freie Margin ignoriert unrealisierte Verluste",PE,"free_margin=self._balance - margin_used + min(unrealized, ZERO),","free_margin=self._balance - margin_used,")
m("P03","Tageswechsel setzt Tagesstart nicht neu",PE,"            self._day = today\n            self._day_start_equity = equity_now","            self._day = today")
m("P04","Tageswechsel auch rueckwaerts",PE,"if today > self._day:","if today != self._day:")
m("P05","Snapshot-Kurs nicht als Bewertung uebernommen (Order)",PE,"        self._marks[snapshot.instrument] = Mark(snapshot.mid, snapshot.timestamp)\n\n        # 3.","\n        # 3.")
m("P06","Funding mehrfach buchbar",PE,"if position is None or event.time <= position.last_funding_ts:","if position is None:")
m("P07","Funding-Zeitstempel nicht fortgeschrieben",PE,"position, funding_paid=position.funding_paid + cost, last_funding_ts=event.time","position, funding_paid=position.funding_paid + cost")
m("P08","Stop-Ausstieg zur Maker-Gebuehr",PE,"taker=reason is not ExitReason.TAKE_PROFIT","taker=False")
m("P09","Kursluecke wird zum Stop-Kurs gefuellt",PE,"raw = candle.open  # Luecke","raw = stop  # Luecke")
m("P10","Teil-Kerze: Ziel zaehlt trotzdem",PE,"        if candle.open_time < pos.opened_at:\n","        if False:\n")
m("P11","Stop UND Ziel in einer Kerze: Ziel gewinnt",PE,"        elif is_beyond(side, adverse, stop):\n            raw = stop  # auch","        elif is_beyond(side, adverse, stop) and not is_beyond(side, tp, favorable):\n            raw = stop  # auch")
m("P12","Liquidation kostet nur den Kursverlust",PE,"gross, exit_fee = -pos.margin, ZERO","gross, exit_fee = gain_down(pos.side.sign * (exit_price - pos.entry_price) * pos.quantity), ZERO")
m("P13","Liquidation wird nie erkannt",PE,"if liq is not None and is_beyond(side, price, liq):","if False:")
m("P14","Noch laufende Kerze wird verarbeitet",PE,"problems.append(RejectReason.FUTURE_TIMESTAMP)  # noch nicht abgeschlossen","pass")
m("P15","Doppelte/aeltere Kerze wird verarbeitet",PE,"if last_close is not None and candle.open_time < last_close:\n                problems.append(","if False:\n                problems.append(")
m("P16","Veraltete Bewertungskurse nicht erkannt",PE,"if mark is None or (now - mark.time).total_seconds() > self._limits.max_mark_age_s:","if mark is None:")
m("P17","Kein Zuruecksetzen nach Fehler (nicht atomar)",PE,"            except BaseException:\n                self._restore(backup)\n                raise","            except BaseException:\n                raise")
m("P18","Doppelte Order-ID wird ausgefuehrt",PE,"if intent.client_order_id in self._known_orders:","if False:")
# ---- Wiederholte Order liefert das urspruengliche Ergebnis (AP-08)
m("O01","Gleiche ID mit anderem Inhalt bekommt das fremde Ergebnis",PE,"if known.result is None or known.fingerprint is None or fingerprint != known.fingerprint:","if known.result is None:")
m("O02","Order-ID ohne belegtes Ergebnis wird als Wiederholung beantwortet",PE,"if known.result is None or known.fingerprint is None or fingerprint != known.fingerprint:","if known.result is None or fingerprint != known.fingerprint:")
m("O03","Fingerabdruck ohne Menge und Hebel",PE,"        text = json.dumps(to_jsonable(intent),","        text = json.dumps(to_jsonable(intent.setup),")
m("O04","Wiederholung wird nicht protokolliert",PE,"        self._emit(\n            EventType.ORDER_REPLAYED,","        (lambda *a: None)(\n            EventType.ORDER_REPLAYED,")
m("O05","Wiederholung ist nicht als solche gekennzeichnet",PE,"        return dataclasses.replace(original, risk=None, replayed=True)","        return dataclasses.replace(original, risk=None)")
m("O06","Order-ID bleibt nach zurueckgesetzter Operation vergeben",PE,"            self._known_orders.pop(oid, None)","            pass")
m("O07","Pipeline meldet eine Wiederholung als neu ausgefuehrt",PI,"        if result.replayed:","        if False:")
# ---- Groesse unter der Sperre neu berechnen (AP-09, Audit M8)
m("Q01","Pipeline: SORA rechnet mit vorab gelesenem Zustand (ausserhalb der Sperre)",PI,"        try:\n            result = self._executor.submit_sized(setup.setup_id, ctx.snapshot, sizer)","        stale = self._executor.portfolio_state()\n        try:\n            result = self._executor.submit_sized(setup.setup_id, ctx.snapshot, lambda _p: sizer(stale))")
m("Q03","Pipeline: SORA-Absturz wird nicht als Fehler gemeldet",PI,"            if not sizing_box:  # SORA ist abgestuerzt\n                return self.reject_setup(setup, \"SORA\", (RejectReason.AGENT_ERROR,))","            if not sizing_box:  # SORA ist abgestuerzt\n                return self.reject_setup(setup, \"SORA\", (RejectReason.AGENT_REJECTED,))")
m("Q02","Executor: Auftrag mit fremder Order-ID wird ausgefuehrt",PE,"        if not isinstance(intent, OrderIntent) or intent.client_order_id != client_order_id:","        if not isinstance(intent, OrderIntent):")
m("P19","Schliessen mit ungueltigen Marktdaten",PE,"        if not validation.ok:\n            self._check_data(validation.reasons)\n            return refuse(*validation.reasons)","        if False:\n            return refuse(*validation.reasons)")
m("P20","Funding: auch Shorts zahlen bei positivem Satz",PE,"cost = cost_up(position.side.sign * event.rate * position.quantity * event.mark_price)","cost = cost_up(event.rate * position.quantity * event.mark_price)")
m("P21","Funding auf Spot-Instrument akzeptiert",PE,"problems.append(RejectReason.BAD_FUNDING)  # Funding gibt es nur bei Futures","pass")
m("P22","Schliessen mit beliebigem Grund (z. B. als Take-Profit)",PE,"if reason not in _REQUESTED_EXITS:","if False:")
m("P23","Datenluecke wird nicht protokolliert",PE,"if last_close is not None and candle.open_time > last_close:","if False:")
m("P24","Einstiegsgebuehr wird nicht abgebucht",PE,"        self._balance -= decision.entry_fee\n","")
m("P25","Marktdaten fuer ein anderes Instrument akzeptiert (Order)",PE,"        if snapshot.instrument != setup.instrument:\n            return self._reject(intent, (RejectReason.SYMBOL_MISMATCH,), None)","")
m("P26","Ungueltige Marktdaten bei Order akzeptiert",PE,"        if not validation.ok:\n            self._check_data(validation.reasons)\n            return self._reject(intent, validation.reasons, None)\n","")
m("P27","Ereignisse sofort statt am Ende (bei Fehler bleibt Protokoll stehen)",PE,"        self._pending.append((event_type, payload))","        self._log.append(event_type, payload)")
m("P28","Zukuenftiges Funding wird gebucht",PE,"        if not problems and (event.time - self._clock.now()).total_seconds() > FUTURE_TOLERANCE_S:\n            problems.append(RejectReason.FUTURE_TIMESTAMP)","")
m("P29","Auffaellige Kerze wird nicht protokolliert",PE,"            > self._limits.max_price_jump\n        ):","            > self._limits.max_price_jump * 1000\n        ):")
m("P30","Schliessen: Kurs nicht als Bewertung uebernommen",PE,"        self._marks[instrument] = Mark(snapshot.mid, snapshot.timestamp)\n        price = self._costs.market_exit_price","        price = self._costs.market_exit_price")
# ---- Kosten
m("C01","Negatives Funding senkt das geplante Risiko",CO,"cost_up(abs(funding_rate) * quantity","cost_up(funding_rate * quantity")
m("C02","Hoechstverlust ohne Ausstiegsgebuehr",CO,"fees = self.fee(quantity * entry_price) + self.fee(quantity * exit_price)","fees = self.fee(quantity * entry_price)")
m("C03","Short-Einstieg zum Ask statt Bid",CO,"            return snapshot.ask * (1 + slip)\n        return snapshot.bid * (1 - slip)","            return snapshot.ask * (1 + slip)\n        return snapshot.ask * (1 - slip)")
m("C04","Stop-Slippage beim Short zum Vorteil",CO,"return raw_stop_price * (1 + slip)","return raw_stop_price * (1 - slip)")
m("C05","Kosten werden abgerundet",CO,"return amount.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING)","return amount.quantize(MONEY_QUANTUM, rounding=ROUND_FLOOR)")
m("C06","Funding-Puffer ignoriert die Anzahl Intervalle",CO," * self.funding_buffer_intervals)",")")
m("C07","Long-Ausstieg per Signal zum Ask statt Bid",CO,"            return snapshot.bid * (1 - slip)\n        return snapshot.ask * (1 + slip)","            return snapshot.ask * (1 - slip)\n        return snapshot.ask * (1 + slip)")
m("C08","Gewinne werden aufgerundet",CO,"return amount.quantize(MONEY_QUANTUM, rounding=ROUND_FLOOR)","return amount.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING)")
m("C09","Hoechstverlust ohne Stop-Slippage",CO,"        exit_price = self.stop_exit_price(side, stop_price)\n        price_loss","        exit_price = stop_price\n        price_loss")
# ---- Datenpruefung
m("V01","Zukunfts-Toleranz 200 s statt 2 s",DV,"FUTURE_TOLERANCE_S = 2 ","FUTURE_TOLERANCE_S = 200 ")
m("V02","Kurssprung-Filter prueft nur Anstiege",DV,"and abs(s.last - ref[1]) / ref[1] > lim.max_price_jump","and (s.last - ref[1]) / ref[1] > lim.max_price_jump")
m("V03","Referenzkurs verfaellt nie",DV,"if 0 <= ref_age <= self._reference_max_age_s \\","if 0 <= ref_age \\")
m("V04","Aeltere Daten nach neueren akzeptiert",DV,"if ref is not None and s.timestamp < ref[0]:","if False:")
m("V05","Veraltete Daten akzeptiert",DV,"if age > lim.max_data_age_s:","if False:")
m("V06","Futures-Snapshot ohne Funding akzeptiert",DV,"            if s.funding_rate is None:\n                reasons.append(RejectReason.FUNDING_MISSING)\n            elif","            if False:\n                reasons.append(RejectReason.FUNDING_MISSING)\n            elif s.funding_rate is not None and")
m("V07","Zu weiter Spread akzeptiert",DV,"elif s.spread_bps > lim.max_spread_bps:","elif False:")
m("V08","Gekreuztes Orderbuch akzeptiert",DV,"            if s.bid > s.ask:\n                reasons.append(RejectReason.CROSSED_BOOK)\n            elif","            if")
m("V09","Fehlerhafte Kerze im Snapshot akzeptiert",DV,"if s.candle is not None and candle_problems(s.candle, s.instrument):","if False:")
m("V10","Abgelehnter Kurs wird trotzdem Referenz",DV,"        if reasons:\n            return ValidationResult(False, tuple(reasons))\n\n        self._ref","        self._ref[s.instrument] = (s.timestamp, s.last)\n        if reasons:\n            return ValidationResult(False, tuple(reasons))\n\n        self._ref")
m("A01","Kerzen-Volumen < 0 erlaubt",CA," or candle.volume < 0:",":")
m("A02","Schief liegende Kerze akzeptiert",CA,"problems.append(RejectReason.CANDLE_MISALIGNED)","pass")
m("A03","Hoch/Tief passen nicht zu Open/Close",CA,"elif not (h >= max(o, c) and lo <= min(o, c) and h >= lo)","elif not (h >= lo)")
m("A04","Luecken in der Reihe nicht gemeldet",CA,"            if c.open_time > prev.close_time:","            if False:")
m("A06","Kerze eines fremden Instruments akzeptiert",CA,"elif expected is not None and candle.instrument != expected:","elif False:")
m("A07","Kerze ohne Zeitzone akzeptiert",CA,"    if not is_aware(candle.open_time) or not interval_ok:","    if not interval_ok:")
# ---- SORA
m("S01","SORA: Liquidations-Sicherheitsfaktor nur 1,25",SO,"self._liq_factor = max(policy.liquidation_buffer, cfg.liquidation_safety)","self._liq_factor = policy.liquidation_buffer")
m("S02","SORA: Mindestgroesse nicht geprueft",SO,"        if quantity < minimum:","        if quantity <= 0:")
m("S03","SORA: Endkontrolle uebersprungen",SO,"        if not decision.approved:\n            return block(decision.reasons)\n\n        note","        note")
m("S04","SORA: Stress-Grenze nicht in der Mengenformel",SO,"return min(by_trade, by_risk, by_exposure, by_stress)","return min(by_trade, by_risk, by_exposure)")
m("S05","SORA: Exposure-Grenze nicht in der Mengenformel",SO,"return min(by_trade, by_risk, by_exposure, by_stress)","return min(by_trade, by_risk, by_stress)")
m("S06","SORA: Gesamtrisiko nicht in der Mengenformel",SO,"return min(by_trade, by_risk, by_exposure, by_stress)","return min(by_trade, by_exposure, by_stress)")
m("S07","SORA: nutzt 100 % der freien Margin",SO,"portfolio.free_margin * cfg.margin_utilization /","portfolio.free_margin /")
m("S08","SORA: Funding nicht im Verlust je Stueck",SO,"loss_unit = costs.worst_case_loss(side, D(1), fill, setup.stop_loss, quote.funding_rate)","loss_unit = costs.worst_case_loss(side, D(1), fill, setup.stop_loss, None)")
m("S09","SORA: eigenes Ziel ueber dem Limit erlaubt",SO,"if cfg.risk_per_trade > policy.max_risk_per_trade:","if False:")
m("S10","SORA: immer hoechster Hebel",SO,"leverage = next((lv for lv in candidates if margin_for(lv) >= limit_for(lv)), candidates[-1])","leverage = candidates[-1]")
m("S11","SORA: eigenes Hebel-Maximum ignoriert",SO,"self._max_leverage = min(policy.max_leverage, cfg.max_leverage)","self._max_leverage = policy.max_leverage")
m("S12","SORA: Marktdaten eines anderen Instruments",SO,"        if snap.instrument != setup.instrument:\n            return block([R.SYMBOL_MISMATCH])","")
# ---- RIN
m("R01","RIN: Spread nicht in den Round-Trip-Kosten",RI,"c.slippage_bps + c.stop_slippage_bps + (c.taker_fee * 2) * 10000 + snap.spread_bps","c.slippage_bps + c.stop_slippage_bps + (c.taker_fee * 2) * 10000")
m("R02","RIN: Netto-CRV ohne Gebuehr am Ziel",RI,"            - c.fee(setup.take_profit, taker=False)\n","")
m("R03","RIN: Geometrie nur gegen Entry",RI,'for ref_name, ref in (("Entry", setup.entry), ("Marktkurs", fill)):','for ref_name, ref in (("Entry", setup.entry),):')
m("R04","RIN: Handelsuniversum nicht geprueft",RI,"if not self._universe.allows(instrument):","if False:")
m("R05","RIN: veraltetes Setup akzeptiert",RI,"if age > self._config.max_setup_age_s:","if False:")
m("R06","RIN: Entry weit vom Markt akzeptiert",RI,"if abs(setup.entry - mid) / mid > cfg.max_entry_deviation:","if False:")
m("R07","RIN: Kurs passt nicht zur letzten Kerze",RI,"if is_positive_decimal(tick) and abs(tick - last) / last > self._config.max_candle_tick_deviation:","if False:")
m("R08","RIN: zu weiter Stop akzeptiert",RI,"if stop_dist / fill > cfg.max_stop_pct:","if False:")
m("R09","RIN: Setup aus der Zukunft akzeptiert",RI,"if age < -2:","if False:")
m("R10","RIN: ueberlappende Kerzen akzeptiert",RI,"if prev is not None and cd.open_time < prev.close_time:","if False:")
m("R11","RIN: Marktdaten eines anderen Instruments",RI,"if snap.instrument != instrument:","if False:")
m("R12","RIN: Spot-Short akzeptiert",RI,"if instrument.market_type is MarketType.SPOT and setup.side is Side.SHORT:","if False:")
m("R13","RIN: Netto-CRV-Minimum abgeschaltet",RI,"if net_rr < cfg.min_net_reward_risk:","if False:")
m("R14","RIN: Mindest-Stop ohne Kostenbezug",RI,"min_bps = max(cfg.min_stop_bps, cfg.min_stop_cost_multiple * roundtrip_bps)","min_bps = cfg.min_stop_bps")
m("R15","RIN: Marktdaten-Pruefung uebersprungen",RI,"        for code in validation.reasons:\n            fail(code, \"Marktdaten: %s\" % code.value)","")
# ---- Pipeline
m("I01","Pipeline: KIBO-Veto ignoriert",PI,'        if blocked is None:\n            blocked = self._opinion(setup, "KIBO", lambda: self._kibo.evaluate(setup, ctx))\n',"")
m("I02","Pipeline: YUZU-Veto ignoriert",PI,'        if blocked is None:\n            blocked = self._opinion(setup, "YUZU", lambda: self._yuzu.evaluate(setup, ctx))\n',"")
m("I03","Pipeline: abgestuerzter Agent zaehlt als Zustimmung",PI,"        if opinion is None:\n            return self.reject_setup(setup, stage, (RejectReason.AGENT_ERROR,))","        if opinion is None:\n            return None")
m("I04","Pipeline: Fehler der Ausfuehrung reisst alles ab",PI,"        except Exception as exc:  # noqa: BLE001 - z. B. Datenbankfehler: zurueckgerollt, nichts wurde gehandelt","        except ZeroDivisionError as exc:")
m("I05","Pipeline: Vorschlaege werden nicht protokolliert",PI,"        for setup in setups:\n            self._aki.record_setup(setup)\n","")
m("I06","Pipeline: SORA-Ablehnung ignoriert",PI,"            if not sizing.approve:\n                return None","            if False:\n                return None")
m("I07","Pipeline: ohne Marktdaten wird trotzdem verarbeitet (Ausstieg)",PI,"        if ctx is None:\n            return self._exit_blocked(signal, (RejectReason.NO_MARKET_DATA,))\n        snapshot = ctx.snapshot","        snapshot = ctx.snapshot if ctx is not None else None")
m("I08","Pipeline: Ausstiegssignale nicht protokolliert",PI,"        for signal in signals:\n            self._aki.record_exit_signal(signal)\n","")
m("I09","Pipeline: Blockade ohne Protokoll",PI,"        self._aki.record_blocked(setup, stage, reasons, details)\n","")
# ---- Pipeline-Reihenfolge nach Vorgabe des Eigners (AP-12, D-017): RIN -> SORA-Vorpruefung -> KIBO/YUZU -> SORA endgueltig
m("W01","Pipeline: SORA-Vorpruefung entfaellt",PI,"        if blocked is None:\n            blocked = self._sizing_precheck(setup, ctx)\n","")
m("W02","Pipeline: alte Reihenfolge (SORA-Vorpruefung erst nach YUZU)",PI,"        if blocked is None:\n            blocked = self._sizing_precheck(setup, ctx)\n        if blocked is None:\n            blocked = self._opinion(setup, \"KIBO\", lambda: self._kibo.evaluate(setup, ctx))\n        if blocked is None:\n            blocked = self._opinion(setup, \"YUZU\", lambda: self._yuzu.evaluate(setup, ctx))\n","        if blocked is None:\n            blocked = self._opinion(setup, \"KIBO\", lambda: self._kibo.evaluate(setup, ctx))\n        if blocked is None:\n            blocked = self._opinion(setup, \"YUZU\", lambda: self._yuzu.evaluate(setup, ctx))\n        if blocked is None:\n            blocked = self._sizing_precheck(setup, ctx)\n")
m("W03","Pipeline: Ablehnung der SORA-Vorpruefung ignoriert",PI,"        self._aki.record_sizing_precheck(sizing)\n        if not sizing.approve:","        self._aki.record_sizing_precheck(sizing)\n        if False:")
m("W04","Pipeline: unlesbarer Zustand vor der Vorpruefung wird uebergangen",PI,"            self._aki.record_system_error(\"EXECUTOR\", repr(exc))\n            return self.reject_setup(setup, \"EXECUTOR\", (RejectReason.EXECUTION_ERROR,))","            self._aki.record_system_error(\"EXECUTOR\", repr(exc))\n            return None")
m("W05","Pipeline: Absturz in der SORA-Vorpruefung zaehlt als Zustimmung",PI,"        if sizing is None:\n            return self.reject_setup(setup, \"SORA\", (RejectReason.AGENT_ERROR,))","        if sizing is None:\n            return None")
m("W06","Pipeline: SORA-Vorpruefung nicht protokolliert",PI,"        self._aki.record_sizing_precheck(sizing)\n","")
# ---- Statistik
m("T01","Gewinnrate zaehlt Nullsummen-Trades als Gewinn",ST,"winners = [t.net_pnl for t in trades if t.net_pnl > 0]","winners = [t.net_pnl for t in trades if t.net_pnl >= 0]")
m("T02","Drawdown gegen ersten Punkt statt Startkapital",PF,"    peak = initial_equity\n    peak_time","    peak = points[0].equity if points else initial_equity\n    peak_time")
m("T03","Abstimmung Endkapital abgeschaltet",PF,"if (points or open_positions) and final != initial_equity + realized + unrealized:","if False:")
m("T04","Gebuehren offener Positionen fehlen im Bericht",PF,"total_fees=trade_stats.total_fees + open_fees,","total_fees=trade_stats.total_fees,")
m("T05","Volatilitaet mit n statt n-1",PF,") / (len(returns) - 1)",") / len(returns)")
m("T06","Verlustserie zaehlt Nullsummen-Trades",ST,"streak = streak + 1 if t.net_pnl < 0 else 0","streak = streak + 1 if t.net_pnl <= 0 else 0")
m("T07","Profit-Faktor aus Brutto statt Netto",ST,"winners = [t.net_pnl for t in trades if t.net_pnl > 0]","winners = [t.gross_pnl for t in trades if t.net_pnl > 0]")
m("T08","Funding offener Positionen fehlt im Bericht",PF,"total_funding=trade_stats.total_funding + open_funding,","total_funding=trade_stats.total_funding,")
m("T09","Drawdown aus Trades statt aus bewerteter Kurve: Unterwasserzeit falsch",PF,"            underwater_since = peak_time if peak_time is not None else points[0].time","            underwater_since = p.time")
# ---- Limits / Konfiguration / Protokoll
m("L01","Hebel-Obergrenze nicht geprueft",LI,'            ("max_leverage", HARD_MAX_LEVERAGE),\n',"")
m("L02","Exposure-Obergrenze nicht geprueft",LI,'            ("max_gross_exposure", HARD_MAX_GROSS_EXPOSURE),\n',"")
m("L03","Stress-Obergrenze nicht geprueft",LI,'            ("max_stress_loss", HARD_MAX_STRESS_LOSS),\n',"")
m("L04","Schwaecherer Stress-Schock erlaubt",LI,"if not (HARD_MIN_STRESS_SHOCK <= self.stress_shock < 1):","if not (0 < self.stress_shock < 1):")
m("L05","Gesamtrisiko-Obergrenze nicht geprueft",LI,'            ("max_portfolio_risk", HARD_MAX_PORTFOLIO_RISK),\n',"")
m("L06","Bewertungsalter-Obergrenze nicht geprueft",LI,'            ("max_mark_age_s", HARD_MAX_MARK_AGE_S),\n',"")
m("E01","Beschaedigte Protokolldatei wird beim Start akzeptiert",EL,"            if not result.ok:\n                raise EventLogCorruptedError(","            if False:\n                raise EventLogCorruptedError(")
m("E02","Erkennung von Boersen-Schluesseln abgeschaltet",CF,"    if offending:\n","    if False:\n")
m("E03","Anderer Modus als PAPER erlaubt",CF,"    if raw_mode != TradingMode.PAPER.value:","    if False:")
# ---- Zentraler PAPER-Schutz (AP-06, Audit M3)
m("E04","PAPER-Schutz beim Import abgeschaltet",IN,"\n_enforce_paper_process()\n","\n")
m("E05","Schluessel-Namen nur in Grossschrift erkannt",CF,'return re.sub(r"[^A-Z0-9]+", "_", split.upper()).strip("_")','return re.sub(r"[^A-Z0-9]+", "_", split).strip("_")')
m("E06","Boersen-Praefixe (CDP, Binance, CCXT ...) nicht erkannt",CF,"    return _mentions_exchange(norm.split(\"_\")) and any(p in norm for p in _SECRET_PARTS)","    return False")
m("E11","Boersenname nur am Anfang des Namens erkannt",CF,"    return any(p in _EXCHANGE_PREFIXES or p.startswith(_LONG_EXCHANGE_PREFIXES) for p in parts)","    return parts[0] in _EXCHANGE_PREFIXES")
m("E12","camelCase-Namen nicht zerlegt",CF,'    split = re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", name)',"    split = name")
m("E07","Allgemeine Namen (API_KEY ...) nicht erkannt",CF," or norm in _GENERIC_NAMES"," ")
m("E08","Schluessel-Inhalte unter fremdem Namen nicht erkannt",CF,"if _is_credential_name(name) or _CREDENTIAL_VALUE.search(value):","if _is_credential_name(name):")
m("E09","TRADING_MODE in Kleinschreibung ignoriert",CF,'if _normalized(k) == "TRADING_MODE"]','if k == "TRADING_MODE"]')
m("E10","Passphrase unter beliebigem Namen nicht erkannt",CF,' or "PASSPHRASE" in norm:',':')
# ---- Globaler Not-Aus (AP-07)
m("N01","Not-Aus vor der Datenpruefung nicht beachtet",PE,"        halted = self._halted_reasons()\n        if halted is not None:\n            return self._reject(intent, halted, None)\n\n        # 2. Marktdaten","        # 2. Marktdaten")
m("N02","Not-Aus nach der Bewertung nicht beachtet",PE,"        halted = self._halted_reasons()\n        if halted is not None:\n            return self._reject(intent, halted, None)\n        decision","        decision")
m("N03","Ausgeloester Not-Aus wird ignoriert",PE,"        if not status.halted:\n            return None","        if True:\n            return None")
m("N04","Datenfehler loesen keinen Not-Aus aus",PE,"        if bad:\n            self._trip(HaltReason.DATA_ERROR","        if False:\n            self._trip(HaltReason.DATA_ERROR")
m("N05","Wiederholte Kerzen loesen den Not-Aus aus",PE,"    RejectReason.BAD_CANDLE, RejectReason.CANDLE_MISALIGNED,","    RejectReason.BAD_CANDLE, RejectReason.CANDLE_MISALIGNED, RejectReason.DUPLICATE_CANDLE,")
m("N06","Notbremsen loesen keinen Not-Aus aus",PE,"        for brake in brake_reasons_for(equity, day_start, peak, self._limits):","        for brake in ():")
m("N07","Protokollfehler loest keinen Not-Aus aus",PE,'                    self._trip_silently(HaltReason.PROTOCOL_ERROR, "Protokoll nicht schreibbar: %s" % type(exc).__name__)',"                    pass")
m("N08","Freigabe ohne Bestaetigungstext",KS,"    if confirmation != RELEASE_CONFIRMATION:","    if False:")
m("N09","Freigabe ohne Namen",KS,"    if not name:\n","    if False:\n")
m("N10","Unlesbarer Not-Aus gilt als frei",PE,'            return HaltStatus(True, None, None, "Not-Aus nicht lesbar: %s" % type(exc).__name__)',"            return HaltStatus(False)")
m("N11","Zweiter Ausloeser ueberschreibt den ersten Grund",KS,"            if self._state.halted:\n                return False\n","")
m("N12","Tagesverlust-Bremse loest den Not-Aus nicht mehr aus (eine Definition fuer beide)",OR,"        if day_loss >= limits.max_daily_loss:","        if day_loss > limits.max_daily_loss * 2:")
m("N13","Verlust der Mitternachts-Kerze zaehlt fuer keinen Tag",PE,"        if now.date() > self._day:\n            # Tageswechsel","        if False:\n            # Tageswechsel")
m("N14","Notbremsen vor statt nach der Order gemessen",OR,"    reasons.extend(brake_reasons_for(equity_after, portfolio.day_start_equity","    reasons.extend(brake_reasons_for(portfolio.equity, portfolio.day_start_equity")
m("N15","Geteilter Not-Aus wird waehrend der Order nicht festgehalten",KS,"    def hold(self) -> ContextManager[object]:\n        return self._lock\n","")
m("N16","Fehler beim Ausloesen bricht Stop-Loss ab",PE,"        except Exception as exc:  # gemeinsamer Not-Aus nicht schreibbar: wenigstens hier stoppen, Schliessen nicht abbrechen\n","        except ZeroDivisionError as exc:  # eingebauter Fehler\n")
m("N17","Freigabe setzt die Bezugswerte der Notbremsen nicht neu",PE,"        self._day, self._day_start_equity, self._peak_equity = now.date(), equity, equity\n","")

# ---- KI-Kostenkontrolle (D-016): Budget, Kostenobergrenze, Orchestrierung, Auswertung
def z(key: str, name: str, path: str, old: str, new: str) -> None:
    KERN.append((key, name, [(path, old, new)], AIT))


z("Z01","KI-Einzelbudget nicht geprueft",AIB,"            if amount > self._budget.per_call_usd:","            if False:")
z("Z02","KI-Tagesbudget nicht geprueft",AIB,"            if today + amount > self._budget.daily_usd:","            if False:")
z("Z03","KI-Monatsbudget nicht geprueft",AIB,"            if month + amount > self._budget.monthly_usd:","            if False:")
z("Z04","Gesperrtes KI-Budget wird ignoriert",AIB,"            if self._blocked:\n                raise BudgetError","            if False:\n                raise BudgetError")
z("Z05","Gescheiterter KI-Aufruf kostet nichts",AIB,"                actual = reserved  # Aufruf gescheitert: im Zweifel voll berechnen","                actual = ZERO")
z("Z06","Kosten ueber der Reservierung sperren nicht",AIB,"            if actual > reserved:\n                self._blocked","            if False:\n                self._blocked")
z("Z07","Kostenobergrenze ohne Ausgabe-Tokens",AIP,"               + Decimal(max_output_tokens) * self.output_per_mtok) / MILLION",") / MILLION")
z("Z08","Kostenobergrenze mit dem billigeren Eingabepreis",AIP,"worst_input_price = max(self.input_per_mtok, self.cache_write_per_mtok)","worst_input_price = self.input_per_mtok")
z("Z09","KI-Kosten abgerundet",AIP,"               + Decimal(usage.cache_read_tokens) * self.cache_read_per_mtok) / MILLION\n        return cost_up(raw)","               + Decimal(usage.cache_read_tokens) * self.cache_read_per_mtok) / MILLION\n        return raw.quantize(Decimal('0.00000001'), rounding='ROUND_DOWN')")
z("Z10","Wertschranke abgeschaltet",AIO,"        if run.spent + ceiling > request.expected_value_usd * self._routing.max_cost_share:","        if False:")
z("Z11","Teurere Stufe auch bei sicherer Antwort",AIO,"            if answer is not None and confidence >= self._routing.accept_confidence:\n                break","            if False:\n                break")
z("Z12","Sichere Regeln trotzdem per KI geprueft",AIO,"        if valid_rules is not None and valid_rules.confidence >= self._routing.rule_confidence:","        if False:")
z("Z13","Cache / Doppel-Erkennung abgeschaltet",AIO,"            cached = self._cached(key)\n","            cached = None\n")
z("Z14","KI ohne ereignisbasierte Aktivierung",AIO,"            if not self._activation.should_run(request.agent, request.trigger, request.subject or request.kind):","            if False:")
z("Z15","Abkuehlzeit der Aktivierung wirkungslos",AIA,"            return last is None or (self._clock.now() - last).total_seconds() >= rule.cooldown_s","            return True")
z("Z16","Verletzte Token-Annahme sperrt das Budget nicht",AIO,"        if usage.total_input > prompt_bytes + REQUEST_OVERHEAD_TOKENS:","        if False:")
z("Z17","Anbieterfehler ohne Kosten abgerechnet",AIO,"            self._settle(reservation, None)\n            return \"ANBIETER_FEHLER\"","            self._settle(reservation, ZERO)\n            return \"ANBIETER_FEHLER\"")
z("Z18","Unplausible Sicherheit der KI-Antwort akzeptiert",AIO,"    if not confidence.is_finite() or not ZERO <= confidence <= ONE:\n        return None, ZERO\n","")
z("Z19","Uebergrosse Prompts erlaubt",AIO,'    if len((request.instruction + "\\n" + payload).encode("utf-8")) > MAX_PROMPT_BYTES:',"    if False:")
z("Z20","KI-Kosten je Trade ohne Analysen ohne Trade",AUC,"_div(total, len(trades))","_div(on_trades, len(trades))")
z("Z26","Analysen frueherer Zeitraeume nicht dem Trade zugeordnet",AUC,"        net - operating, ai, attribute_ai_costs(records, period_trades, earlier, unmeasured_ai),","        net - operating, ai, attribute_ai_costs(records, period_trades, (), unmeasured_ai),")
z("Z45","Voll umgelegte KI-Kosten ohne Kostenbuch-Anteil",AUC,"    total = on_trades + without + unmeasured_ai_cost\n","    total = on_trades + without\n")
z("Z46","Voll umgelegte KI-Kosten nur aus dem Zeitraum",AUC,"    total = on_trades + without + unmeasured_ai_cost\n","    total = sum((r.cost for r in period), ZERO) + unmeasured_ai_cost\n")
z("Z47","0 Ausgabe-Tokens fuer eine Antwort werden geglaubt",AIO,"        charge: Optional[Decimal] = None if wrong_model or no_usage or no_output else price.cost(usage)\n","        charge: Optional[Decimal] = None if wrong_model or no_usage else price.cost(usage)\n")
z("Z48","Nicht zustaendige Anfragen reservieren Budget",AIO,"        if not self._activation.ready(request.agent, request.trigger, request.subject or request.kind):","        if False:")
z("Z49","Nicht schreibbare Sperre laesst den Prozess weiter zahlen",AIO,"            self._local_block = reason or \"gesperrt\"\n","            pass\n")
z("Z21","Nettoergebnis ohne Betriebskosten",AUC,"        net - operating, ai, attribute_ai_costs(records, period_trades, earlier, unmeasured_ai),","        net, ai, attribute_ai_costs(records, period_trades, earlier, unmeasured_ai),")
z("Z22","Zeitraum-Kosten nicht anteilig",AUC,"        return cost_up(self.amount * Decimal(str(overlap)) / Decimal(str(whole)))","        return self.amount")
z("Z23","Teure Methode schon bei positivem Mittelwert bevorzugt",AUV,"    elif low > 0:","    elif mean_diff > 0:")
z("Z24","Teure Methode schon mit wenigen Daten bevorzugt",AUV,"    if n < min_samples:","    if False:")
z("Z27","Wertschranke nur je Stufe statt je Analyse",AIO,"        if run.spent + ceiling > request.expected_value_usd","        if ceiling > request.expected_value_usd")
z("Z28","Fehlender Verbrauch wird als 0 USD abgerechnet",AIO,"        charge: Optional[Decimal] = None if wrong_model or no_usage or no_output else price.cost(usage)","        charge: Optional[Decimal] = None if wrong_model or no_output else price.cost(usage)")
z("Z29","Anderes Modell des Anbieters bleibt unbemerkt",AIO,"        wrong_model = response.provider != target.provider or response.model != target.model","        wrong_model = False")
z("Z30","Ausgabe ueber max_tokens sperrt nicht",AIO,"        if usage.output_tokens > request.max_output_tokens:","        if False:")
z("Z31","Unsichere KI schlaegt sicherere Regeln",AIO,"        if rules is not None and best[1] <= rules.confidence:","        if False:")
z("Z32","Unsichere KI-Ergebnisse werden gecacht",AIO,"        if self._routing.cache_ttl_s > 0 and best[1] >= self._routing.accept_confidence:","        if self._routing.cache_ttl_s > 0:")
z("Z33","Fehler der Messung (AKI) sperrt das Budget nicht",AIO,"                self._block(\"Messung (AKI) gescheitert: %s\" % type(exc).__name__)","                pass")
z("Z34","Gescheiterte Abrechnung sperrt das Budget nicht",AIO,"            self._block(\"Abrechnung gescheitert: %s\" % type(exc).__name__)","            pass")
z("Z35","Regel-Ersatz wird nicht gemessen",AIO,"            self._note(run, request, Source.RULES, \"REGELN_ERSATZ:%s\" % reason, confidence=rules.confidence)\n","")
z("Z36","Gleichzeitige gleiche Analysen werden doppelt bezahlt",AIO,"                waiting = self._inflight.get(key) if cached is None else None","                waiting = None")
z("Z37","Eskalation scheitert an der eigenen Abkuehlzeit",AIO,"            run.claimed = True\n","")
z("Z38","Cache waechst unbegrenzt",AIO,"        while len(self._cache) >= MAX_CACHE_ENTRIES:","        while False:")
z("Z39","Sperre in der Budget-Reservierung fehlt",AIB,"        with self._lock:\n            if self._blocked:\n                raise BudgetError(\"KI-Budget gesperrt","        if True:\n            if self._blocked:\n                raise BudgetError(\"KI-Budget gesperrt")
z("Z40","Betriebskosten nicht nach UTC umgerechnet",AUC,"        object.__setattr__(self, \"start\", self.start.astimezone(timezone.utc))\n        object.__setattr__(self, \"end\", self.end.astimezone(timezone.utc))\n","")
z("Z41","Cache-Treffer schlaegt sicherere Regeln",AIO,"                if valid_rules is not None and cached.confidence <= valid_rules.confidence:","                if False:")
z("Z42","AKI verliert den Messpunkt bei Protokollfehler","src/trading/agents/aki.py","        self._ai_records.append(record)\n        self._log.append(EventType.AI_CALL, record)\n","        self._log.append(EventType.AI_CALL, record)\n        self._ai_records.append(record)\n")
z("Z43","Betriebskosten-Zeitraum ohne Zeitzone still angenommen",AUC,"        if not (is_aware(start) and is_aware(end)):  # sonst deutete astimezone() sie still als Ortszeit des Rechners\n            raise ValueError(\"Zeitraum ohne Zeitzone.\")\n","")
z("Z44","Abfragezeitraum der Betriebskosten nicht nach UTC",AUC,"        start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)\n","")
KERN.append(("Z25","Geheimnis-Scan nimmt jeden Namen mit 'tokens' aus",[("scripts/secret_scan.py","    return bool(TOKEN_COUNT_NAME.match(name))","    return 'tokens' in name.lower()")],["tests/test_secret_scan.py"]))


BACKTEST: List[Mutant] = [

 ("B01", "Ausfuehrung zum SCHLUSSKURS der gerade beginnenden Kerze (Blick in die Zukunft)", [(EN, "return context(instrument, opening[1].open) if opening", "return context(instrument, opening[1].close) if opening")], BT),
 ("B02", "Strategie sieht die gerade beginnende Kerze", [(EN, "            for event in tick.fundings:  # 2. Funding", "            for _s, _c in tick.opens.values():\n                history[_c.instrument].append(_c)\n            for event in tick.fundings:  # 2. Funding")], BT),
 ("B03", "Ausfuehrung ohne Folgekerze: ersatzweise zum letzten Schlusskurs", [(EN, "return context(instrument, opening[1].open) if opening is not None else None", "return context(instrument, opening[1].open) if opening is not None else context(instrument, history[instrument][-1].close)")], BT),
 ("B04", "Funding wird nicht abgerechnet", [(EN, "                executor.apply_funding(event)\n", "                pass\n")], BT),
 ("B05", "Bekannter Funding-Satz wird nicht fortgeschrieben", [(EN, "                funding_rate[event.instrument] = event.rate\n", "")], BT),
 ("B06", "Einstiege vor Ausstiegen", [(EN, "            for signal in signals:  # 5. Ausfuehrung: erst Ausstiege, dann Einstiege", "            for setup in setups:\n                result = pipeline.process_setup(setup, fill(tick, setup.instrument))\n                if result.executed:\n                    executed += 1\n                for reason in result.reasons:\n                    blocked[reason.value] = blocked.get(reason.value, 0) + 1\n            setups = []\n            for signal in signals:")], BT),
 ("B07", "Abgestuerzte Strategie liefert trotzdem ein Ergebnis", [(EN, "        if log.of_type(EventType.SYSTEM_ERROR):", "        if False:")], BT),
 ("B08", "Verworfene Marktdaten werden hingenommen", [(EN, "        if log.of_type(EventType.DATA_REJECTED):", "        if False:")], BT_PG),
 ("B09", "Bezeichnung des Laufs veraendert den Fingerabdruck", [(EN, 'if k != "label"', 'if k != "kein-feld"')], BT),
 ("B10", "Kapitalpunkt VOR dem Funding desselben Zeitpunkts", [(EN, "            for event in tick.fundings:  # 2. Funding\n                executor.apply_funding(event)\n                funding_rate[event.instrument] = event.rate\n            if not tick.closes:\n                continue\n            aki.record_equity(now, executor.portfolio_state())  # 3. Kapitalkurve\n", "            if tick.closes:\n                aki.record_equity(now, executor.portfolio_state())\n            for event in tick.fundings:  # 2. Funding\n                executor.apply_funding(event)\n                funding_rate[event.instrument] = event.rate\n            if not tick.closes:\n                continue\n")], BT),
 ("B11", "Verlaufsfenster um eine Kerze zu gross", [(EN, "bars={i: tuple(bars[-cfg.history_bars:])", "bars={i: tuple(bars[-cfg.history_bars - 1:])")], BT),
 ("B12", "Per Signal geschlossene Trades fehlen in der Auswertung", [(EN, "                    exits_executed += 1\n                    aki.register_closed_trades([outcome.result.trade])", "                    exits_executed += 1")], BT),
 ("B13", "Per Stop/Ziel geschlossene Trades fehlen in der Auswertung", [(EN, "aki.register_closed_trades(executor.on_candle(candle))", "executor.on_candle(candle)")], BT),
 ("B14", "Spread des Datensatzes wird ignoriert", [(EN, "instrument, clock.now(), price, spread[instrument], funding_rate[instrument]", "instrument, clock.now(), price, Decimal(0), funding_rate[instrument]")], BT),
 ("B15", "Ergebnis-Fingerabdruck ohne Kapitalkurve und Trades", [(EN, '"trades": trades, "curve": curve, "open": open_positions,', '"trades": len(trades), "curve": len(curve), "open": open_positions,')], BT),
 ("B16", "Defektes Protokoll wird hingenommen", [(EN, "        if not check.ok:", "        if False:")], BT),
 ("ST01", "Donchian-Kanal enthaelt die aktuelle Kerze", [(SI, "    window = candles[-(n + 1):-1]", "    window = candles[-n:]")], STT),
 ("ST02", "Short-Vorschlaege auch auf Spot", [(SL, "        return self.allow_short and instrument.market_type is MarketType.FUTURES", "        return self.allow_short")], STT),
 ("ST03", "Stop und Ziel zu Gunsten gerundet", [(SL, "        rounding = ROUND_FLOOR if signal.side is Side.LONG else ROUND_CEILING", "        rounding = ROUND_CEILING if signal.side is Side.LONG else ROUND_FLOOR")], STT),
 ("ST04", "Zweiter Einstieg trotz offener Position", [(SL, "                    exits.append(ExitProposal(instrument, reason))\n                continue\n", "                    exits.append(ExitProposal(instrument, reason))\n")], STT),
 ("ST05", "Trendfolge ohne frisches Kreuzen", [(SL, "        if now_up is None or was_up is None or slow is None or now_up == was_up:", "        if now_up is None or was_up is None or slow is None:")], STT),
 ("ST06", "Wahre Spanne ohne Kursluecken", [(SI, "            tr = max(tr, abs(c.high - prev_close), abs(c.low - prev_close))", "            tr = tr")], STT),
 ("ST07", "Kaufen-und-Halten ohne Funding", [(SB, "                h.funding += h.quantity * value", "                h.funding += 0")], STT),
 ("ST08", "Kaufen-und-Halten ohne Ausstiegsgebuehr", [(SB, "        fee = proceeds * costs.taker_fee\n", "        fee = ZERO\n")], STT),
 ("ST09", "Scalping ohne Grenze der Haltedauer", [(SL, "        if held >= self.max_hold:", "        if False:")], STT),
 ("KY01", "KIBO laesst Stop im Rauschen zu", [(KB, "        if abs(setup.entry - setup.stop_loss) < cfg.min_stop_atr * atr:", "        if False:")], KYT),
 ("KY02", "KIBO laesst Setups gegen den klaren Trend zu", [(KB, "        if against and cfg.block_counter_trend:", "        if False:")], KYT),
 ("KY03", "KI kann harte KIBO-Ablehnung aufheben", [(KB, "        if hard:\n            return AgentOpinion(AgentName.KIBO", "        if False:\n            return AgentOpinion(AgentName.KIBO")], KYT),
 ("KY04", "KI-Antwort ohne klare Entscheidung zaehlt", [(AR, "and isinstance(answer.get(\"approve\"), bool):", "and answer.get(\"confidence\") is not None:")], KYT),
 ("KY05", "YUZU laesst extreme Volatilitaet zu", [(YZ, "        if regime.atr_pct > cfg.max_atr_pct:", "        if False:")], KYT),
 ("KY06", "YUZU ignoriert Funding gegen die Position", [(YZ, "            if paid > cfg.max_funding:", "            if False:")], KYT),
 ("KY07", "YUZU ignoriert den Spread im Verhaeltnis zum Stop", [(YZ, "        if stop_distance > 0 and spread > cfg.max_spread_stop_share * stop_distance:", "        if False:")], KYT),
 ("KY08", "Trend ohne Effizienz-Schwelle", [(RG, "    if efficiency >= config.trend_efficiency and slope > 0 and price > now:", "    if slope > 0 and price > now:")], KYT),
 ("KY09", "Gleichstaende im Volatilitaets-Rang nicht beruecksichtigt", [(RG, " + D(sum(1 for v in earlier if v == current)) / 2)", ")")], KYT),
 ("KY10", "Backtest nutzt wieder neutrale Platzhalter", [("src/trading/backtest/engine.py", "        kibo = self._kibo or RuleBasedKibo()", "        kibo = self._kibo or NeutralKibo()")], KYT),
 ("DA01", "Dashboard laesst Modus LIVE zu", [(DSH_API, "    if source.mode is Mode.LIVE:", "    if False:")], DBT),
 ("DA02", "Dashboard liefert beliebige Dateien aus", [(DSH_API, "        if name not in STATIC_FILES:  # feste Liste - kein Zugriff auf andere Dateien", "        if not (STATIC / name).is_file():")], DBT),
 ("DA03", "Dashboard ohne Sicherheits-Header", [(DSH_API, "            response.headers[key] = value", "            pass")], DBT),
 ("DA04", "Ausgeduennte Kapitalkurve verliert den tiefsten Punkt", [(DSH_STATE, "    keep = {round(i * step) for i in range(limit - 1)} | {0, len(points) - 1, lowest}", "    keep = {round(i * step) for i in range(limit - 1)} | {0, len(points) - 1}")], DBT),
 ("DA05", "Drawdown nur aus den angezeigten Punkten", [(DSH_STATE, "    return [{\"time\": p.time.isoformat(), \"equity\": str(p.equity), \"drawdown_pct\": str(drawdown[p.time])}\n            for p in thin_curve(points)]", "    return [{\"time\": p.time.isoformat(), \"equity\": str(p.equity), \"drawdown_pct\": \"0\"}\n            for p in thin_curve(points)]")], DBT),
 ("DA06", "WebSocket sendet unveraenderte Zustaende erneut", [(DSH_API, "                if body[\"version\"] != last:", "                if True:")], DBT),
 ("RT01", "Engine startet ohne Freigabe des Eigners", [(RTE, "    if env.get(APPROVAL_ENV) != APPROVAL_TEXT:", "    if False:")], RTT),
 ("RT02", "Engine standardmaessig eingeschaltet", [(RTE, "    enabled: bool = False  # Standard: AUS.", "    enabled: bool = True  # Standard: AUS.")], RTT),
 ("RT03", "Wiederholte Engine-Fehler ohne Not-Aus", [(RTE, "        if self._consecutive_errors >= self._config.max_consecutive_errors:", "        if False:")], RTT),
 ("RT04", "Engine handelt auf verworfenen Kerzen", [(RTE, "        if not accepted:\n            self.stats.rejected_candles += 1", "        if False:\n            self.stats.rejected_candles += 1")], RTT),
 ("RT05", "Signal-Ausstiege fehlen in AKI", [(RTE, "        new = trades[self._registered:]", "        new = trades[len(trades):]")], RTT),
 ("RT06", "Setup-Kennung ohne Entscheidungszeit", [(RTE, "p.side.value, now.strftime(\"%Y%m%dT%H%M%SZ\"))", "p.side.value, \"x\")")], RTT),
 ("RT07", "Futures mit erfundenem Funding-Satz", [(RTE, "        rate = self._funding.get(candle.instrument)\n", "        rate = self._funding.get(candle.instrument, ZERO)\n")], RTT),
 ("RT08", "Aufwaermen nach Neustart handelt", [(RTE, "                self._remember(c)\n                taken += 1", "                self.process(c)\n                taken += 1")], RTT),
 ("RS01", "Testdaten mehrfach ansehbar", [(RSR, "            if entry[\"test_opened\"]:\n                raise RegistryError", "            if False:\n                raise RegistryError")], RST),
 ("RS02", "Test auch ohne bestandene Validierung", [(RSR, "            if entry[\"status\"] != Status.VALIDATED.value:\n                raise RegistryError(\"Test nur", "            if False:\n                raise RegistryError(\"Test nur")], RST),
 ("RS03", "Verworfene Versuche zaehlen nicht", [(RSR, "            family[\"trials\"] += 1\n", "            family[\"trials\"] += 1 if passed else 0\n")], RST),
 ("RS04", "Schwelle unabhaengig von der Zahl der Versuche", [(RSE, "    required = se * (z + penalty)", "    required = se * z")], RST),
 ("RS05", "Kein Kosten-Stresstest", [(RSE, "        if stressed.net_result <= 0:\n            reasons.append(\"bei", "        if False:\n            reasons.append(\"bei")], RST),
 ("RS06", "Walk-forward-Tests ueberlappen erlaubt", [(RSP, "step < test or", "step < ZERO_TIME or")], RST),
 ("RS07", "Freigabe ohne bestandenen Test", [(RSR, "            if entry[\"status\"] != Status.TESTED.value or not (entry[\"test\"] or {}).get(\"passed\"):", "            if entry[\"status\"] != Status.TESTED.value:")], RST),
 ("RS08", "Zeitfenster mit Funding aus dem Zeitraum selbst", [("src/trading/backtest/data.py", "            known = [f.rate for f in s.funding if f.time <= candles[0].open_time]", "            known = [f.rate for f in s.funding if f.time <= candles[-1].close_time]")], RST),
 ("RS09", "Marktphase beim Einstieg mit Zukunftsdaten", [(RSV, "    candles = [c for c in series.candles if c.close_time <= time][-history:]", "    candles = list(series.candles)[-history:]")], RST),
 ("BR01", "Mengenschritt der Boerse nicht geprueft", [(OR, "        if not rules.on_quantity_step(quantity):", "        if False:")], BRT),
 ("BR02", "Mindestwert der Boerse nicht geprueft", [(OR, "        if quantity * fill < rules.min_notional:", "        if False:")], BRT),
 ("BR03", "Preisschritt der Boerse nicht geprueft", [(OR, "        if not (rules.on_price_tick(stop) and rules.on_price_tick(tp)):", "        if False:")], BRT),
 ("BR04", "Fehlende Boersenregeln werden geraten", [("src/trading/safety/risk_manager.py", "            if rules is None:  # Regelwerk vorhanden", "            if False:  # Regelwerk vorhanden")], BRT),
 ("BR05", "Hoechsthebel der Boerse ignoriert", [(OR, "(rules is not None and leverage > rules.max_leverage):", "(False):")], BRT),
 ("BR06", "SORA rundet nicht auf den Mengenschritt der Boerse", [(SO, "        step = rules.quantity_step if rules is not None else cfg.quantity_step", "        step = cfg.quantity_step")], BRT),
 ("BR07", "Stop zu Gunsten auf den Preisschritt gerundet", [(BXR, "aufrunden).\"\"\"\n        rounding = ROUND_FLOOR if side is Side.LONG else ROUND_CEILING\n        return (stop", "aufrunden).\"\"\"\n        rounding = ROUND_CEILING if side is Side.LONG else ROUND_FLOOR\n        return (stop")], BRT),
 ("BR08", "Falsche Gebuehrenstufe", [("src/trading/execution/fees.py", "        return [t for t in self.tiers if t.min_volume_30d <= volume_30d][-1]", "        return [t for t in self.tiers if t.min_volume_30d <= volume_30d][0]")], BRT),
 ("BR09", "SORA ignoriert den Hoechsthebel der Boerse", [(SO, "                      if rules is None or lv <= rules.max_leverage]", "                      if True]")], BRT),
 ("BV01", "Vergleich trotz verschiedener Datensaetze", [(CM, "if r.dataset_fingerprint != first.dataset_fingerprint:", "if False:")], BT),
 ("BV02", "Vergleich trotz verschiedener Kosten/Kapital", [(CM, "if r.assumptions_fingerprint != first.assumptions_fingerprint:", "if False:")], BT),
 ("BV03", "Vergleichstabelle unsortiert", [(CM, "return sorted(rows, key=lambda row: (-row[\"return_pct\"], row[\"strategy\"], row[\"label\"]))", "return rows")], BT),
 ("BD01", "Kerzenfehler (Luecken, Duplikate) nur noch Warnung", [(DA, "(errors if issue.is_error else warnings).append(", "(warnings).append(")], BT),
 ("BD02", "Futures ohne Funding-Daten erlaubt", [(DA, "    if not series.funding:\n        error(", "    if False:\n        error(")], BT),
 ("BD03", "Funding nach dem Ende der Kerzen erlaubt", [(DA, "if last_close is not None and is_aware(last_close) and ev.time > last_close:", "if False:")], BT),
 ("BD04", "Doppeltes/unsortiertes Funding erlaubt", [(DA, "if previous is not None and ev.time <= previous:", "if False:")], BT),
 ("BD05", "Fremde Kerze in der Reihe erlaubt", [(DA, "if isinstance(c, Candle) and (c.instrument != series.instrument or c.interval_s != series.interval_s):", "if False:")], BT),
 ("BD06", "Instrument doppelt im Datensatz erlaubt", [(DA, "for i, n in seen.items() if n > 1]", "for i, n in seen.items() if n > 99]")], BT),
 ("BD07", "Funding bei Spot erlaubt", [(DA, "        if series.funding or series.initial_funding_rate is not None:", "        if False:")], BT),
 ("BD08", "Fingerabdruck ignoriert Reihenfolge-Sortierung", [(DA, "ordered = tuple(sorted(series, key=lambda s: s.instrument.key))", "ordered = tuple(series)")], BT),
 ("BD09", "Funding eines fremden Instruments erlaubt", [(DA, "if not isinstance(ev, FundingEvent) or ev.instrument != series.instrument:", "if not isinstance(ev, FundingEvent):")], BT),
]

LOCK: Edit = (EX, '"SELECT * FROM accounts WHERE account_id = %s FOR UPDATE"',
              '"SELECT * FROM accounts WHERE account_id = %s"')
RISK: Edit = (S2, "    IF acc.equity <= 0 OR remaining > acc.max_portfolio_risk * acc.equity THEN", "    IF false THEN")
EXPO: Edit = (S2, "    IF exposure > acc.max_gross_exposure * acc.equity THEN", "    IF false THEN")

DATENBANK: List[Mutant] = [

 ("G01","Konto-Sperre im Programm entfernt",[LOCK], PG),
 ("G02","DB-Gurt Gesamtrisiko entfernt",[RISK], PG),
 ("G03","DB-Gurt Exposure entfernt",[EXPO], PG),
 ("G04","Sperre UND beide DB-Gurte entfernt",[LOCK,RISK,EXPO], PG),
 ("G05","DB prueft den Hash neuer Protokolleintraege nicht",[(S1,"IF NEW.hash <> expected THEN","IF false THEN")], PG),
 ("G06","Order-IDs nach Neustart vergessen",[(EX,"known = self._known_order(cur, order_id) if order_id is not None else None","known = None")], PG),
 ("G07","Hoechststand wird nicht gespeichert",[(EX,"new_vals = (after.balance, engine.equity(), after.peak_equity, after.day,",'new_vals = (after.balance, engine.equity(), account["peak_equity"], after.day,')], PG),
 ("G08","Kein Rollback (autocommit statt Transaktion)",[(EX,"            with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:\n                yield cur","            conn.autocommit = True\n            with conn.cursor(row_factory=dict_row) as cur:\n                yield cur")], PG),
 ("G09","Offene Positionen werden nach Neustart nicht geladen",[(EX,"AND status = 'OPEN' ORDER BY position_id","AND status = 'NEVER' ORDER BY position_id")], PG),
 ("G10","Protokoll ohne Reihenfolge-Sperre",[(PL,'c.execute("SELECT pg_advisory_xact_lock(%s)", (CHAIN_LOCK_KEY,))',"pass")], PG),
 ("G11","Marktzustand (Kurs, letzte Kerze) wird nicht gespeichert",[(EX,"            if new_row != old_row:","            if False:")], PG),
 ("G12","Tagesstart-Kapital wird nicht gespeichert",[(EX,"after.day_start_equity, after.position_counter)",'account["day_start_equity"], after.position_counter)')], PG),
 ("G13","Funding-Fortschritt wird nicht gespeichert",[(EX,"            elif p != before[pid]:\n                self._update_position(cur, p)","            elif False:\n                self._update_position(cur, p)")], PG),
 ("G14","App-Rolle darf alle Spalten offener Positionen aendern",[(S2,"REVOKE UPDATE ON positions FROM trading_app;","-- REVOKE entfernt")], PG),
 ("G15","Exposure-Limit des Kontos nachtraeglich aenderbar",[(S2,"       OR NEW.max_gross_exposure <> OLD.max_gross_exposure\n","")], PG),
 ("G16","DB-Gurt liest Konto nur (REPEATABLE-READ-Umweg offen)",[(S2,"UPDATE accounts SET version = version WHERE account_id = NEW.account_id RETURNING * INTO acc;","SELECT * INTO acc FROM accounts WHERE account_id = NEW.account_id;")], PG),
 ("G17","Veraenderte Migration wird nicht bemerkt",[(MG,"if m.version in done and done[m.version] != m.checksum:","if False:")], PG),
 ("G18","Protokoll nicht in derselben Transaktion (gar nicht geschrieben)",[(EX,"            append_raw(cur, ev.ts, ev.event_type, ev.payload_json)","            pass")], PG),
 ("G19","Abgeschlossene Trades werden nicht gespeichert",[(EX,"            self._insert_closed_trade(cur, t)","            pass")], PG),
 ("G20","Mehrere offene Positionen je Instrument moeglich",[(S2,"CREATE UNIQUE INDEX positions_one_open_per_instrument","CREATE INDEX positions_one_open_per_instrument")], PG),
 ("G21","Handelstag wird nicht gespeichert",[(EX,"after.peak_equity, after.day,",'after.peak_equity, account["day"],')], PG),
 ("G22","Protokolleintraege loeschbar",[(S1,"CREATE TRIGGER event_log_no_update_delete BEFORE UPDATE OR DELETE ON event_log","CREATE TRIGGER event_log_no_update_delete BEFORE UPDATE OF seq ON event_log")], PG),
 ("G23","Pruefung ignoriert den Kettenende-Zeiger",[(PL,"if head is None or (int(head[0]), str(head[1])) != expected:","if False:")], PG),
 ("G24","DB prueft die Sequenznummer nicht",[(S1,"IF NEW.seq <> head.seq + 1 THEN","IF false THEN")], PG),
 ("G25","DB prueft die Verkettung nicht",[(S1,"IF NEW.prev_hash <> head.hash THEN","IF false THEN")], PG),
 ("G26","Geschlossene Positionen aenderbar",[(S1,"IF OLD.status = 'CLOSED' THEN","IF false THEN")], PG),
 ("G27","Bestehende Datenbank wird ohne Rueckfrage als migriert markiert",[(MG,"                if not baseline_existing:","                if False:")], PG),
 ("G28","Order-Vermerk wird nicht gespeichert",[(EX,"        if order is not None:\n            intent, result = order","        if False:\n            intent, result = order")], PG),
 ("G29","Geschlossene Position bleibt in der DB offen",[(EX,"                    \"UPDATE positions SET status = 'CLOSED', closed_at = %s\"","                    \"UPDATE positions SET status = status, closed_at = %s\"")], PG),
 ("G30","Guthaben wird nicht gespeichert",[(EX,"new_vals = (after.balance, engine.equity(),",'new_vals = (account["balance"], engine.equity(),')], PG),
 ("G31","Anwendung darf den Not-Aus aufheben",[(S3,"        IF current_user = 'trading_app' THEN\n            RAISE EXCEPTION 'Not-Aus darf nur","        IF false THEN\n            RAISE EXCEPTION 'Not-Aus darf nur")], PG),
 ("G32","DB-Gurt: neue Position trotz Not-Aus",[(S3,"    IF COALESCE((SELECT halted FROM trading_halt WHERE id = 1), true) THEN","    IF false THEN")], PG),
 ("G33","Erster Grund des Not-Aus ueberschreibbar",[(S3,"IF NEW.reason IS DISTINCT FROM OLD.reason OR NEW.since IS DISTINCT FROM OLD.since\n           OR NEW.note IS DISTINCT FROM OLD.note THEN","IF false THEN")], PG),
 ("G34","Vom Programm ausgeloester Not-Aus wird nicht gespeichert",[(EX,"            write_trip(cur, halt_after)\n","            pass\n")], PG),
 ("G35","Protokoll-Schreibfehler setzt keinen Not-Aus",[(EX,"                    trip_in_new_transaction(self._db.get(), self._clock, HaltReason.PROTOCOL_ERROR,\n                                            \"Protokoll nicht schreibbar","                    (lambda *a: None)(self._db.get(), self._clock, HaltReason.PROTOCOL_ERROR,\n                                            \"Protokoll nicht schreibbar")], PG),
 ("G36","Executor liest den Not-Aus nicht aus der Datenbank",[(EX,"halt = read_halt(cur, lock=True)","halt = HaltStatus(False)")], PG),
 ("G37","Fehlende Not-Aus-Zeile gilt als frei",[(PK,'return HaltStatus(True, None, None, "Not-Aus-Zeile fehlt")',"return RUNNING")], PG),
 ("G38","Beschaedigtes Protokoll bei der Pruefung setzt keinen Not-Aus",[(EX,"        if not ok:\n            with self._conn_lock:","        if False:\n            with self._conn_lock:")], PG),
 ("G39","Not-Aus wird ohne Sicherungspunkt protokolliert (kaputtes Protokoll verhindert ihn)",[(PK,"                with conn.transaction():\n                    log_event(","                if True:\n                    log_event(")], PG),
 ("G40","Not-Aus-Zeile ohne Sperre gelesen (Wettlauf mit Ausloesern)",[(EX,"halt = read_halt(cur, lock=True)","halt = read_halt(cur)")], PG),
 ("G41","Not-Aus-Zeile loesch-/neu anlegbar (Freigabe ohne Namen)",[(S3,"CREATE TRIGGER trading_halt_no_insert_delete BEFORE INSERT OR DELETE ON trading_halt","CREATE TRIGGER trading_halt_no_insert_delete BEFORE UPDATE OF id ON trading_halt")], PG),
 ("G42","Not-Aus-Tabelle leerbar (TRUNCATE)",[(S3,"CREATE TRIGGER trading_halt_no_truncate BEFORE TRUNCATE ON trading_halt","CREATE TRIGGER trading_halt_no_truncate AFTER INSERT ON trading_halt")], PG),
 ("G43","Freigabe erbt Name und Zeitpunkt einer frueheren Freigabe",[(S3,"           OR NEW.released_at IS NOT DISTINCT FROM OLD.released_at OR NEW.released_at < OLD.since THEN","           THEN")], PG),
 ("G44","Haengende Transaktionen ohne Zeitlimit",[(DB,'            " -c idle_in_transaction_session_timeout=15000"\n',"")], PG),
 ("G45","Freigabe setzt die Notbremsen der Konten nicht neu",[(PK,'"UPDATE accounts SET day = %s, day_start_equity = equity, peak_equity = equity,"\n                " version = version + 1, updated_at = now()",','"SELECT %s::date",')], PG),
 ("G46","Freigabe ohne Not-Aus-Zeile meldet Erfolg",[(PK,"            if cur.rowcount != 1:  # Zeile fehlt","            if False:  # Zeile fehlt")], PG),
 ("G47","Ausloesen von aussen ohne Wiederholung bei belegter Sperre",[(PK,"                retry = isinstance(exc, psycopg.errors.LockNotAvailable) or self._db.is_lost()","                retry = self._db.is_lost()")], PG),
 ("G48","Wiederholung aus der DB liefert die Position mit spaeterem Funding",[(EX,"            position = dataclasses.replace(opened, last_funding_ts=opened.opened_at, funding_paid=Decimal(0))","            position = opened")], PG),
 ("G49","Fingerabdruck wird nicht gespeichert",[(EX,"self._clock.now(), order_fingerprint(intent),","self._clock.now(), None,")], PG),
 ("G50","DB-Gurt: Fill ohne Position erlaubt",[(S4,"        fingerprint IS NULL OR ((status = 'filled') = (position_id IS NOT NULL))),","        true),")], PG),
 ("G51","DB: Fingerabdruck in beliebigem Format",[(S4,"ADD COLUMN fingerprint TEXT CHECK (fingerprint ~ '^[0-9a-f]{64}$'),","ADD COLUMN fingerprint TEXT,")], PG),
 ("G52","DB: Order verweist auf nicht vorhandene Position",[(S4,",\n    ADD CONSTRAINT orders_position_fk FOREIGN KEY (account_id, position_id) REFERENCES positions (account_id, position_id);",";")], PG),
 ("G53","Groessen-Order: Order-ID nicht geprueft und nicht gespeichert",[(EX,"                         client_order_id, lambda: built[-1] if built else None)","                         None, lambda: built[-1] if built else None)")], PG),
 ("G54","DB: Anwendung darf wieder Konten anlegen",[(S5,"REVOKE INSERT ON accounts FROM trading_app;","GRANT INSERT ON accounts TO trading_app;")], PG),
 ("G55","DB: Kontoaenderung ohne genau einen Versionsschritt",[(S5,"    IF NEW.version <> OLD.version + 1 THEN","    IF NEW.version < OLD.version THEN")], PG),
 ("G56","DB: Positionszaehler darf sinken",[(S5,"    IF NEW.position_counter < OLD.position_counter THEN","    IF false THEN")], PG),
 ("G57","DB: Handelstag darf zurueckspringen",[(S5,"    IF NEW.day < OLD.day THEN","    IF false THEN")], PG),
 ("G58","DB: Tagesstart-Equity innerhalb des Tages aenderbar",[(S5,"    IF NEW.day = OLD.day AND NEW.day_start_equity <> OLD.day_start_equity THEN","    IF false THEN")], PG),
 ("G59","DB: Hoechststand darf sinken",[(S5,"    IF NEW.peak_equity < OLD.peak_equity OR NEW.peak_equity < NEW.equity THEN","    IF NEW.peak_equity < NEW.equity THEN")], PG),
 ("G60","DB: Hoechststand darf unter der Equity liegen",[(S5,"    IF NEW.peak_equity < OLD.peak_equity OR NEW.peak_equity < NEW.equity THEN","    IF NEW.peak_equity < OLD.peak_equity THEN")], PG),
 ("G61","DB: Plausibilitaetspruefung gilt nur fuer die Rolle trading_app",[(S5,"    IF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN","    IF current_user <> 'trading_app' THEN")], PG),
 ("G62","Executor legt fehlendes Konto nicht an, startet aber trotzdem",[(EX,"        if not self._read(exists):\n            raise AccountMissingError(","        if False:\n            raise AccountMissingError(")], PG),
 ("G63","Kontoanlage ohne Protokolleintrag",[(AC,"            if created:  # in derselben Transaktion: ohne Protokolleintrag kein Konto","            if False:")], PG),
 ("G64","Kontoanlage prueft das Startkapital nicht",[(AC,"    if not is_positive_decimal(initial_equity):","    if initial_equity is None:")], PG),
 ("G65","Kontoanlage prueft den Kontonamen nicht",[(AC,"    if not isinstance(account_id, str) or not _ACCOUNT_ID.match(account_id):","    if not isinstance(account_id, str):")], PG),
    # AP-11 Wiederverbindung (Audit H7)
 ("G66","Abgerissene Verbindung wird nie neu aufgebaut",[(DB,"        if self.is_lost():\n            self._reconnect()","        if self._conn is None:\n            self._reconnect()")], PG + RC),
 ("G67","Order wird nach Abbruch beim COMMIT still wiederholt",[(EX,"                if attempt == 0 and not decided and self._db.is_lost():","                if attempt == 0 and self._db.is_lost():")], RC),
 ("G68","Wiederverbindung ohne Begrenzung der Versuche",[(DB,"        for attempt in range(len(self._delays) + 1):","        for attempt in iter(int, 1):")], RC),
 ("G69","Wiederverbindung ohne Wartezeit",[(DB,"                self._sleep(self._delays[attempt - 1])","                pass")], RC),
 ("G70","Geschlossene Verbindung verbindet sich selbst neu",[(DB,"        if self._closed:\n            raise","        if False:\n            raise")], PG + RC),
 ("G71","Fehler ohne Verbindungsverlust (z. B. Sperr-Zeitlimit) wird wiederholt",[(EX,"                if attempt == 0 and not decided and self._db.is_lost():","                if attempt == 0 and not decided:")], RC),
 ("G72","Lesen ohne Wiederverbindung",[(EX,"                if attempt == 0 and self._db.is_lost():\n                    continue\n                raise","                raise")], RC),
 ("G73","Not-Aus von aussen scheitert an abgerissener Verbindung",[(PK,"                retry = isinstance(exc, psycopg.errors.LockNotAvailable) or self._db.is_lost()","                retry = isinstance(exc, psycopg.errors.LockNotAvailable)")], RC),
 # ---- KI-Budget in der Datenbank (D-016, Migration 006)
 ("G74","DB-Gurt KI-Budget entfernt",[(S6,"    IF NEW.reserved > lim.per_call_usd OR today + NEW.reserved > lim.daily_usd\n       OR month + NEW.reserved > lim.monthly_usd THEN","    IF false THEN")], PGB),
 ("G75","Gesperrtes KI-Budget in der DB ignoriert",[(S6,"    IF NOT FOUND OR lim.blocked THEN","    IF NOT FOUND THEN")], PGB),
 ("G76","Anwendung darf KI-Budget entsperren",[(S6,"           OR (OLD.blocked AND NOT NEW.blocked) THEN","           OR false THEN")], PGB),
 ("G77","KI-Budgeteintraege mehrfach abrechenbar",[(S6,"    IF OLD.actual IS NOT NULL OR NEW.actual IS NULL\n","    IF false AND (OLD.actual IS NOT NULL OR NEW.actual IS NULL)\n")], PGB),
 ("G78","KI-Budgetverlauf loeschbar",[(S6,"CREATE TRIGGER ai_budget_entries_no_delete BEFORE DELETE ON ai_budget_entries","CREATE TRIGGER ai_budget_entries_no_delete BEFORE UPDATE OF day ON ai_budget_entries")], PGB),
 ("G79","Budget der Anwendung bei DB-Budget ignoriert",[(PAB,"                if today + amount > self._budget.daily_usd or month + amount > self._budget.monthly_usd:","                if False:")], PGB),
 ("G80","DB-Budget sperrt nicht bei Kosten ueber Reservierung",[(PAB,"            if cost > reservation.amount:","            if False:")], PGB),
 ("G81","KI-Grenzen ohne Namen der Freigabe",[(S6,"           AND (NEW.approved_by IS NULL OR btrim(NEW.approved_by) = '' OR NEW.approved_at IS NULL","           AND (NEW.approved_at IS NULL")], PGB),
 ("G82","KI-Budget bucht auf den Tag der Anwendung",[(S6,"    NEW.created_at := now();                           -- Zeit und Tag von der Datenbank, nicht von der Anwendung\n    NEW.day := (now() AT TIME ZONE 'UTC')::date;\n","")], PGB),
 ("G83","Neue KI-Grenzen mit alter Freigabe",[(S6,"                OR NEW.approved_at IS NOT DISTINCT FROM OLD.approved_at) THEN","                ) THEN")], PGB),
 ("G84","KI-Budget ohne Namen und Zeitpunkt entsperrbar",[(S6,"        IF OLD.blocked AND NOT NEW.blocked\n           AND (NEW.unblocked_by IS NULL","        IF false AND OLD.blocked AND NOT NEW.blocked\n           AND (NEW.unblocked_by IS NULL")], PGB),
 ("G85","Neue KI-Grenzen mit geloeschtem Freigabe-Zeitpunkt",[(S6,"           AND (NEW.approved_by IS NULL OR btrim(NEW.approved_by) = '' OR NEW.approved_at IS NULL\n","           AND (NEW.approved_by IS NULL OR btrim(NEW.approved_by) = ''\n")], PGB),
 ("G86","Entsperren mit geloeschtem Zeitpunkt",[(S6,"           AND (NEW.unblocked_by IS NULL OR btrim(NEW.unblocked_by) = '' OR NEW.unblocked_at IS NULL\n","           AND (NEW.unblocked_by IS NULL OR btrim(NEW.unblocked_by) = ''\n")], PGB),
 ("G87","Abrechnungszeit von der Anwendung",[(S6,"    NEW.settled_at := now();\n","")], PGB),
 ("G88","KI-Reservierungen nur lesend gesperrt (REPEATABLE READ)",[(S6,"    UPDATE ai_budget_limits SET reservations = reservations + 1 WHERE id = 1 RETURNING * INTO lim;\n","    SELECT * INTO lim FROM ai_budget_limits WHERE id = 1 FOR UPDATE;\n")], PGB),
 ("G89","Anwendung darf Freigabe-Felder des KI-Budgets aendern",[(S6,"           OR NEW.monthly_usd IS DISTINCT FROM OLD.monthly_usd OR NEW.approved_by IS DISTINCT FROM OLD.approved_by\n           OR NEW.approved_at IS DISTINCT FROM OLD.approved_at OR NEW.unblocked_by IS DISTINCT FROM OLD.unblocked_by\n           OR NEW.unblocked_at IS DISTINCT FROM OLD.unblocked_at\n","           OR NEW.monthly_usd IS DISTINCT FROM OLD.monthly_usd\n")], PGB),
 ("G90","Anwendung darf KI-Grenzen aendern (Trigger)",[(S6,"        IF NEW.per_call_usd IS DISTINCT FROM OLD.per_call_usd OR NEW.daily_usd IS DISTINCT FROM OLD.daily_usd\n           OR NEW.monthly_usd IS DISTINCT FROM OLD.monthly_usd OR NEW.approved_by","        IF NEW.approved_by")], PGB),
 ("G91","KI-Budget: eine Verbindung ohne Sperre fuer alle Threads",[(PAB,"        with self._lock:\n            conn = self._db.get()","        if True:\n            conn = self._db.get()")], PGB),
 ("G92","KI-Budget arbeitet in fremder offener Transaktion",[(PAB,"            if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:","            if False:")], PGB),
 ("G93","KI-Reservierung schon abgerechnet angelegt",[(S6,"    IF NEW.actual IS NOT NULL THEN\n        RAISE EXCEPTION 'KI-Budget: neue Reservierung ist noch nicht abgerechnet'","    IF false THEN\n        RAISE EXCEPTION 'KI-Budget: neue Reservierung ist noch nicht abgerechnet'")], PGB),
]


GROUPS = {"kern": KERN, "backtest": BACKTEST, "datenbank": DATENBANK}
HAVE_DB = bool(os.environ.get("TEST_ADMIN_URL") and os.environ.get("TEST_APP_PASSWORD"))


def needs_db(tests: Sequence[str]) -> bool:
    return any("_pg" in t for t in tests)


def copy_project(workdir: Path) -> Path:
    """Legt EINMAL eine Arbeitskopie an; jeder eingebaute Fehler wird danach wieder zurueckgenommen."""
    copy = workdir / "projekt"
    copy.mkdir()
    # Alles, was die Tests lesen (auch Sperrdateien, Dockerfile, CI-Datei) - sonst schlagen Tests in der Kopie schon ohne
    # eingebauten Fehler fehl, und jeder Eintrag gaelte faelschlich als "entdeckt".
    for folder in ("src", "tests", "db", "scripts", "requirements", ".github"):
        shutil.copytree(ROOT / folder, copy / folder, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pyproject.toml", "Dockerfile"):
        shutil.copy(ROOT / name, copy)
    return copy


def stale_entries() -> List[str]:
    """Eintraege, deren Textstelle nicht genau einmal im Projekt steht. Vor dem Lauf geprueft, damit ein veralteter Eintrag
    nicht erst nach langer Laufzeit auffaellt - und damit kein Fehler an einer unbeabsichtigten zweiten Stelle eingebaut wird."""
    stale = []
    for group in GROUPS.values():
        for key, _, edits, _ in group:
            for path, old, _ in edits:
                count = (ROOT / path).read_text(encoding="utf-8").count(old)
                if count != 1:
                    stale.append("%s (%s: %d Fundstellen)" % (key, path, count))
    return stale


def check(mutant: Mutant, copy: Path, with_db: bool) -> Tuple[bool, str]:
    key, _, edits, tests = mutant
    originals = {}
    try:
        for path, old, new in edits:
            target = copy / path
            text = target.read_text(encoding="utf-8")
            originals.setdefault(path, text)
            if old not in text:
                raise SystemExit("Eintrag %s: Textstelle in %s nicht gefunden - bitte den Eintrag anpassen:\n%s"
                                 % (key, path, old))
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if with_db or not k.startswith("TEST_")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # Die Pruefung der Textstellen selbst (test_mutation_entries) zaehlt nie: Jeder eingebaute Fehler veraendert
        # seine eigene Textstelle und liesse sie sonst scheitern - jeder Eintrag gaelte faelschlich als "entdeckt".
        run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                              "--ignore=tests/test_mutation_entries.py", *tests],
                             cwd=copy, capture_output=True, text=True, env=env)
    finally:
        for path, text in originals.items():  # Fehler wieder ausbauen
            (copy / path).write_text(text, encoding="utf-8")
    lines = [line for line in run.stdout.strip().splitlines() if line.strip()]
    failed = next((line[7:110] for line in lines if line.startswith("FAILED")), "")
    if run.returncode == 1:  # Tests sind fehlgeschlagen = Fehler bemerkt
        return True, failed
    if run.returncode == 0:
        return False, lines[-1] if lines else ""
    raise SystemExit("Eintrag %s: Tests konnten nicht ausgefuehrt werden (Code %d):\n%s\n%s"
                     % (key, run.returncode, run.stdout[-800:], run.stderr[-800:]))


def main(argv: Sequence[str]) -> int:
    only: List[str] = []
    if "--only" in argv:
        i = list(argv).index("--only")
        only, argv = list(argv[i + 1:]), argv[:i]
    wanted = list(argv) or list(GROUPS)
    unknown = [g for g in wanted if g not in GROUPS]
    if unknown:
        raise SystemExit("Unbekannte Gruppe: %s (moeglich: %s)" % (", ".join(unknown), ", ".join(GROUPS)))
    keys = [mt[0] for group in GROUPS.values() for mt in group]
    assert len(keys) == len(set(keys)), "Kennungen muessen eindeutig sein"

    stale = stale_entries()
    if stale:
        raise SystemExit("Veraltete Eintraege - bitte an den aktuellen Code anpassen:\n  " + "\n  ".join(stale))

    found = missed = skipped = 0
    survivors: List[str] = []
    workdir = Path(tempfile.mkdtemp(prefix="mutation-check-"))
    try:
        copy = copy_project(workdir)
        # Grundprobe: OHNE eingebauten Fehler muessen alle Tests in der Kopie bestehen. Sonst waere jedes "ENTDECKT"
        # wertlos, weil die Tests ohnehin fehlschlagen.
        broken, detail = check(("BASIS", "Kopie ohne Fehler", [], ALL_TESTS), copy, HAVE_DB)
        if broken:
            raise SystemExit("Grundprobe fehlgeschlagen: Die Tests scheitern in der Kopie schon ohne eingebauten Fehler "
                             "(%s). Erst das beheben." % detail)
        print("[BASIS] Kopie ohne eingebauten Fehler: alle Tests bestanden", flush=True)
        for group in wanted:
            for mutant in GROUPS[group]:
                key, name, _, tests = mutant
                if only and key not in only:
                    continue
                with_db = needs_db(tests)
                if with_db and not HAVE_DB:
                    skipped += 1
                    print("[%s] %s => NICHT GEPRUEFT (keine Datenbank konfiguriert)" % (key, name), flush=True)
                    continue
                detected, detail = check(mutant, copy, with_db)
                if detected:
                    found += 1
                    print("[%s] %s => ENTDECKT (%s)" % (key, name, detail), flush=True)
                else:
                    missed += 1
                    survivors.append("%s %s" % (key, name))
                    print("[%s] %s => NICHT ENTDECKT (!) [%s]" % (key, name, detail), flush=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    print("\nERGEBNIS: %d entdeckt, %d nicht entdeckt, %d nicht geprueft" % (found, missed, skipped))
    for line in survivors:
        print("  fehlender Test fuer: " + line)
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
