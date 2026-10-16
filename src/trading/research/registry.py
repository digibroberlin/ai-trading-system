"""Strategie-Register (AP-18): Versionen, Versuchszaehlung, Testdaten nur EINMAL je Version, Freigabestatus.

Gegen Ueberanpassung zaehlt das Register JEDEN Versuch (jede Parameter-Variante, die auf Validierungsdaten bewertet
wurde) je Strategie-Familie - die Anforderung an das Ergebnis steigt mit der Zahl der Versuche (siehe
`experiment.deflated_sharpe`). Die Testdaten einer Version duerfen genau einmal angesehen werden; ein zweiter Blick
ist ein Fehler (sonst wird der Test unbemerkt zur zweiten Validierung).

Ablauf: IDEE -> (Validierung) VALIDIERT oder VERWORFEN -> (Test, einmal) GETESTET (bestanden oder nicht) ->
FREIGEGEBEN_PAPER (nur mit Namen; das ist eine Empfehlung fuer den Paper-Betrieb, kein Start - den gibt nur der Eigner).
Das Register liegt als JSON-Datei vor (atomar geschrieben) oder nur im Speicher.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from trading.audit.events import to_jsonable


class Status(str, Enum):
    IDEA = "IDEE"
    VALIDATED = "VALIDIERT"
    REJECTED = "VERWORFEN"
    TESTED = "GETESTET"
    APPROVED_PAPER = "FREIGEGEBEN_PAPER"


class RegistryError(RuntimeError):
    """Verstoss gegen die Forschungsregeln (z. B. Testdaten zweimal angesehen)."""


@dataclass(frozen=True)
class StrategyVersion:
    family: str  # z. B. "breakout"
    params: Mapping[str, str]  # alle Einstellungen als Text (Decimal ohne Rundung)

    @property
    def key(self) -> str:
        body = json.dumps(dict(sorted(self.params.items())), sort_keys=True, separators=(",", ":"))
        return "%s@%s" % (self.family, hashlib.sha256(body.encode("utf-8")).hexdigest()[:12])


class StrategyRegistry:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {"families": {}, "versions": {}}
        if path is not None and path.exists():
            self._data = json.loads(path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ lesen

    def trials(self, family: str) -> int:
        return int(self._data["families"].get(family, {}).get("trials", 0))

    def entry(self, version: StrategyVersion) -> Dict[str, Any]:
        found = self._data["versions"].get(version.key)
        if found is None:
            raise RegistryError("Version %s ist nicht registriert." % version.key)
        return dict(found)

    def status(self, version: StrategyVersion) -> Status:
        return Status(self.entry(version)["status"])

    def entries(self) -> List[Dict[str, Any]]:
        return [dict(v, key=k) for k, v in sorted(self._data["versions"].items())]

    # ------------------------------------------------------------------ schreiben

    def register(self, version: StrategyVersion) -> None:
        with self._lock:
            if version.key not in self._data["versions"]:
                self._data["versions"][version.key] = {"family": version.family, "params": dict(version.params),
                                                       "status": Status.IDEA.value, "validation": None, "test": None,
                                                       "test_opened": False, "approved_by": None}
                self._save()

    def record_validation(self, version: StrategyVersion, metrics: Mapping[str, Any], passed: bool, note: str) -> int:
        """Ein Versuch auf Validierungsdaten - zaehlt IMMER (auch wenn verworfen). Rueckgabe: Versuche der Familie."""
        with self._lock:
            entry = self._version(version)
            if entry["status"] not in (Status.IDEA.value, Status.VALIDATED.value, Status.REJECTED.value):
                raise RegistryError("Version %s wurde schon getestet - keine weitere Validierung." % version.key)
            family = self._data["families"].setdefault(version.family, {"trials": 0})
            family["trials"] += 1
            entry["validation"] = {"metrics": to_jsonable(dict(metrics)), "passed": passed, "note": note,
                                   "trial": family["trials"]}
            entry["status"] = (Status.VALIDATED if passed else Status.REJECTED).value
            self._save()
            return int(family["trials"])

    def open_test(self, version: StrategyVersion) -> None:
        """Gibt die Testdaten frei - genau einmal je Version und nur nach bestandener Validierung."""
        with self._lock:
            entry = self._version(version)
            if entry["test_opened"]:
                raise RegistryError("Testdaten fuer %s wurden schon angesehen - kein zweiter Test." % version.key)
            if entry["status"] != Status.VALIDATED.value:
                raise RegistryError("Test nur nach bestandener Validierung (Status %s)." % entry["status"])
            entry["test_opened"] = True  # vor dem Lauf gespeichert: auch ein abgebrochener Test zaehlt
            self._save()

    def record_test(self, version: StrategyVersion, metrics: Mapping[str, Any], passed: bool, note: str) -> None:
        with self._lock:
            entry = self._version(version)
            if not entry["test_opened"] or entry["test"] is not None:
                raise RegistryError("Testergebnis nur einmal und nur nach open_test.")
            entry["test"] = {"metrics": to_jsonable(dict(metrics)), "passed": passed, "note": note}
            entry["status"] = Status.TESTED.value
            self._save()

    def approve_for_paper(self, version: StrategyVersion, approved_by: str) -> None:
        name = (approved_by or "").strip()
        with self._lock:
            entry = self._version(version)
            if not name:
                raise RegistryError("Freigabe nur mit Namen.")
            if entry["status"] != Status.TESTED.value or not (entry["test"] or {}).get("passed"):
                raise RegistryError("Freigabe nur nach bestandenem Test.")
            entry["status"], entry["approved_by"] = Status.APPROVED_PAPER.value, name
            self._save()

    # ------------------------------------------------------------------ intern

    def _version(self, version: StrategyVersion) -> Dict[str, Any]:
        entry = self._data["versions"].get(version.key)
        if entry is None:
            raise RegistryError("Version %s ist nicht registriert." % version.key)
        return entry  # type: ignore[no-any-return]

    def _save(self) -> None:
        if self._path is None:
            return
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, sort_keys=True, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._path)  # atomar: nie eine halb geschriebene Datei


__all__ = ["RegistryError", "Status", "StrategyRegistry", "StrategyVersion"]
