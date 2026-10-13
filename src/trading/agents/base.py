"""Gemeinsame Grundklasse der sechs Agenten.

Wichtig: Agenten bekommen KEINE SafetyLimits, KEINEN Risikomanager und KEINEN Executor
in die Hand. Sie liefern nur Vorschlaege und Einschaetzungen. Entscheidungen mit
Konsequenzen trifft ausschliesslich die Pipeline zusammen mit dem unabhaengigen
Risikomanager und dem Executor.
"""

from __future__ import annotations

from abc import ABC
from enum import Enum
from typing import ClassVar, Optional

from trading.ai.provider import AIProvider, NullProvider


class AgentName(str, Enum):
    MOMO = "MOMO"
    RIN = "RIN"
    SORA = "SORA"
    KIBO = "KIBO"
    YUZU = "YUZU"
    AKI = "AKI"


class BaseAgent(ABC):
    name: ClassVar[AgentName]
    role: ClassVar[str]

    def __init__(self, ai: Optional[AIProvider] = None) -> None:
        self._ai = ai or NullProvider()

    @property
    def ai(self) -> AIProvider:
        return self._ai
