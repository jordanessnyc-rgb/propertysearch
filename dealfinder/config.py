"""Settings: loaded from a TOML file (see config.example.toml) plus environment variables.

Secrets (API keys, MLS tokens) are read from the environment, never from the TOML file,
so the config file is safe to commit.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Underwriting:
    # Financing. financing = "auto" uses cash for listings flagged cash-only, a loan otherwise.
    financing: str = "auto"          # auto | cash | loan
    down_payment_pct: float = 0.25
    interest_rate: float = 0.075
    loan_term_years: int = 30
    closing_cost_pct: float = 0.03

    # Operating expenses
    vacancy_pct: float = 0.06
    management_pct: float = 0.08
    maintenance_pct: float = 0.08
    capex_pct: float = 0.05
    insurance_annual_per_unit: float = 1400.0
    property_tax_rate: float = 0.015  # used when the listing has no tax figure
    utilities_monthly_per_unit: float = 0.0

    # Rent basis: "market", "section8", or "best" (whichever is higher per unit)
    rent_basis: str = "best"
    section8_payment_standard_pct: float = 1.0  # PHA payment standard as % of FMR (0.9-1.1 typical)

    # Targets used for max offer and scoring
    target_cap_rate: float = 0.09
    target_cash_on_cash: float = 0.12
    min_dscr: float = 1.25
    rehab_contingency_pct: float = 0.15


@dataclass
class Settings:
    db_path: str = "data/dealfinder.db"
    markets: list[str] = field(default_factory=list)   # zip codes to track
    max_price: float = 0.0                              # 0 = no limit
    property_types: list[str] = field(default_factory=list)
    underwriting: Underwriting = field(default_factory=Underwriting)
    use_llm_reader: bool = False
    llm_model: str = "claude-opus-5"
    sources: dict = field(default_factory=dict)         # per-source options

    # Secrets from the environment
    @property
    def rentcast_api_key(self) -> str:
        return os.environ.get("RENTCAST_API_KEY", "")

    @property
    def hud_api_token(self) -> str:
        return os.environ.get("HUD_API_TOKEN", "")

    @property
    def reso_token(self) -> str:
        return os.environ.get("RESO_ACCESS_TOKEN", "")

    @property
    def anthropic_api_key(self) -> str:
        return os.environ.get("ANTHROPIC_API_KEY", "")


def _apply(dc, values: dict):
    known = {f.name for f in fields(dc)}
    for k, v in values.items():
        if k in known:
            setattr(dc, k, v)
    return dc


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    path = Path(path or os.environ.get("DEALFINDER_CONFIG", "config.toml"))
    settings = Settings()
    if not path.exists():
        return settings
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    uw = raw.pop("underwriting", {})
    _apply(settings, raw)
    _apply(settings.underwriting, uw)
    return settings
