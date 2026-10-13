"""Austauschbare KI-Modelle ueber eine einheitliche Schnittstelle.

Regeln:
* KI-Ausgaben sind nur BERATUNG. Sie werden von den Agenten in typisierte Daten
  (AgentOpinion, TradeSetup ...) uebersetzt. Es gibt keinen Weg, dass Text eines Modells
  direkt eine Order, ein Limit oder eine Einstellung veraendert.
* Jeder Provider (Anthropic, OpenAI, lokale Modelle ...) implementiert nur `complete`.
  Echte Provider kommen in einer spaeteren Phase und NUR nach Ruecksprache wegen Kosten.
* Es wird hier bewusst kein Netzwerkcode mitgeliefert.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Mapping


@dataclass(frozen=True)
class AIRequest:
    task: str  # z. B. "kibo.technical_review"
    prompt: str
    context: Mapping[str, object] = field(default_factory=dict)
    max_tokens: int = 1024


@dataclass(frozen=True)
class AIResponse:
    text: str
    provider: str
    model: str


class AIProvider(ABC):
    name = "abstract"

    @abstractmethod
    def complete(self, request: AIRequest) -> AIResponse:
        """Beantwortet eine Anfrage. Darf Exceptions werfen; Aufrufer behandeln sie fail-closed."""


class NullProvider(AIProvider):
    """Offline-Provider ohne KI: liefert immer eine leere, neutrale Antwort."""

    name = "null"

    def complete(self, request: AIRequest) -> AIResponse:
        return AIResponse(text="", provider=self.name, model="none")


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers = {}  # type: Dict[str, AIProvider]

    def register(self, provider: AIProvider) -> None:
        if provider.name in self._providers:
            raise ValueError("Provider %r ist bereits registriert." % provider.name)
        self._providers[provider.name] = provider

    def get(self, name: str) -> AIProvider:
        try:
            return self._providers[name]
        except KeyError:
            raise KeyError("Unbekannter KI-Provider: %r" % name) from None

    def names(self) -> List[str]:
        return sorted(self._providers)
