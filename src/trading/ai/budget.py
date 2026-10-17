"""Harte Budgets fuer KI-Aufrufe (je Aufruf, Tag und Monat, in USD).

Ablauf je Aufruf: VOR dem Aufruf wird die hoechstmoegliche Kost (Obergrenze, siehe pricing.ModelPrice.max_cost)
reserviert. Passt sie nicht mehr in eines der Budgets, findet der Aufruf nicht statt. NACH dem Aufruf wird mit den
tatsaechlichen Kosten abgerechnet. Scheitert der Aufruf, bleibt die volle Reservierung als verbraucht stehen (der Anbieter
koennte trotzdem abgerechnet haben - im Zweifel teurer rechnen).

Grundsaetze:
* Standard ist 0 USD: Ohne ausdrueckliche Freigabe des Eigners (B-004) gibt es keinen einzigen bezahlten Aufruf.
* Tag und Monat in UTC (ueber die Clock).
* Waren die tatsaechlichen Kosten hoeher als die Reservierung (darf nicht vorkommen - dann stimmt eine Annahme der
  Obergrenze nicht), sperrt sich das Budget fuer alle weiteren Aufrufe (fail closed), bis ein Mensch es prueft.
* Sicherheitspruefungen haengen nie vom Budget ab: Ist es erschoepft, entscheiden die Agenten regelbasiert weiter.
"""

from __future__ import annotations

import itertools
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from decimal import InvalidOperation
from typing import Dict, Mapping, Optional, Tuple

from trading.clock import Clock
from trading.domain.numeric import is_finite_decimal

ZERO = Decimal(0)


class BudgetError(RuntimeError):
    """Aufruf nicht erlaubt (Budget erschoepft, gesperrt oder ungueltige Betraege)."""


@dataclass(frozen=True)
class AIBudget:
    per_call_usd: Decimal = ZERO
    daily_usd: Decimal = ZERO
    monthly_usd: Decimal = ZERO

    def __post_init__(self) -> None:
        for name in ("per_call_usd", "daily_usd", "monthly_usd"):
            value = getattr(self, name)
            if not is_finite_decimal(value) or value < 0:
                raise BudgetError("Budget %s ungueltig: %r" % (name, value))
        if self.daily_usd > self.monthly_usd:
            raise BudgetError("Tagesbudget darf nicht ueber dem Monatsbudget liegen.")

    @property
    def allows_spending(self) -> bool:
        return self.per_call_usd > 0 and self.daily_usd > 0 and self.monthly_usd > 0


NO_BUDGET = AIBudget()
NOT_APPROVED = "KI nicht freigegeben (Budget 0): kein Aufruf, auch nicht zum Preis 0."
BUDGET_ENV = ("AI_BUDGET_PER_CALL_USD", "AI_BUDGET_DAILY_USD", "AI_BUDGET_MONTHLY_USD")


def budget_from_env(env: Mapping[str, str]) -> AIBudget:
    """Budget der Anwendung aus der Umgebung. Fehlt ein Wert: 0 (kein bezahlter Aufruf). Ungueltig: BudgetError.

    Die Werte setzt nur der Eigner (Freigabe B-004). Im Betrieb gilt zusaetzlich die Grenze in der Datenbank
    (persistence/ai_budget.py) - die strengere gewinnt.
    """
    values = []
    for name in BUDGET_ENV:
        raw = (env.get(name) or "0").strip()
        try:
            values.append(Decimal(raw))
        except (InvalidOperation, ValueError):
            raise BudgetError("%s ist keine Zahl: %r" % (name, raw)) from None
    return AIBudget(*values)


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    amount: Decimal
    day: date
    month: Tuple[int, int]
    agent: str
    model: str


@dataclass(frozen=True)
class BudgetStatus:
    day: date
    spent_today: Decimal
    spent_month: Decimal
    budget: AIBudget
    blocked: bool
    note: str = ""

    @property
    def remaining_today(self) -> Decimal:
        return max(ZERO, min(self.budget.daily_usd - self.spent_today, self.budget.monthly_usd - self.spent_month))


