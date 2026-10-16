"""Hartes KI-Budget in PostgreSQL (Migration 004): ueberlebt Neustarts, gilt fuer alle Prozesse, Grenzen nur vom
Administrator, die Datenbank selbst verweigert jede Ueberschreitung."""

from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal

import pytest

# Ohne psycopg (z. B. Python 3.9, Kern-Pruefung) sichtbar ueberspringen statt beim Einsammeln abzubrechen.
psycopg = pytest.importorskip("psycopg", reason="psycopg nicht installiert - Datenbank-Tests uebersprungen")
from conftest import requires_db  # noqa: E402
from helpers import T0  # noqa: E402

from psycopg import errors as E  # noqa: E402
from trading.ai.budget import AIBudget, BudgetError  # noqa: E402
from trading.clock import FixedClock  # noqa: E402
from trading.persistence.ai_budget import PostgresBudgetLedger, set_limits_as_admin, unblock_as_admin  # noqa: E402

pytestmark = requires_db
D = Decimal
APP = AIBudget(D("1"), D("5"), D("50"))  # Anwendung erlaubt viel - die Datenbank-Grenzen sind strenger


def admin(pg):
    return psycopg.connect(pg.admin_url, autocommit=True)


def app(pg):
    return psycopg.connect(pg.app_url, autocommit=True)


def ledger(pg, budget=APP, clock=None):
    return PostgresBudgetLedger(pg.app_url, budget, clock or FixedClock(T0))


def test_fresh_database_allows_no_paid_call_at_all(pg):
    led = ledger(pg)
    with pytest.raises(BudgetError):
        led.reserve(D("0.00000001"), "KIBO", "m")
    status = led.status()
    assert status.budget.daily_usd == 0 and not status.blocked


def test_database_limits_hold_even_if_the_application_allows_more(pg):
    set_limits_as_admin(pg.admin_url, AIBudget(D("0.5"), D("1"), D("2")), "Eigner")
    led = ledger(pg)
    with pytest.raises(BudgetError):
        led.reserve(D("0.6"), "KIBO", "m")  # Einzelgrenze der Datenbank
    a = led.reserve(D("0.5"), "KIBO", "m")
    led.reserve(D("0.5"), "KIBO", "m")
    with pytest.raises(BudgetError):
        led.reserve(D("0.01"), "KIBO", "m")  # Tagesgrenze
    led.settle(a, D("0.2"))
    led.reserve(D("0.3"), "KIBO", "m")
    assert led.status().spent_today == D("1.0")


def test_application_budget_is_also_respected(pg):
    set_limits_as_admin(pg.admin_url, AIBudget(D("1"), D("10"), D("100")), "Eigner")
    led = ledger(pg, AIBudget(D("0.1"), D("0.2"), D("1")))
    led.reserve(D("0.1"), "KIBO", "m")
    led.reserve(D("0.1"), "KIBO", "m")
    with pytest.raises(BudgetError):
        led.reserve(D("0.1"), "KIBO", "m")
    assert led.status().budget == AIBudget(D("0.1"), D("0.2"), D("1"))  # die strengere Grenze gilt


def test_spending_survives_a_restart_and_months_roll_over(pg):
    set_limits_as_admin(pg.admin_url, AIBudget(D("1"), D("1"), D("1.5")), "Eigner")
    clock = FixedClock(T0)
    ledger(pg, clock=clock).reserve(D("1"), "KIBO", "m")
    restarted = ledger(pg, clock=clock)
    with pytest.raises(BudgetError):
        restarted.reserve(D("0.01"), "KIBO", "m")
    clock.set(T0 + timedelta(days=1))
    restarted.reserve(D("0.5"), "KIBO", "m")
    with pytest.raises(BudgetError):
        restarted.reserve(D("0.01"), "KIBO", "m")  # Monatsgrenze
    clock.set(T0.replace(month=2, day=1))
    restarted.reserve(D("1"), "KIBO", "m")


def test_parallel_reservations_never_exceed_the_daily_limit(pg):
    set_limits_as_admin(pg.admin_url, AIBudget(D("0.1"), D("1"), D("10")), "Eigner")
    ok = []

    def worker():
        led = ledger(pg)
        for _ in range(8):
            try:
                led.reserve(D("0.1"), "KIBO", "m")
                ok.append(1)
            except BudgetError:
                pass
        led.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 10
    with admin(pg) as c:
        assert c.execute("SELECT SUM(reserved) FROM ai_budget_entries").fetchone()[0] == D("1.0")


