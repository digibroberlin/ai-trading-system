"""Hartes KI-Budget in PostgreSQL (Migration 006): ueberlebt Neustarts, gilt fuer alle Prozesse, Grenzen nur vom
Administrator, die Datenbank selbst verweigert jede Ueberschreitung. Tag und Monat bestimmt die Datenbank-Uhr."""

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


def server_today(pg):
    with admin(pg) as c:
        return c.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]


def test_spending_survives_a_restart(pg):
    set_limits_as_admin(pg.admin_url, AIBudget(D("1"), D("1"), D("1.5")), "Eigner")
    ledger(pg).reserve(D("1"), "KIBO", "m")
    restarted = ledger(pg)
    with pytest.raises(BudgetError):
        restarted.reserve(D("0.01"), "KIBO", "m")
    assert restarted.status().spent_today == D("1")


@pytest.mark.parametrize("app_time", [T0.replace(year=2031, month=7), T0.replace(year=1999)])
def test_a_wrong_application_clock_cannot_book_into_another_day_or_month(pg, app_time):
    """Die Anwendungsuhr geht falsch (Zukunft oder Vergangenheit): gebucht wird trotzdem auf den Tag der Datenbank, und
    die heutigen Grenzen gelten weiter - ein neuer Tag laesst sich nicht vortaeuschen."""
    set_limits_as_admin(pg.admin_url, AIBudget(D("1"), D("1"), D("10")), "Eigner")
    ledger(pg).reserve(D("1"), "KIBO", "m")
    wrong = ledger(pg, clock=FixedClock(app_time))
    with pytest.raises(BudgetError):
        wrong.reserve(D("0.01"), "KIBO", "m")
    status = wrong.status()
    assert status.day == server_today(pg) and status.spent_today == D("1")
    with admin(pg) as c:
        assert c.execute("SELECT DISTINCT day FROM ai_budget_entries").fetchall() == [(server_today(pg),)]
        # auch direkt eingefuegte Werte fuer Tag und Zeitpunkt ueberschreibt die Datenbank
        c.execute("UPDATE ai_budget_limits SET daily_usd = 5, approved_by = 'Eigner', approved_at = clock_timestamp()")
        c.execute("INSERT INTO ai_budget_entries (reservation_id, created_at, day, agent, model, reserved)"
                  " VALUES ('direkt', '2031-07-01T00:00Z', '2031-07-01', 'KIBO', 'm', 0.5)")
        day, created = c.execute("SELECT day, created_at::date FROM ai_budget_entries WHERE reservation_id = 'direkt'"
                                 ).fetchone()
    assert day == server_today(pg) and created.year == server_today(pg).year


def test_only_this_month_counts_for_the_monthly_limit(pg):
    """Monatsfenster: Eintraege des Vormonats zaehlen nicht, fruehere Tage dieses Monats zaehlen zum Monat, nicht zu heute.
    (Die alten Eintraege werden mit abgeschaltetem Gurt eingefuegt - anders laesst sich kein anderer Tag buchen.)"""
    today = server_today(pg)
    first = today.replace(day=1)
    last_month = first - timedelta(days=1)
    with admin(pg) as c, c.transaction():
        c.execute("ALTER TABLE ai_budget_entries DISABLE TRIGGER ai_budget_reserve_check")
        for rid, day, amount in (("alt", last_month, "7"), ("monat", first, "2")):
            c.execute("INSERT INTO ai_budget_entries (reservation_id, created_at, day, agent, model, reserved, actual,"
                      " settled_at) VALUES (%s, now(), %s, 'KIBO', 'm', %s, %s, now())", (rid, day, amount, amount))
        c.execute("ALTER TABLE ai_budget_entries ENABLE TRIGGER ai_budget_reserve_check")
    set_limits_as_admin(pg.admin_url, AIBudget(D("1"), D("3"), D("3")), "Eigner")
    led = ledger(pg, AIBudget(D("1"), D("5"), D("50")))
    status = led.status()
    assert status.spent_month == D("2") and status.spent_today == (D("2") if today == first else 0)
    led.reserve(D("1"), "KIBO", "m")  # Monat 2 + 1 = 3: genau an der Grenze
    with pytest.raises(BudgetError):
        led.reserve(D("0.01"), "KIBO", "m")  # Monatsgrenze (der Vormonat zaehlt nicht mit)


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


def test_limits_need_the_name_and_a_new_time_of_the_approval(pg):
    with pytest.raises(BudgetError):
        set_limits_as_admin(pg.admin_url, AIBudget(D("1"), D("1"), D("1")), "")
    with admin(pg) as c, pytest.raises(E.CheckViolation):
        c.execute("UPDATE ai_budget_limits SET daily_usd = 1, monthly_usd = 1")
    set_limits_as_admin(pg.admin_url, AIBudget(D("1"), D("1"), D("1")), "Eigner")
    with admin(pg) as c:
        with pytest.raises(E.CheckViolation, match="neuem Zeitpunkt"):  # alte Freigabe gilt nicht fuer neue Grenzen
            c.execute("UPDATE ai_budget_limits SET daily_usd = 2, monthly_usd = 2")
        with pytest.raises(E.CheckViolation, match="neuem Zeitpunkt"):
            c.execute("UPDATE ai_budget_limits SET daily_usd = 2, monthly_usd = 2, approved_by = 'Jemand'")
        with pytest.raises(E.CheckViolation, match="Namen"):  # neuer Zeitpunkt, aber ohne Namen
            c.execute("UPDATE ai_budget_limits SET daily_usd = 2, monthly_usd = 2, approved_by = ' ',"
                      " approved_at = clock_timestamp()")
        c.execute("UPDATE ai_budget_limits SET daily_usd = 2, monthly_usd = 2, approved_by = 'Eigner',"
                  " approved_at = clock_timestamp()")
        assert c.execute("SELECT daily_usd, approved_by FROM ai_budget_limits").fetchone() == (D("2"), "Eigner")


def test_unblocking_needs_a_name_and_a_new_time(pg):
    ledger(pg).block("Test")
    with admin(pg) as c:
        for stmt in ("UPDATE ai_budget_limits SET blocked = false",
                     "UPDATE ai_budget_limits SET blocked = false, unblocked_by = 'Eigner'",
                     "UPDATE ai_budget_limits SET blocked = false, unblocked_by = ' ', unblocked_at = clock_timestamp()"):
            with pytest.raises(E.CheckViolation, match="Entsperren"):
                c.execute(stmt)
    unblock_as_admin(pg.admin_url, "Eigner")
    ledger(pg).block("nochmal")
    with admin(pg) as c, pytest.raises(E.CheckViolation, match="Entsperren"):  # die alte Freigabe reicht nicht
        c.execute("UPDATE ai_budget_limits SET blocked = false")
    unblock_as_admin(pg.admin_url, "Eigner")
    with admin(pg) as c:
        blocked, by, at = c.execute("SELECT blocked, unblocked_by, unblocked_at FROM ai_budget_limits").fetchone()
    assert not blocked and by == "Eigner" and at is not None


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