class BudgetLedger(ABC):
    """Fuehrt Reservierungen und Abrechnungen. Implementierungen muessen `reserve` unteilbar machen."""

    @abstractmethod
    def reserve(self, amount: Decimal, agent: str, model: str) -> Reservation:
        """Reserviert `amount` oder wirft BudgetError (dann KEIN Aufruf)."""

    @abstractmethod
    def settle(self, reservation: Reservation, actual: Optional[Decimal]) -> None:
        """Rechnet ab. `actual=None` (Aufruf gescheitert) -> die volle Reservierung gilt als verbraucht."""

    @abstractmethod
    def status(self) -> BudgetStatus:
        """Verbrauch heute und in diesem Monat (Reservierungen zaehlen voll, bis sie abgerechnet sind)."""

    @abstractmethod
    def block(self, reason: str) -> None:
        """Sperrt alle weiteren Aufrufe (z. B. wenn eine Annahme der Kostenobergrenze verletzt wurde)."""


def check_amount(amount: Decimal) -> None:
    if not is_finite_decimal(amount) or amount < 0:
        raise BudgetError("Betrag ungueltig: %r" % (amount,))


class InMemoryBudgetLedger(BudgetLedger):
    """Budget im Speicher eines Prozesses (Backtests, Tests). Fuer den Betrieb: persistence/ai_budget.py."""

    def __init__(self, budget: AIBudget, clock: Clock) -> None:
        self._budget = budget
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: Dict[str, Tuple[date, Decimal, Optional[Decimal]]] = {}  # id -> (Tag, reserviert, abgerechnet)
        self._ids = itertools.count(1)
        self._blocked = ""

    def _spent(self, day: date) -> Tuple[Decimal, Decimal]:
        today = month = ZERO
        for entry_day, reserved, actual in self._entries.values():
            used = reserved if actual is None else actual
            if (entry_day.year, entry_day.month) == (day.year, day.month):
                month += used
                if entry_day == day:
                    today += used
        return today, month

    def reserve(self, amount: Decimal, agent: str, model: str) -> Reservation:
        check_amount(amount)
        now: datetime = self._clock.now()
        day = now.date()
        with self._lock:
            if self._blocked:
                raise BudgetError("KI-Budget gesperrt: %s" % self._blocked)
            if not self._budget.allows_spending:  # Budget 0 = keine Freigabe: auch kein Aufruf zum Preis 0
                raise BudgetError(NOT_APPROVED)
            if amount > self._budget.per_call_usd:
                raise BudgetError("Aufruf (%s USD) ueber dem Einzelbudget (%s USD)." % (amount, self._budget.per_call_usd))
            today, month = self._spent(day)
            if today + amount > self._budget.daily_usd:
                raise BudgetError("Tagesbudget reicht nicht (%s + %s > %s USD)." % (today, amount, self._budget.daily_usd))
            if month + amount > self._budget.monthly_usd:
                raise BudgetError("Monatsbudget reicht nicht (%s + %s > %s USD)." % (month, amount, self._budget.monthly_usd))
            rid = "R%08d" % next(self._ids)
            self._entries[rid] = (day, amount, None)
            return Reservation(rid, amount, day, (day.year, day.month), agent, model)

    def settle(self, reservation: Reservation, actual: Optional[Decimal]) -> None:
        with self._lock:
            entry = self._entries.get(reservation.reservation_id)
            if entry is None or entry[2] is not None:
                raise BudgetError("Reservierung %s unbekannt oder schon abgerechnet." % reservation.reservation_id)
            day, reserved, _ = entry
            if actual is None:
                actual = reserved  # Aufruf gescheitert: im Zweifel voll berechnen
            check_amount(actual)
            if actual > reserved:
                self._blocked = ("tatsaechliche Kosten %s ueber der Reservierung %s (Annahme der Obergrenze verletzt)"
                                 % (actual, reserved))
            self._entries[reservation.reservation_id] = (day, reserved, actual)

    def block(self, reason: str) -> None:
        with self._lock:
            if not self._blocked:
                self._blocked = reason or "gesperrt"

    def status(self) -> BudgetStatus:
        day = self._clock.now().date()
        with self._lock:
            today, month = self._spent(day)
            return BudgetStatus(day, today, month, self._budget, bool(self._blocked), self._blocked)


__all__ = [
    "AIBudget", "BUDGET_ENV", "BudgetError", "BudgetLedger", "BudgetStatus", "InMemoryBudgetLedger", "NO_BUDGET",
    "Reservation", "budget_from_env", "check_amount",
]
