"""Lokaler Zwischenspeicher fuer historische Marktdaten (AP-14): eine Datei je Instrument, Art, Intervall und UTC-Tag.

* Gespeichert werden nur ABGESCHLOSSENE Tage (der laufende Tag aendert sich noch) - und genau das, was die Boerse
  geliefert hat (nach Pruefung, ohne Auffuellen).
* Jede Datei traegt ihren Inhalt-Fingerabdruck (SHA-256). Passt er nicht (beschaedigt, von Hand geaendert) oder passen
  Instrument/Art/Tag nicht zum Dateinamen, wird die Datei NICHT benutzt, sondern neu geladen (`discarded` nennt sie).
* Geschrieben wird atomar (temporaere Datei, dann umbenennen): nie eine halb geschriebene Datei.
Der Ordner liegt standardmaessig unter `data/` (nicht im Repository, jederzeit neu ladbar).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from trading.domain.instrument import InstrumentId, is_instrument

Row = Tuple[str, ...]
VERSION = 1


def rows_digest(rows: Sequence[Row]) -> str:
    return hashlib.sha256(json.dumps([list(r) for r in rows], separators=(",", ":")).encode("utf-8")).hexdigest()


class DayCache:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._lock = threading.Lock()
        self.discarded: List[Path] = []

    def path(self, kind: str, instrument: InstrumentId, interval_s: int, day: datetime) -> Path:
        if not is_instrument(instrument) or kind not in ("candles", "funding"):
            raise ValueError("Ungueltiger Cache-Schluessel.")
        return (self._root / instrument.exchange.value / instrument.market_type.value / instrument.symbol
                / ("%s-%d-%s.json" % (kind, interval_s, day.strftime("%Y-%m-%d"))))

    def get(self, kind: str, instrument: InstrumentId, interval_s: int, day: datetime) -> Optional[List[Row]]:
        path = self.path(kind, instrument, interval_s, day)
        if not path.exists():
            return None
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
            rows = [tuple(str(v) for v in r) for r in data["rows"]]
            ok = (data.get("version") == VERSION and data.get("instrument") == instrument.key
                  and data.get("kind") == kind and data.get("interval_s") == interval_s
                  and data.get("day") == day.strftime("%Y-%m-%d") and data.get("sha256") == rows_digest(rows)
                  and all(isinstance(v, str) for r in data["rows"] for v in r))
        except (OSError, ValueError, KeyError, TypeError):
            ok, rows = False, []
        if not ok:
            with self._lock:
                self.discarded.append(path)
            return None
        return rows

    def put(self, kind: str, instrument: InstrumentId, interval_s: int, day: datetime, rows: Sequence[Row]) -> None:
        path = self.path(kind, instrument, interval_s, day)
        body = {"version": VERSION, "instrument": instrument.key, "kind": kind, "interval_s": interval_s,
                "day": day.strftime("%Y-%m-%d"), "rows": [list(r) for r in rows], "sha256": rows_digest(rows)}
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)


__all__ = ["DayCache", "Row", "rows_digest"]
