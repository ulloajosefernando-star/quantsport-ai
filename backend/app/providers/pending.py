"""Adaptadores PENDIENTES de implementación/configuración (Fase 2).

Se registran para que el dashboard muestre qué fuentes están previstas, su coste y su
estado real. No devuelven datos: cualquier llamada lanza ProviderNotConfigured.
"""

from __future__ import annotations

from app.providers.base import DataProvider, ProviderInfo, ProviderKind, ProviderNotConfigured


class PendingProvider(DataProvider):
    def __init__(self, info: ProviderInfo, reason: str) -> None:
        self.info = info
        self.reason = reason

    def _fail(self, *_a, **_k):
        raise ProviderNotConfigured(f"{self.info.name}: {self.reason}")

    fetch_results = fetch_fixtures = fetch_odds = fetch_competitions = _fail  # type: ignore[assignment]


def pending_providers() -> list[PendingProvider]:
    return [
        PendingProvider(
            ProviderInfo(
                key="nflverse", name="nflverse (datasets públicos)", kind=ProviderKind.PENDING,
                sports=["nfl"], capabilities=[], configured=False,
                docs_url="https://github.com/nflverse/nflverse-data",
                cost_note="0 MXN", coverage_note="Play-by-play, EPA, rosters (históricos).",
            ),
            "Importador previsto para Fase 2/3.",
        ),
        PendingProvider(
            ProviderInfo(
                key="mlb_stats_api", name="MLB Stats API", kind=ProviderKind.PENDING,
                sports=["mlb"], capabilities=[], configured=False,
                docs_url="https://statsapi.mlb.com",
                cost_note="0 MXN", license_note="Revisar términos de uso (uso no comercial).",
            ),
            "Adaptador previsto para Fase 2/3.",
        ),
        PendingProvider(
            ProviderInfo(
                key="nba_source", name="Fuente NBA (por decidir)", kind=ProviderKind.PENDING,
                sports=["nba"], capabilities=[], configured=False,
                cost_note="Por evaluar (balldontlie / API-NBA tienen planes gratuitos limitados).",
            ),
            "Fuente autorizada por seleccionar en Fase 3.",
        ),
        PendingProvider(
            ProviderInfo(
                key="tennis_datasets", name="Datasets de tenis (Sackmann / tennis-data.co.uk)",
                kind=ProviderKind.PENDING, sports=["tennis"], capabilities=[], configured=False,
                docs_url="https://github.com/JeffSackmann/tennis_atp",
                cost_note="0 MXN", license_note="tennis_atp: CC BY-NC-SA 4.0 (uso no comercial).",
            ),
            "Importador previsto para Fase 2/3.",
        ),
    ]
