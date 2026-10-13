"""RIN - regelbasierte Validierung von Handelssignalen (keine KI, keine Schaetzung).

RIN beantwortet: "Ist dieses Setup auf diesen Marktdaten formal und plausibel in Ordnung?"
RIN hat Vetorecht, ersetzt aber NICHT den Risikomanager (der prueft unabhaengig nochmals).

Geprueft wird (alle Verstoesse werden gesammelt):
  1. Handelspaar: korrektes Format UND ausdruecklich erlaubt (Whitelist je Markt); passt zum Snapshot
  2. Spot-Short ist verboten
  3. Marktdaten: vollstaendig, endlich, aktuell, Bid <= Ask, Spread, Kurssprung, Kerzen konsistent
  4. Setup selbst: vollstaendig (nur endliche Decimal-Werte), nicht veraltet, nicht aus der Zukunft
  5. Entry nahe am aktuellen Markt
  6. Stop-Loss / Take-Profit auf der richtigen Seite - gegenueber Entry UND gegenueber dem
     tatsaechlich ausfuehrbaren Kurs (Kurs darf nicht schon hinter Stop/Ziel liegen)
  7. Stop weder zu eng (wuerde von Kosten/Spread aufgefressen) noch zu weit
  8. Chance-Risiko-Verhaeltnis NACH Kosten (Gebuehren, Slippage, Funding)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, List, Optional

from trading.agents.base import AgentName
from trading.agents.contracts import AgentOpinion, MarketContext
from trading.agents.rin import RinAgent
from trading.ai.provider import AIProvider
from trading.clock import Clock
from trading.domain.models import MarketType, Side, TradeSetup
from trading.domain.numeric import is_finite_decimal, is_positive_decimal
from trading.domain.reasons import RejectReason as R
from trading.domain.universe import TradingUniverse
from trading.execution.costs import CostModel
from trading.safety.data_validator import SnapshotValidator
from trading.safety.limits import (
    HARD_MAX_DATA_AGE_S,
    HARD_MAX_PRICE_JUMP,
    SafetyLimits,
)

D = Decimal
_ZERO = D(0)
Fail = Callable[[R, str], None]


@dataclass(frozen=True)
class RinConfig:
    """Schwellen von RIN. Datenpruefungen koennen nur STRENGER als die Obergrenzen sein."""

    min_net_reward_risk: Decimal = D("1.5")
    max_entry_deviation: Decimal = D("0.005")  # 0,5 % Abstand Entry <-> Marktpreis
    min_stop_bps: Decimal = D("10")
    min_stop_cost_multiple: Decimal = D("3")  # Stop-Abstand >= 3x Round-Trip-Kosten
    max_stop_pct: Decimal = D("0.10")
    max_setup_age_s: int = 60
    max_data_age_s: int = HARD_MAX_DATA_AGE_S
    max_spread_bps: Decimal = D("20")
    max_price_jump: Decimal = HARD_MAX_PRICE_JUMP
    max_candle_tick_deviation: Decimal = D("0.05")

    def __post_init__(self) -> None:
        positives = (
            self.min_net_reward_risk, self.max_entry_deviation, self.min_stop_bps,
            self.min_stop_cost_multiple, self.max_stop_pct, self.max_setup_age_s,
            self.max_candle_tick_deviation,
        )
        if not all(is_positive_decimal(D(x)) for x in positives):
            raise ValueError("Alle RIN-Schwellen muessen positiv sein.")

    def data_limits(self) -> SafetyLimits:
        # Wirft LimitViolationError, wenn eine Datenschwelle lockerer als die Obergrenze waere.
        return SafetyLimits(
            max_data_age_s=self.max_data_age_s,
            max_spread_bps=self.max_spread_bps,
            max_price_jump=self.max_price_jump,
        )


class RuleBasedRin(RinAgent):
    def __init__(
        self,
        universe: TradingUniverse,
        clock: Clock,
        config: Optional[RinConfig] = None,
        costs: Optional[CostModel] = None,
        ai: Optional[AIProvider] = None,
    ) -> None:
        super().__init__(ai)
        self._universe = universe
        self._clock = clock
        self._config = config or RinConfig()
        self._costs = costs or CostModel()
        # Eigener Validator (eigener Zustand), unabhaengig vom Validator des Executors.
        self._validator = SnapshotValidator(self._config.data_limits(), clock)
        self._validator_lock = threading.Lock()  # der Validator ist zustandsbehaftet

    # ------------------------------------------------------------------ Vertrag

    def validate(self, setup: TradeSetup, context: MarketContext) -> AgentOpinion:
        reasons = []  # type: List[R]
        notes = []  # type: List[str]

        def fail(code: R, text: str) -> None:
            if code not in reasons:
                reasons.append(code)
            notes.append(text)

        net_rr = self._check(setup, context, fail)
        setup_id = setup.setup_id if isinstance(getattr(setup, "setup_id", None), str) else "?"
        if reasons:
            return AgentOpinion(
                AgentName.RIN, setup_id, False, _ZERO, tuple(notes), tuple(reasons)
            )
        assert net_rr is not None
        score = min(D(1), net_rr / (self._config.min_net_reward_risk * 2))
        return AgentOpinion(
            AgentName.RIN, setup_id, True, score,
            ("RIN: formal und plausibel in Ordnung (Netto-CRV %.2f)" % net_rr,),
        )

    # ------------------------------------------------------------------ Regeln

    def _check(self, setup: TradeSetup, context: MarketContext, fail: Fail) -> Optional[Decimal]:
        cfg = self._config
        snap = context.snapshot

        # --- 1./2. Handelspaar und Markt ---
        if not self._universe.allows(setup.symbol, setup.market_type):
            fail(R.INVALID_SYMBOL, "Handelspaar %r ist fuer %s nicht erlaubt" % (
                setup.symbol, getattr(setup.market_type, "value", setup.market_type)))
        if snap.symbol != setup.symbol or snap.market_type != setup.market_type:
            fail(R.SYMBOL_MISMATCH, "Marktdaten gehoeren nicht zum Handelspaar des Setups")
        if setup.market_type is MarketType.SPOT and setup.side is Side.SHORT:
            fail(R.SPOT_SHORT_NOT_ALLOWED, "Spot-Short ist verboten")
        if not isinstance(setup.side, Side) or not isinstance(setup.market_type, MarketType):
            fail(R.MISSING_DATA, "Seite oder Markttyp fehlen/ungueltig")
            return None

        # --- 3. Marktdaten ---
        with self._validator_lock:
            validation = self._validator.validate(snap)
        for code in validation.reasons:
            fail(code, "Marktdaten: %s" % code.value)
        self._check_candles(context, fail)

        # --- 4. Setup selbst ---
        prices = (setup.entry, setup.stop_loss, setup.take_profit)
        if any(p is None for p in prices):
            fail(R.MISSING_DATA, "Entry, Stop-Loss und Take-Profit sind Pflicht")
            return None
        if not all(is_finite_decimal(p) for p in prices):
            fail(R.NON_FINITE_VALUE, "Entry/Stop/Take-Profit muessen endliche Decimal-Werte sein")
            return None
        if not all(p > 0 for p in prices):
            fail(R.NON_POSITIVE_PRICE, "Entry/Stop/Take-Profit muessen positiv sein")
            return None
        self._check_setup_age(setup, fail)

        # Ab hier braucht es verwertbare Marktpreise.
        if not (is_positive_decimal(snap.bid) and is_positive_decimal(snap.ask)):
            return None
        if snap.bid > snap.ask:
            return None  # bereits als CROSSED_BOOK gemeldet
        fill = self._costs.entry_fill_price(setup.side, snap)
        mid = snap.mid

        # --- 5. Entry nahe am Markt ---
        if abs(setup.entry - mid) / mid > cfg.max_entry_deviation:
            fail(R.ENTRY_DEVIATES_FROM_MARKET, "Entry weicht mehr als %s %% vom Markt ab"
                 % (cfg.max_entry_deviation * 100))

        # --- 6. Geometrie (gegenueber Entry UND gegenueber dem ausfuehrbaren Kurs) ---
        sign = setup.side.sign
        geometry_ok = True
        for ref_name, ref in (("Entry", setup.entry), ("Marktkurs", fill)):
            if not sign * (ref - setup.stop_loss) > 0:
                fail(R.STOP_WRONG_SIDE, "Stop-Loss liegt nicht hinter %s" % ref_name)
                geometry_ok = False
            if not sign * (setup.take_profit - ref) > 0:
                fail(R.TAKE_PROFIT_WRONG_SIDE, "Take-Profit liegt nicht vor %s" % ref_name)
                geometry_ok = False
        if not geometry_ok:
            return None

        # --- 7. Stop-Abstand ---
        stop_dist = abs(fill - setup.stop_loss)
        stop_bps = stop_dist / fill * 10000
        c = self._costs
        roundtrip_bps = (
            c.slippage_bps + c.stop_slippage_bps + (c.taker_fee * 2) * 10000 + snap.spread_bps
        )
        min_bps = max(cfg.min_stop_bps, cfg.min_stop_cost_multiple * roundtrip_bps)
        if stop_bps < min_bps:
            fail(R.STOP_TOO_TIGHT, "Stop-Abstand %.0f bp < Mindestabstand %.0f bp (Kosten!)"
                 % (stop_bps, min_bps))
        if stop_dist / fill > cfg.max_stop_pct:
            fail(R.STOP_TOO_WIDE, "Stop-Abstand ueber %s %%" % (cfg.max_stop_pct * 100))

        # --- 8. Netto-Chance-Risiko ---
        funding = snap.funding_rate if (
            setup.market_type is MarketType.FUTURES and is_finite_decimal(snap.funding_rate)
        ) else None
        risk_unit = c.worst_case_loss(setup.side, D(1), fill, setup.stop_loss, funding)
        reward_unit = (
            sign * (setup.take_profit - fill)
            - c.fee(fill, taker=True)
            - c.fee(setup.take_profit, taker=False)
        )
        if risk_unit <= 0:
            fail(R.STOP_WRONG_SIDE, "Risiko pro Einheit nicht positiv")
            return None
        net_rr = reward_unit / risk_unit
        if net_rr < cfg.min_net_reward_risk:
            fail(R.REWARD_RISK_TOO_LOW, "Netto-CRV %.2f < Minimum %s" % (
                net_rr, cfg.min_net_reward_risk))
        return net_rr

    def _check_setup_age(self, setup: TradeSetup, fail: Fail) -> None:
        created = setup.created_at
        if not isinstance(created, datetime) or created.tzinfo is None:
            fail(R.MISSING_DATA, "Zeitstempel des Setups fehlt oder hat keine Zeitzone")
            return
        age = (self._clock.now() - created).total_seconds()
        if age > self._config.max_setup_age_s:
            fail(R.STALE_SETUP, "Setup ist %.0f s alt (Maximum %s s)" % (
                age, self._config.max_setup_age_s))
        if age < -2:
            fail(R.STALE_SETUP, "Setup liegt in der Zukunft")

    def _check_candles(self, context: MarketContext, fail: Fail) -> None:
        candles = context.candles
        if not candles:
            return
        prev = None
        for cd in candles:
            if not SnapshotValidator._candle_ok(cd, context.snapshot.symbol):
                fail(R.BAD_CANDLE, "Kerze unvollstaendig oder in sich widerspruechlich")
                return
            if prev is not None and cd.open_time < prev.close_time:
                fail(R.BAD_CANDLE, "Kerzen ueberlappen oder sind falsch sortiert")
                return
            prev = cd
        last = candles[-1].close
        tick = context.snapshot.last
        if is_positive_decimal(tick) and abs(tick - last) / last > self._config.max_candle_tick_deviation:
            fail(R.CANDLE_PRICE_MISMATCH, "Aktueller Kurs weicht stark von der letzten Kerze ab")