def test_application_can_neither_raise_limits_nor_unblock(pg):
    with app(pg) as c:
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("UPDATE ai_budget_limits SET daily_usd = 100, monthly_usd = 100, per_call_usd = 1")
        c.execute("UPDATE ai_budget_limits SET blocked = true, block_reason = 'Test'")
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("UPDATE ai_budget_limits SET blocked = false")
        for stmt in ("DELETE FROM ai_budget_entries", "DELETE FROM ai_budget_limits", "TRUNCATE ai_budget_entries"):
            with pytest.raises(E.InsufficientPrivilege):
                c.execute(stmt)
    with pytest.raises(BudgetError):
        unblock_as_admin(pg.admin_url, " ")
    unblock_as_admin(pg.admin_url, "Eigner")
    with admin(pg) as c:
        assert c.execute("SELECT blocked FROM ai_budget_limits").fetchone() == (False,)


@pytest.mark.parametrize("stmt", ["DELETE FROM ai_budget_entries", "TRUNCATE ai_budget_entries",
                                  "DELETE FROM ai_budget_limits",
                                  "INSERT INTO ai_budget_limits (id) VALUES (2)"])
def test_even_the_administrator_cannot_delete_budget_history(pg, stmt):
    set_limits_as_admin(pg.admin_url, AIBudget(D("1"), D("1"), D("1")), "Eigner")
    ledger(pg).reserve(D("0.5"), "KIBO", "m")
    with admin(pg) as c, pytest.raises(E.InsufficientPrivilege, match="nicht erlaubt"):
        c.execute(stmt)


def test_limits_need_the_name_of_the_approval(pg):
    with pytest.raises(BudgetError):
        set_limits_as_admin(pg.admin_url, AIBudget(D("1"), D("1"), D("1")), "")
    with admin(pg) as c, pytest.raises(E.CheckViolation):
        c.execute("UPDATE ai_budget_limits SET daily_usd = 1, monthly_usd = 1")


def test_entries_are_settled_exactly_once_and_otherwise_immutable(pg):
    set_limits_as_admin(pg.admin_url, AIBudget(D("1"), D("1"), D("1")), "Eigner")
    led = ledger(pg)
    r = led.reserve(D("0.5"), "KIBO", "m")
    led.settle(r, D("0.1"))
    with pytest.raises(BudgetError):
        led.settle(r, D("0.05"))
    with app(pg) as c:
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("UPDATE ai_budget_entries SET actual = 0")
        with pytest.raises(E.InsufficientPrivilege):
            c.execute("UPDATE ai_budget_entries SET reserved = 0")


def test_cost_above_reservation_blocks_the_budget_for_every_process(pg):
    set_limits_as_admin(pg.admin_url, AIBudget(D("1"), D("1"), D("1")), "Eigner")
    led = ledger(pg)
    r = led.reserve(D("0.01"), "KIBO", "m")
    led.settle(r, D("0.02"))
    other = ledger(pg)
    assert other.status().blocked
    with pytest.raises(BudgetError, match="gesperrt"):
        other.reserve(D("0.01"), "KIBO", "m")
    with app(pg) as c, pytest.raises(E.CheckViolation):  # auch direkt an der Anwendung vorbei: die Datenbank sperrt
        c.execute("INSERT INTO ai_budget_entries (reservation_id, created_at, day, agent, model, reserved)"
                  " VALUES ('x', now(), current_date, 'KIBO', 'm', 0.01)")


def test_failed_call_keeps_the_full_reservation(pg):
    set_limits_as_admin(pg.admin_url, AIBudget(D("1"), D("1"), D("1")), "Eigner")
    led = ledger(pg)
    r = led.reserve(D("0.4"), "KIBO", "m")
    led.settle(r, None)
    assert led.status().spent_today == D("0.4")


def test_orchestrator_with_the_database_budget(pg):
    """Ende zu Ende: ohne Datenbank-Freigabe kein Aufruf; mit Freigabe wird reserviert und mit den echten Kosten
    abgerechnet - und das Budget gilt auch fuer einen neu gestarteten Prozess."""
    from test_ai_orchestration import FakeProvider, request

    from trading.ai.activation import DEFAULT_ACTIVATION, ActivationPolicy
    from trading.ai.orchestrator import AIOrchestrator, Source
    from trading.ai.pricing import DEFAULT_PRICES
    from trading.ai.provider import ProviderRegistry

    def orchestrator(provider, clock):
        reg = ProviderRegistry()
        reg.register(provider)
        return AIOrchestrator(reg, DEFAULT_PRICES, ledger(pg, clock=clock), ActivationPolicy(DEFAULT_ACTIVATION, clock),
                              clock)

    clock = FixedClock(T0)
    provider = FakeProvider()
    assert orchestrator(provider, clock).analyze(request()).reason == "BUDGET"
    assert provider.calls == []
    set_limits_as_admin(pg.admin_url, AIBudget(D("0.01"), D("0.02"), D("1")), "Eigner")
    a = orchestrator(provider, clock).analyze(request())
    assert a.source is Source.AI and len(provider.calls) == 1
    with admin(pg) as c:
        reserved, actual = c.execute("SELECT reserved, actual FROM ai_budget_entries").fetchone()
    assert actual == a.cost and 0 < actual <= reserved
