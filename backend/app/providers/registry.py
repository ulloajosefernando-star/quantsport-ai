from __future__ import annotations

from app.core.config import Settings
from app.providers.api_football import ApiFootballProvider
from app.providers.base import DataProvider
from app.providers.csv_footballdata import CsvProvider
from app.providers.demo import DemoProvider
from app.providers.football_data_org import FootballDataOrgProvider
from app.providers.pending import pending_providers
from app.providers.the_odds_api import TheOddsApiProvider


def all_providers(settings: Settings) -> list[DataProvider]:
    return [
        DemoProvider(settings.demo_seed),
        CsvProvider(settings.imports_dir),
        FootballDataOrgProvider(settings),
        TheOddsApiProvider(settings),
        ApiFootballProvider(settings),
        *pending_providers(),
    ]


def get_provider(settings: Settings, key: str) -> DataProvider | None:
    for p in all_providers(settings):
        if p.info.key == key:
            return p
    return None
