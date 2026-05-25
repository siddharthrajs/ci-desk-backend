"""
EIA Open Data API v2 service.

All public methods return data sorted newest-first with week-over-week deltas added.
Series-filter constants are defined at module level — verify or explore codes at:
  https://www.eia.gov/opendata/browser/petroleum
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.cache import get_cache

logger = logging.getLogger(__name__)

# =============================================================================
# EIA v2 API route paths
# =============================================================================
_ROUTE_STOCKS = "petroleum/stoc/wstk"
_ROUTE_PRODUCTION = "petroleum/sum/sndw"
_ROUTE_REFINERY = "petroleum/pnp/wiup"
_ROUTE_PRODUCT_SUPPLIED = "petroleum/cons/wpsup"
_ROUTE_SPOT_PRICES = "petroleum/pri/spt"

# =============================================================================
# Series filter constants — EIA v2 facet parameters
#
# Each constant is a dict of {facet_name: [facet_value, ...]}.
# The helper _fetch_eia_series() encodes these as EIA bracket-notation query params:
#   {"product": ["EPC0"]} → facets[product][]=EPC0
# =============================================================================

# ---------------------------------------------------------------------------
# petroleum/stoc/wstk — Weekly Petroleum Stocks (Thousand Barrels)
#   product codes: EPC0=crude oil,  EPM0=motor gasoline,
#                  EPD0=distillate, EPCO=SPR crude
#   area    codes: NUS=U.S. total,  Y35NY=Cushing OK
#   process code:  SAX=ending stocks
# ---------------------------------------------------------------------------

# U.S. commercial crude ending stocks, excluding the SPR.
# Released each Wednesday in the Weekly Petroleum Status Report (WPSR).
CRUDE_STOCKS: dict[str, list[str]] = {
    "product":  ["EPC0"],
    "duoarea":  ["NUS"],
    "process":  ["SAX"],
}

# Crude stocks at Cushing, Oklahoma — WTI futures physical delivery hub.
# Cushing levels drive the near-month WTI basis and calendar-spread structure.
CUSHING_STOCKS: dict[str, list[str]] = {
    "product":  ["EPC0"],
    "duoarea":  ["Y35NY"],
    "process":  ["SAX"],
}

# U.S. motor gasoline ending stocks (finished product + blending components, all grades).
GASOLINE_STOCKS: dict[str, list[str]] = {
    "product":  ["EPM0"],
    "duoarea":  ["NUS"],
    "process":  ["SAX"],
}

# U.S. distillate fuel oil (diesel + heating oil combined).
# Low distillate stocks amplify heating-oil and diesel crack spreads.
DISTILLATE_STOCKS: dict[str, list[str]] = {
    "product":  ["EPD0"],
    "duoarea":  ["NUS"],
    "process":  ["SAX"],
}

# U.S. Strategic Petroleum Reserve crude oil level.
# SPR drawdowns add short-term supply; fills remove it.
SPR_LEVEL: dict[str, list[str]] = {
    "product":  ["EPCO"],
    "duoarea":  ["NUS"],
    "process":  ["SAX"],
}

# ---------------------------------------------------------------------------
# petroleum/sum/sndw — Weekly Petroleum Summary (includes crude production)
#   duoarea code: NUS=U.S. total
#   process code: FPF=field production of crude oil
# ---------------------------------------------------------------------------

# Domestic crude output — primary tracker of shale growth vs. OPEC production cuts.
CRUDE_PRODUCTION: dict[str, list[str]] = {
    "duoarea": ["NUS"],
    "process": ["FPF"],
}

# ---------------------------------------------------------------------------
# petroleum/pnp/wiup — Weekly Refinery Inputs & Utilization
#   duoarea codes: R10-R50=PADD 1-5 (NUS not available for weekly)
#   process code: YUP=% utilization of operable capacity
# ---------------------------------------------------------------------------

# Area code → label mapping for refinery utilization results.
# PADD 3 (Gulf Coast, ~55% of U.S. capacity) is the most closely watched district.
# NUS (national total) is not available in the weekly wiup dataset.
REFINERY_AREAS: dict[str, str] = {
    "R10": "padd1",   # East Coast
    "R20": "padd2",   # Midwest
    "R30": "padd3",   # Gulf Coast (dominant refining hub)
    "R40": "padd4",   # Rocky Mountain
    "R50": "padd5",   # West Coast (incl. Alaska)
}

REFINERY_UTILIZATION: dict[str, list[str]] = {
    "duoarea": list(REFINERY_AREAS.keys()),
    "process": ["YUP"],
}

# ---------------------------------------------------------------------------
# petroleum/cons/wpsup — Product Supplied, weekly (Thousand Barrels/Day)
#   product codes: EPM0F=finished motor gasoline, EPD0=distillate, EPJK=kerosene-jet
#   process code:  VPP=product supplied
#
# Product supplied is the EIA's demand proxy — barrels pushed into distribution.
# 4-week averages smooth single-week weather and holiday distortions.
# ---------------------------------------------------------------------------

# Product code → label mapping for product-supplied results.
PRODUCT_SUPPLIED_PRODUCTS: dict[str, str] = {
    "EPM0F": "gasoline",   # finished motor gasoline
    "EPD0":  "distillate", # distillate fuel oil (diesel + heating oil)
    "EPJK":  "jet",        # kerosene-type jet fuel
}

PRODUCT_SUPPLIED: dict[str, list[str]] = {
    "product": list(PRODUCT_SUPPLIED_PRODUCTS.keys()),
    "duoarea": ["NUS"],
    "process": ["VPP"],
}

# ---------------------------------------------------------------------------
# petroleum/pri/spt — Spot Prices (daily)
#   product codes: EPCWTI=WTI crude ($/bbl),
#                  EPMRB=RBOB gasoline ($/gal),
#                  EPD2F=No. 2 heating oil ($/gal)
#
# Used to compute crack spreads, e.g.:
#   3-2-1 crack = (2×RBOB×42 + 1×HO×42 − 3×WTI) / 3  [converts gal→bbl]
# ---------------------------------------------------------------------------

# Product code → label mapping for spot-price results.
SPOT_PRODUCTS: dict[str, str] = {
    "EPCWTI": "wti",          # WTI crude spot (Cushing, OK) — $/barrel
    "EPMRB":  "rbob",         # RBOB gasoline spot (NY Harbor) — $/gallon
    "EPD2F":  "heating_oil",  # No. 2 heating oil spot (NY Harbor) — $/gallon
}

SPOT_PRICES: dict[str, list[str]] = {
    "product": list(SPOT_PRODUCTS.keys()),
}


class EIAService:
    """EIA Open Data API v2 client. Inject via FastAPI Depends."""

    BASE_URL = "https://api.eia.gov/v2"

    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        self._client = client
        self._api_key = api_key

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    async def _fetch_eia_series(
        self,
        route: str,
        facets: dict[str, list[str]],
        frequency: str = "weekly",
        length: int = 52,
    ) -> list[dict[str, Any]]:
        """GET /{route}/data/ with the given facets; return raw data rows from the response.

        EIA v2 query params use bracket notation for facets and data columns:
          facets[product][]=EPC0  →  params["facets[product][]"] = ["EPC0"]
        httpx serialises list values as repeated params automatically.
        """
        params: dict[str, Any] = {
            "api_key":            self._api_key,
            "frequency":          frequency,
            "data[0]":            "value",
            "sort[0][column]":    "period",
            "sort[0][direction]": "desc",
            "offset":             0,
            "length":             length,
        }
        for key, values in facets.items():
            params[f"facets[{key}][]"] = values

        url = f"{self.BASE_URL}/{route}/data/"
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()["response"]["data"]

    @staticmethod
    def _parse_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert raw EIA data rows into clean dicts with WoW change metrics.

        EIA returns `value` as a string — cast to float; rows with non-numeric
        values (e.g. "NA", None) are silently dropped.
        Rows are expected newest-first; WoW delta is row[i] vs row[i+1].
        """
        parsed: list[dict[str, Any]] = []
        for row in rows:
            try:
                value = float(row["value"])
            except (TypeError, ValueError):
                continue
            parsed.append({"period": row["period"], "value": value})

        for i, item in enumerate(parsed):
            if i + 1 < len(parsed):
                prev = parsed[i + 1]["value"]
                delta = item["value"] - prev
                item["wow_change"] = round(delta, 3)
                item["wow_pct_change"] = round(delta / prev * 100, 2) if prev else None
            else:
                item["wow_change"] = None
                item["wow_pct_change"] = None

        return parsed

    @staticmethod
    def _parse_grouped(
        rows: list[dict[str, Any]],
        group_key: str,
        label_map: dict[str, str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Partition multi-series rows by group_key and apply _parse_series to each bucket."""
        buckets: dict[str, list[dict[str, Any]]] = {label: [] for label in label_map.values()}
        for row in rows:
            label = label_map.get(row.get(group_key, ""))
            if label is not None:
                buckets[label].append(row)
        return {
            label: EIAService._parse_series(bucket)
            for label, bucket in buckets.items()
        }

    # -------------------------------------------------------------------------
    # Petroleum stocks
    # -------------------------------------------------------------------------

    async def get_crude_stocks(self) -> list[dict[str, Any]]:
        """Weekly U.S. commercial crude oil ending stocks (Thousand Barrels)."""
        async def fetch() -> list[dict[str, Any]]:
            rows = await self._fetch_eia_series(_ROUTE_STOCKS, CRUDE_STOCKS)
            return self._parse_series(rows)

        return await get_cache().cache_or_fetch("eia:crude_stocks", fetch)

    async def get_cushing_stocks(self) -> list[dict[str, Any]]:
        """Weekly crude oil stocks at Cushing, OK (Thousand Barrels)."""
        async def fetch() -> list[dict[str, Any]]:
            rows = await self._fetch_eia_series(_ROUTE_STOCKS, CUSHING_STOCKS)
            return self._parse_series(rows)

        return await get_cache().cache_or_fetch("eia:cushing_stocks", fetch)

    async def get_gasoline_stocks(self) -> list[dict[str, Any]]:
        """Weekly U.S. motor gasoline ending stocks (Thousand Barrels)."""
        async def fetch() -> list[dict[str, Any]]:
            rows = await self._fetch_eia_series(_ROUTE_STOCKS, GASOLINE_STOCKS)
            return self._parse_series(rows)

        return await get_cache().cache_or_fetch("eia:gasoline_stocks", fetch)

    async def get_distillate_stocks(self) -> list[dict[str, Any]]:
        """Weekly U.S. distillate fuel oil ending stocks (Thousand Barrels)."""
        async def fetch() -> list[dict[str, Any]]:
            rows = await self._fetch_eia_series(_ROUTE_STOCKS, DISTILLATE_STOCKS)
            return self._parse_series(rows)

        return await get_cache().cache_or_fetch("eia:distillate_stocks", fetch)

    async def get_spr_level(self) -> list[dict[str, Any]]:
        """Weekly U.S. Strategic Petroleum Reserve level (Thousand Barrels)."""
        async def fetch() -> list[dict[str, Any]]:
            rows = await self._fetch_eia_series(_ROUTE_STOCKS, SPR_LEVEL)
            return self._parse_series(rows)

        return await get_cache().cache_or_fetch("eia:spr_level", fetch)

    # -------------------------------------------------------------------------
    # Production & refining
    # -------------------------------------------------------------------------

    async def get_crude_production(self) -> list[dict[str, Any]]:
        """Weekly U.S. crude oil field production (Thousand Barrels/Day)."""
        async def fetch() -> list[dict[str, Any]]:
            rows = await self._fetch_eia_series(_ROUTE_PRODUCTION, CRUDE_PRODUCTION)
            return self._parse_series(rows)

        return await get_cache().cache_or_fetch("eia:crude_production", fetch)

    async def get_refinery_utilization(self) -> dict[str, list[dict[str, Any]]]:
        """Weekly refinery utilization (%) for national + PADD 1-5."""
        async def fetch() -> dict[str, list[dict[str, Any]]]:
            rows = await self._fetch_eia_series(_ROUTE_REFINERY, REFINERY_UTILIZATION)
            return self._parse_grouped(rows, "duoarea", REFINERY_AREAS)

        return await get_cache().cache_or_fetch("eia:refinery_utilization", fetch)

    # -------------------------------------------------------------------------
    # Demand & prices
    # -------------------------------------------------------------------------

    async def get_product_supplied(self) -> dict[str, list[dict[str, Any]]]:
        """Weekly 4-week avg product supplied (Thousand Bbl/Day): gasoline, distillate, jet."""
        async def fetch() -> dict[str, list[dict[str, Any]]]:
            rows = await self._fetch_eia_series(_ROUTE_PRODUCT_SUPPLIED, PRODUCT_SUPPLIED)
            return self._parse_grouped(rows, "product", PRODUCT_SUPPLIED_PRODUCTS)

        return await get_cache().cache_or_fetch("eia:product_supplied", fetch)

    async def get_spot_prices(self) -> dict[str, list[dict[str, Any]]]:
        """Daily spot prices for WTI ($/bbl), RBOB ($/gal), and heating oil ($/gal)."""
        async def fetch() -> dict[str, list[dict[str, Any]]]:
            rows = await self._fetch_eia_series(
                _ROUTE_SPOT_PRICES,
                SPOT_PRICES,
                frequency="daily",
                length=90,
            )
            return self._parse_grouped(rows, "product", SPOT_PRODUCTS)

        return await get_cache().cache_or_fetch("eia:spot_prices", fetch)
