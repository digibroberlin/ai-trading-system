"""Unveraenderliches Ereignisprotokoll fuer AKI."""

import dataclasses
import json
from decimal import Decimal

import pytest
from helpers import T0

from trading.audit.event_log import (
    EventLogCorruptedError,
    InMemoryEventLog,
    JsonlEventLog,
    GENESIS_HASH,
)
from trading.audit.events import EventType
from trading.clock import FixedClock
from trading.domain.models import Side


def filled(log, n=3):
    for i in range(n):
        log.append(EventType.SETUP_PROPOSED, {"i": i, "price": Decimal("1.50"), "side": Side.LONG})


def test_chain_is_valid_and_linked():
    log = InMemoryEventLog(FixedClock(T0))
    filled(log)
    evs = log.events()
    assert [e.seq for e in evs] == [1, 2, 3]
    assert evs[0].prev_hash == GENESIS_HASH
    assert evs[1].prev_hash == evs[0].hash
    assert log.verify().ok and log.verify().checked == 3


def test_empty_log_is_valid():
    assert InMemoryEventLog().verify().ok


def test_decimal_enum_and_datetime_survive_exactly():
    log = InMemoryEventLog(FixedClock(T0))
    ev = log.append(EventType.SETUP_PROPOSED, {"p": Decimal("0.10"), "s": Side.SHORT, "t": T0})
    assert ev.payload == {"p": "0.10", "s": "short", "t": T0.isoformat()}


def test_no_way_to_change_or_delete_events_through_the_api():
    log = InMemoryEventLog()
    forbidden = {"update", "delete", "remove", "clear", "pop", "insert", "truncate", "edit", "set"}
    assert not forbidden & {name for name in dir(log) if not name.startswith("_")}
    assert isinstance(log.events(), tuple)  # Kopie, keine veraenderbare interne Liste


def test_payload_is_frozen_at_append_time():
    log = InMemoryEventLog()
    payload = {"reasons": ["A"]}
    log.append(EventType.SETUP_BLOCKED, payload)
    payload["reasons"].append("B")  # spaetere Aenderung am Original
    assert log.events()[0].payload == {"reasons": ["A"]}
    assert log.verify().ok


def test_tampering_with_content_is_detected():
    log = InMemoryEventLog()
    filled(log)
    evs = list(log.events())
    evs[1] = dataclasses.replace(evs[1], payload_json='{"i":99}')
    log._events = evs  # Angriff: direkter Eingriff in den Speicher
    res = log.verify()
    assert not res.ok and res.first_bad_seq == 2


def test_removing_an_event_is_detected():
    log = InMemoryEventLog()
    filled(log, 4)
    log._events = [e for e in log.events() if e.seq != 2]
    assert not log.verify().ok


def test_reordering_is_detected():
    log = InMemoryEventLog()
    filled(log, 3)
    e = list(log.events())
    log._events = [e[0], e[2], e[1]]
    assert not log.verify().ok


def test_nan_and_unknown_types_are_refused_instead_of_silently_logged():
    log = InMemoryEventLog()
    with pytest.raises(ValueError):
        log.append(EventType.SETUP_PROPOSED, {"x": Decimal("NaN")})
    with pytest.raises(ValueError):
        log.append(EventType.SETUP_PROPOSED, {"x": float("inf")})
    with pytest.raises(TypeError):
        log.append(EventType.SETUP_PROPOSED, {"x": object()})
    assert log.events() == ()  # nichts Halbfertiges im Protokoll


# ------------------------------------------------------------------ Datei-Variante


def test_file_log_persists_and_continues_the_chain(tmp_path):
    path = str(tmp_path / "events.jsonl")
    first = JsonlEventLog(path, FixedClock(T0))
    filled(first, 2)
    reopened = JsonlEventLog(path, FixedClock(T0))
    assert len(reopened.events()) == 2 and reopened.verify().ok
    filled(reopened, 1)
    assert [e.seq for e in JsonlEventLog(path).events()] == [1, 2, 3]
    assert JsonlEventLog(path).verify_file().ok


def test_file_log_refuses_to_start_when_file_was_tampered(tmp_path):
    path = tmp_path / "events.jsonl"
    log = JsonlEventLog(str(path), FixedClock(T0))
    filled(log, 3)
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[1])
    record["payload_json"] = '{"i":12345}'
    lines[1] = json.dumps(record, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(EventLogCorruptedError):
        JsonlEventLog(str(path))


def test_file_log_detects_deleted_middle_line(tmp_path):
    path = tmp_path / "events.jsonl"
    log = JsonlEventLog(str(path), FixedClock(T0))
    filled(log, 4)
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(EventLogCorruptedError):
        JsonlEventLog(str(path))


def test_running_log_notices_file_tampering_via_verify_file(tmp_path):
    path = tmp_path / "events.jsonl"
    log = JsonlEventLog(str(path), FixedClock(T0))
    filled(log, 3)
    text = path.read_text(encoding="utf-8").replace('\\"i\\":1', '\\"i\\":7')
    path.write_text(text, encoding="utf-8")
    assert log.verify().ok  # Arbeitsspeicher ist unveraendert ...
    assert not log.verify_file().ok  # ... aber die Datei auf der Platte stimmt nicht mehr


def test_garbage_in_file_is_refused(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("das ist kein json\n", encoding="utf-8")
    with pytest.raises(EventLogCorruptedError):
        JsonlEventLog(str(path))
