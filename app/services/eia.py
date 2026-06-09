"""
EIA Open Data API v2 service.

All public methods return data sorted newest-first with week-over-week deltas added.
Series-filter constants are defined at module level — verify or explore codes at:
  https://www.eia.gov/opendata/browser/petroleum
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.cache import get_cache
from app.core.http_client import request_with_retry

logger = logging.getLogger(__name__)

# =============================================================================
# EIA v2 API route paths
# =============================================================================
_ROUTE_STOCKS = "petroleum/stoc/wstk"
_ROUTE_REFINERY = "petroleum/pnp/wiup"
_ROUTE_PRODUCT_SUPPLIED = "petroleum/cons/wpsup"
_ROUTE_SPOT_PRICES = "petroleum/pri/spt"

# Upstream — US subtab
_ROUTE_WEEKLY_SUPPLY      = "petroleum/sum/sndw"   # weekly crude prod, net imports
_ROUTE_MONTHLY_PRODUCTION = "petroleum/crd/crpdn"  # monthly crude by state/PADD
_ROUTE_DRILL              = "petroleum/crd/drill"  # rotary / well-service rigs
_ROUTE_API_GRAVITY        = "petroleum/crd/api"    # production by API gravity
_ROUTE_WEEKLY_IMPORTS_CTY = "petroleum/move/wimpc" # weekly preliminary imports by country
_ROUTE_CRUDE_IMPORTS      = "crude-oil-imports"    # monthly final imports by country
_ROUTE_NG_PRODUCTION      = "natural-gas/prod/sum" # NG gross withdrawals + dry
_ROUTE_CRUDE_RESERVES     = "petroleum/crd/pres"   # crude proved reserves (annual)
_ROUTE_NG_RESERVES        = "natural-gas/enr/dry"  # dry NG proved reserves (annual)

# Midstream
_ROUTE_CRUDE_EXPCP        = "petroleum/move/expcp"  # monthly crude exports by PADD
_ROUTE_PADD_PIPE          = "petroleum/move/pipe"   # inter-PADD crude pipeline movements

# Upstream — OPEC+ subtab
_ROUTE_INTERNATIONAL      = "international"         # country-level production (monthly)
_ROUTE_STEO               = "steo"                 # Short-Term Energy Outlook (monthly + 18M forecast)

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
    "duoarea":  ["YCUOK"],
    "process":  ["SAX"],
}

# U.S. motor gasoline ending stocks (finished product + blending components, all grades).
GASOLINE_STOCKS: dict[str, list[str]] = {
    "product":  ["EPM0"],
    "duoarea":  ["NUS"],
    "process":  ["SAE"],
}

# U.S. distillate fuel oil (diesel + heating oil combined).
# Low distillate stocks amplify heating-oil and diesel crack spreads.
DISTILLATE_STOCKS: dict[str, list[str]] = {
    "product":  ["EPD0"],
    "duoarea":  ["NUS"],
    "process":  ["SAE"],
}

# U.S. Strategic Petroleum Reserve crude oil level.
# SPR drawdowns add short-term supply; fills remove it.
# Process SAS = "Ending Stocks SPR" (distinct from SAX = "Ending Stocks Excluding SPR").
SPR_LEVEL: dict[str, list[str]] = {
    "product":  ["EPC0"],
    "duoarea":  ["NUS"],
    "process":  ["SAS"],
}

# Jet fuel (kerosene-type) ending stocks — same route as crude/gasoline/distillate.
# Uses process SAE (ending stocks, petroleum products).
JET_FUEL_STOCKS: dict[str, list[str]] = {
    "product": ["EPJK"],
    "duoarea": ["NUS"],
    "process": ["SAE"],
}

# =============================================================================
# Midstream constants
# =============================================================================

# Weekly crude exports: lives on the weekly supply route (sndw) with process EEX.
# duoarea NUS-Z00 = U.S. total exports to all foreign destinations.
# Units: MBBL/D (same as weekly production series on this route).
CRUDE_EXPORTS_WEEKLY: dict[str, list[str]] = {
    "product":  ["EPC0"],
    "duoarea":  ["NUS-Z00"],
    "process":  ["EEX"],
}

# Monthly crude exports by PADD of origin (petroleum/move/expcp).
# duoarea format: "R1x-Z00" = PADD x exports to foreign destinations.
# Units: MBBL (monthly total — no per-day variant on this route).
CRUDE_EXPORTS_PADD_AREAS: list[str] = [
    "R10-Z00",  # PADD 1 (East Coast)
    "R20-Z00",  # PADD 2 (Midwest)
    "R30-Z00",  # PADD 3 (Gulf Coast)
    "R40-Z00",  # PADD 4 (Rocky Mountain)
    "R50-Z00",  # PADD 5 (West Coast)
]

# Inter-PADD crude pipeline movements (petroleum/move/pipe, process LMV).
# duoarea format: "DEST-SRC" → e.g., "R20-R30" = PADD 2 receipts FROM PADD 3.
# All 13 directional pairs that EIA publishes for crude oil.
PADD_PIPE_PAIRS: list[str] = [
    "R10-R20", "R10-R30",
    "R20-R10", "R20-R30", "R20-R40",
    "R30-R10", "R30-R20", "R30-R40", "R30-R50",
    "R40-R20", "R40-R30",
    "R50-R30", "R50-R40",
]
PADD_SHORT: dict[str, str] = {
    "R10": "padd1", "R20": "padd2", "R30": "padd3",
    "R40": "padd4", "R50": "padd5",
}
PADD_LABELS: dict[str, str] = {
    "R10": "PADD 1 East Coast",
    "R20": "PADD 2 Midwest",
    "R30": "PADD 3 Gulf Coast",
    "R40": "PADD 4 Rocky Mtn",
    "R50": "PADD 5 West Coast",
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

# Series IDs for petroleum/pri/spt — filter + group by the "series" field in
# EIA v2 responses rather than "product". Series IDs are unambiguous; product
# codes can match multiple geographic series (e.g. EPC0 = crude in all areas).
#
# EIA v2 strips the "PET." prefix and ".D" (daily) suffix from legacy IDs:
#   PET.RWTC.D                   → RWTC
#   PET.RBRTE.D                  → RBRTE
#   PET.EER_EPMRR_PF4_Y35NY_DPG.D → EER_EPMRR_PF4_Y35NY_DPG  (NY Harbor, not RGC)
#   PET.EER_EPD2F_PF4_Y35NY_DPG.D → EER_EPD2F_PF4_Y35NY_DPG  (NY Harbor, not RGC)
#
# With 4 series and 90 days of daily data, length=400 gives a buffer for
# weekends/holidays where some series may skip a day.
SPOT_SERIES_LABELS: dict[str, str] = {
    "RWTC":                     "wti",         # WTI Crude Spot (Cushing, OK) $/bbl
    "RBRTE":                    "brent",       # Europe Brent Spot FOB $/bbl
    "EER_EPMRR_PF4_Y05LA_DPG": "rbob",        # LA Reformulated RBOB Regular Gasoline $/gal
    "EER_EPD2F_PF4_Y05LA_DPG": "heating_oil", # No. 2 Heating Oil (Los Angeles) $/gal
}

SPOT_SERIES_FULL: dict[str, list[str]] = {
    "series": list(SPOT_SERIES_LABELS.keys()),
}

# =============================================================================
# Upstream — US subtab constants
# =============================================================================

# petroleum/crd/crpdn — region/state breakdown for the small-multiples panel.
# State codes (Sxx) coexist with PADD codes (Rxx) and the GOMS aggregate.
US_PRODUCTION_REGIONS: dict[str, str] = {
    "STX":  "texas",
    "SND":  "north_dakota",
    "SNM":  "new_mexico",
    "R20":  "padd2",
    "R30":  "padd3",
    "GOMS": "gulf_of_america",
}

# petroleum/crd/drill — rotary + well-service rigs (EIA's republished Baker
# Hughes data). Filtering by series ID avoids ambiguity from the overlapping
# (duoarea, product, process) triples.
US_RIG_SERIES: dict[str, str] = {
    "E_ERTRR0_XR0_NUS_C":   "total",
    "E_ERTRRO_XR0_NUS_C":   "oil",
    "E_ERTRRG_XR0_NUS_C":   "gas",
    "E_ERTRR0_XR0_RUSON_C": "onshore",
    "E_ERTRR0_XR0_RUSOF_C": "offshore",
}

# petroleum/crd/api — gravity buckets are published at the state level only;
# Lower-48 totals come from summing these states server-side. R98 = "Other
# States" aggregate. R3FM = Federal Offshore Gulf (intentionally excluded to
# avoid double counting once we add GoA elsewhere).
API_GRAVITY_STATES: list[str] = [
    "STX", "SND", "SNM", "SOK", "SCA", "SCO", "SWY", "SUT", "SMT",
    "SOH", "SLA", "SPA", "SAR", "SKS", "SWV", "R98",
]
API_GRAVITY_BUCKETS: dict[str, str] = {
    "EPC30L": "heavy",       # ≤30 API
    "EPC43B": "medium",      # 30.1–40 API
    "EPC54B": "light",       # 40.1–50 API
    "EPC50G": "condensate",  # 50.1+ API
}

# petroleum/move/wimpc — weekly preliminary imports use NUS-Nxx country codes.
# The response carries the ISO-3 code as `area-name`; we map ISO-3 → OPEC+ flag.
# OPEC: Algeria, Iran, Iraq, Kuwait, Libya, Nigeria, Saudi Arabia, UAE,
#       Venezuela, Equatorial Guinea, Gabon, Rep. of Congo. (Angola left 2024.)
# OPEC+ partners: Russia, Kazakhstan, Azerbaijan, Bahrain, Brunei, Malaysia,
#                 Mexico, Oman, South Sudan, Sudan.
OPEC_PLUS_ISO3: set[str] = {
    "DZA", "IRN", "IRQ", "KWT", "LBY", "NGA", "SAU", "ARE",
    "VEN", "GNQ", "GAB", "COG",
    "RUS", "KAZ", "AZE", "BHR", "BRN", "MYS", "MEX", "OMN", "SDN", "SSD",
}

# crude-oil-imports uses descriptive country names (not ISO-3) in the
# originName field. Cover the OPEC+ members that actually appear in US
# import data — countries that haven't shipped to the US for years are
# omitted to keep the set tight.
OPEC_PLUS_NAMES: set[str] = {
    "Algeria", "Iran", "Iraq", "Kuwait", "Libya", "Nigeria",
    "Saudi Arabia", "United Arab Emirates",
    "Venezuela", "Equatorial Guinea", "Gabon", "Republic Of Congo (Brazzaville)",
    "Russia", "Kazakhstan", "Azerbaijan", "Bahrain", "Brunei",
    "Malaysia", "Mexico", "Oman", "Sudan", "South Sudan",
}

# natural-gas/prod/sum — process codes for NG production aggregates.
#   FGW = Gross Withdrawals, FPD = Dry Production, FGS = Shale withdrawals.
NG_PROCESS_GROSS = "FGW"
NG_PROCESS_DRY   = "FPD"

# =============================================================================
# Upstream — OPEC+ subtab constants
# =============================================================================

# international — EIA v2 facets for production by country (Thousand Bbl/Day).
#   productId=57 = Crude oil + lease condensate — the OPEC+ quota basis ("crude")
#   productId=55 = Crude oil, NGPL, and other liquids — total liquids ("liquids")
#   activityId=1 = Production
# Verify / explore at: https://www.eia.gov/opendata/browser/international
_INTL_PRODUCT_CRUDE   = "57"
_INTL_PRODUCT_LIQUIDS = "55"
_INTL_ACTIVITY_ID     = "1"

# Subtab "basis" toggle → EIA international productId. Crude is the default
# because OPEC+ quotas/cuts are defined on crude oil + lease condensate.
_INTL_PRODUCT_BY_BASIS: dict[str, str] = {
    "crude":   _INTL_PRODUCT_CRUDE,
    "liquids": _INTL_PRODUCT_LIQUIDS,
}

# OPEC+ member ISO-3 codes → display names, ordered by typical production level.
# Core OPEC (12 members after Angola's 2024 exit) + non-OPEC+ partners.
OPEC_PLUS_PROD_MEMBERS: dict[str, str] = {
    "SAU": "Saudi Arabia",
    "RUS": "Russia",
    "IRQ": "Iraq",
    "ARE": "United Arab Emirates",
    "KWT": "Kuwait",
    "IRN": "Iran",
    "NGA": "Nigeria",
    "KAZ": "Kazakhstan",
    "LBY": "Libya",
    "DZA": "Algeria",
    "OMN": "Oman",
    "VEN": "Venezuela",
    "GAB": "Gabon",
    "AZE": "Azerbaijan",
    "COG": "Congo",
    "GNQ": "Equatorial Guinea",
    "MYS": "Malaysia",
    "SDN": "Sudan",
    "BRN": "Brunei",
    "SSD": "South Sudan",
    "BHR": "Bahrain",
}

# ---------------------------------------------------------------------------
# STEO series for the OPEC+ overview. STEO values are ALREADY mb/d (do not /1000).
# Overview is basis-independent — capacity/spare/splits are all crude (no liquids
# equivalent exists in STEO). All crude basis here.
# ---------------------------------------------------------------------------
_STEO_CAPACITY = "COPC_OPEC"          # OPEC total crude production capacity
_STEO_SPARE    = "COPS_OPEC"          # OPEC total spare crude capacity
_STEO_BALANCE  = "T3_STCHANGE_WORLD"  # World net inventory withdrawals (mb/d; <0 = build = surplus)

# Structural split (crude): OPEC total, OPEC+ other participants, non-OPEC+ ex-US.
_STEO_SPLIT_OPEC       = "COPR_OPEC"
_STEO_SPLIT_OPEC_OTHER = "COPR_OPECPLUS_OTHER"
_STEO_SPLIT_NON        = "COPR_NONOPECPLUS_XUS"

# Unplanned production disruptions (PADI_*, mb/d) — OPEC+ members / key disruptors
# that have a STEO series. STEO uses its own country codes (not ISO-3).
_STEO_PADI: dict[str, str] = {
    "PADI_RS": "Russia",
    "PADI_IR": "Iran",
    "PADI_LY": "Libya",
    "PADI_VE": "Venezuela",
    "PADI_NI": "Nigeria",
    "PADI_IZ": "Iraq",
    "PADI_KU": "Kuwait",
    "PADI_SA": "Saudi Arabia",
    "PADI_MX": "Mexico",
    "PADI_AJ": "Azerbaijan",
    "PADI_GB": "Gabon",
    "PADI_SU": "Sudan & S. Sudan",
}

# Hand-maintained OPEC+ required-production quotas (mb/d crude). Update after
# each monthly OPEC+ meeting. Loaded via a function so tests can patch it.
_QUOTA_PATH = Path(__file__).resolve().parent.parent / "data" / "opec_plus_quotas.json"


def _load_opec_quotas() -> dict[str, Any]:
    """Load OPEC+ required-production quotas from app/data/opec_plus_quotas.json."""
    try:
        with open(_QUOTA_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        logger.warning("OPEC quota file missing/invalid: %s", _QUOTA_PATH)
        return {"as_of": None, "source": None, "required_mbd": {}}


# JODI-Oil free annual CSVs. Only these 5 OPEC members report crude production to
# JODI (Iraq, UAE, Iran, Libya, Congo, Gabon, Eq. Guinea don't), so the cross-check
# compares EIA vs JODI on this same set — apples to apples. JODI ISO-2 → EIA ISO-3.
_JODI_BASE = "https://www.jodidata.org/_resources/files/downloads/oil-data/annual-csv/primary"
_JODI_OPEC: dict[str, str] = {
    "SA": "SAU",
    "KW": "KWT",
    "NG": "NGA",
    "DZ": "DZA",
    "VE": "VEN",
}


def _jodi_csv_urls() -> list[str]:
    """Recent JODI primary CSVs: current year (primaryyear{Y}.csv) + prior ({Y-1}.csv)."""
    y = datetime.now(timezone.utc).year
    return [f"{_JODI_BASE}/primaryyear{y}.csv", f"{_JODI_BASE}/{y - 1}.csv"]

# Legacy product-code constants kept for the existing /api/downstream endpoint.
SPOT_PRODUCTS_FULL: dict[str, str] = {
    "EPC0":     "wti",
    "EPCBRENT": "brent",
    "EPMRB":    "rbob",
    "EPD2F":    "heating_oil",
}

SPOT_PRICES_FULL: dict[str, list[str]] = {
    "product": list(SPOT_PRODUCTS_FULL.keys()),
}

# Product supplied including total petroleum for the v2 demand endpoint.
PRODUCT_SUPPLIED_PRODUCTS_V2: dict[str, str] = {
    "EPM0F": "gasoline",   # finished motor gasoline
    "EPD0":  "distillate", # distillate fuel oil
    "EPJK":  "jet",        # kerosene-type jet fuel
    "EP":    "total",      # total petroleum products
}

PRODUCT_SUPPLIED_V2: dict[str, list[str]] = {
    "product": list(PRODUCT_SUPPLIED_PRODUCTS_V2.keys()),
    "duoarea": ["NUS"],
    "process": ["VPP"],
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
        data_columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """GET /{route}/data/ with the given facets; return raw data rows from the response.

        EIA v2 query params use bracket notation for facets and data columns:
          facets[product][]=EPC0  →  params["facets[product][]"] = ["EPC0"]
        httpx serialises list values as repeated params automatically.

        data_columns: which EIA data fields to request (default: ["value"]).
                      Override to ["quantity"] for crude-oil-imports.
        """
        columns = data_columns or ["value"]
        params: dict[str, Any] = {
            "api_key":            self._api_key,
            "frequency":          frequency,
            "sort[0][column]":    "period",
            "sort[0][direction]": "desc",
            "offset":             0,
            "length":             length,
        }
        for i, col in enumerate(columns):
            params[f"data[{i}]"] = col
        for key, values in facets.items():
            params[f"facets[{key}][]"] = values

        # EIA occasionally takes 20-40s to respond on heavy queries (multi-state
        # aggregations, 5Y weekly histories). Use the shared retry-equipped
        # client + extend read timeout to 60s for EIA only — the shared 30s
        # default is fine for FRED/CFTC but tight for EIA's bigger payloads.
        url = f"{self.BASE_URL}/{route}/data/"
        response = await request_with_retry(
            "GET", url,
            params=params,
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
        )
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
    # Refining (shared across midstream + downstream)
    # -------------------------------------------------------------------------

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

    async def get_spot_prices_full(self) -> dict[str, list[dict[str, Any]]]:
        """Daily spot prices for WTI, Brent ($/bbl), RBOB and heating oil ($/gal) — ~90 days.

        Makes 4 parallel requests, one per series, so each gets exactly 90 rows.
        This avoids all product-code and series-filter guesswork: each URL fetches
        one well-known EIA series ID directly.
        """
        async def fetch() -> dict[str, list[dict[str, Any]]]:
            # Request 100 rows each to cover ~90 trading days (accounting for holidays).
            wti_rows, brent_rows, rbob_rows, ho_rows = await asyncio.gather(
                self._fetch_eia_series(_ROUTE_SPOT_PRICES, {"series": ["RWTC"]},                   frequency="daily", length=100),
                self._fetch_eia_series(_ROUTE_SPOT_PRICES, {"series": ["RBRTE"]},                  frequency="daily", length=100),
                self._fetch_eia_series(_ROUTE_SPOT_PRICES, {"series": ["EER_EPMRR_PF4_Y05LA_DPG"]}, frequency="daily", length=100),
                self._fetch_eia_series(_ROUTE_SPOT_PRICES, {"series": ["EER_EPD2F_PF4_Y05LA_DPG"]}, frequency="daily", length=100),
            )
            return {
                "wti":         self._parse_series(wti_rows),
                "brent":       self._parse_series(brent_rows),
                "rbob":        self._parse_series(rbob_rows),
                "heating_oil": self._parse_series(ho_rows),
            }

        # Cache key v6 — busts v5 which used Y35NY (NY Harbor); switched to Y05LA (Los Angeles).
        return await get_cache().cache_or_fetch("eia:spot_prices_full_v6", fetch, ttl=300)

    async def get_refinery_utilization_2yr(self) -> dict[str, list[dict[str, Any]]]:
        """Weekly refinery utilization (%) for PADD 1-5 — 104 weeks (2Y) history.

        length=520 = 5 PADDs × 104 weeks.
        """
        async def fetch() -> dict[str, list[dict[str, Any]]]:
            rows = await self._fetch_eia_series(_ROUTE_REFINERY, REFINERY_UTILIZATION, length=520)
            return self._parse_grouped(rows, "duoarea", REFINERY_AREAS)

        return await get_cache().cache_or_fetch("eia:refinery_util_2yr_v2", fetch, ttl=3600)

    async def get_product_supplied_full(self) -> dict[str, list[dict[str, Any]]]:
        """Weekly product supplied (KBPD) for gasoline, distillate, jet, total — 104 weeks.

        length=416 = 4 products × 104 weeks.
        """
        async def fetch() -> dict[str, list[dict[str, Any]]]:
            rows = await self._fetch_eia_series(
                _ROUTE_PRODUCT_SUPPLIED, PRODUCT_SUPPLIED_V2, length=416,
            )
            return self._parse_grouped(rows, "product", PRODUCT_SUPPLIED_PRODUCTS_V2)

        return await get_cache().cache_or_fetch("eia:product_supplied_full_v2", fetch, ttl=3600)

    # =========================================================================
    # Upstream — US subtab
    # =========================================================================

    async def get_us_crude_production(self) -> dict[str, Any]:
        """Weekly US + L48 crude production, weekly net imports, and 3Y monthly
        crude production. Single bundle for the primary chart + hero cards 1/3/4.

        Units: all values returned in MBD (input is kbpd from EIA, /1000).
        """
        async def fetch() -> dict[str, Any]:
            # 5Y of weekly data = ~260 weeks. Request 280 for safety buffer.
            # Each sub-call is independent; if EIA is having a partial outage
            # (one route slow, others fine) we degrade gracefully — return what
            # came back, null the rest — rather than failing the whole panel.
            us_weekly, l48_weekly, net_imports, monthly_rows_raw = await asyncio.gather(
                self._fetch_eia_series(
                    _ROUTE_WEEKLY_SUPPLY,
                    {"duoarea": ["NUS"], "process": ["FPF"]},
                    frequency="weekly", length=280,
                ),
                self._fetch_eia_series(
                    _ROUTE_WEEKLY_SUPPLY,
                    {"duoarea": ["R48"], "process": ["FPF"]},
                    frequency="weekly", length=280,
                ),
                self._fetch_eia_series(
                    # Net-imports series lives on duoarea NUS-Z00 (not NUS).
                    _ROUTE_WEEKLY_SUPPLY,
                    {"duoarea": ["NUS-Z00"], "process": ["IMN"], "product": ["EPC0"]},
                    frequency="weekly", length=280,
                ),
                self._fetch_eia_series(
                    _ROUTE_MONTHLY_PRODUCTION,
                    {"duoarea": ["NUS"], "product": ["EPC0"], "process": ["FPF"]},
                    frequency="monthly", length=80,
                ),
                return_exceptions=True,
            )

            # Treat each Exception as "no data" — log + null out that series.
            def _safe(result: Any, label: str) -> list[dict[str, Any]]:
                if isinstance(result, Exception):
                    logger.warning("eia.us_crude_production: %s failed: %s", label, result)
                    return []
                return result  # type: ignore[no-any-return]

            us_weekly    = _safe(us_weekly,         "us_weekly")
            l48_weekly   = _safe(l48_weekly,        "l48_weekly")
            net_imports  = _safe(net_imports,       "net_imports")
            monthly_rows_raw = _safe(monthly_rows_raw, "monthly")

            # crpdn publishes both MBBL (monthly total) and MBBL/D rows — we
            # only want per-day rates. No L48 aggregate exists at this route,
            # so monthly history is NUS-only (L48 is available weekly via sndw).
            monthly_rows = [r for r in monthly_rows_raw if r.get("units") == "MBBL/D"]

            us = self._parse_series(us_weekly)
            l48 = self._parse_series(l48_weekly)
            ni = self._parse_series(net_imports)

            def _yoy_pct(rows: list[dict[str, Any]]) -> float | None:
                """Compare row[0] vs row[~52] (1 year ago)."""
                if len(rows) < 53 or rows[52]["value"] == 0:
                    return None
                return round((rows[0]["value"] - rows[52]["value"]) / rows[52]["value"] * 100, 2)

            us_latest  = us[0]  if us  else None
            l48_latest = l48[0] if l48 else None
            ni_latest  = ni[0]  if ni  else None

            weekly_history = [
                {"date": r["period"], "value": round(r["value"] / 1000, 3)}
                for r in us
            ]

            monthly_by_period: dict[str, float] = {}
            for row in monthly_rows:
                period = row.get("period", "")
                if not period or row.get("duoarea") != "NUS":
                    continue
                try:
                    monthly_by_period[period] = float(row["value"])
                except (TypeError, ValueError):
                    continue

            monthly_history = [
                {
                    "date":     f"{p}-01",
                    "us_total": round(monthly_by_period[p] / 1000, 3),
                    "l48":      None,  # not published at this route — use weekly_l48 instead
                }
                for p in sorted(monthly_by_period.keys(), reverse=True)
            ]

            return {
                "weekly_us_mbd":          round(us_latest["value"] / 1000, 3)              if us_latest else None,
                "weekly_us_wow":          round(us_latest["wow_change"] / 1000, 3)         if us_latest and us_latest.get("wow_change") is not None else None,
                "weekly_us_yoy":          _yoy_pct(us),
                "weekly_l48_mbd":         round(l48_latest["value"] / 1000, 3)             if l48_latest else None,
                "weekly_l48_wow":         round(l48_latest["wow_change"] / 1000, 3)        if l48_latest and l48_latest.get("wow_change") is not None else None,
                "weekly_net_imports_mbd": round(ni_latest["value"] / 1000, 3)              if ni_latest else None,
                "weekly_net_imports_wow": round(ni_latest["wow_change"] / 1000, 3)         if ni_latest and ni_latest.get("wow_change") is not None else None,
                "weekly_history":  weekly_history,
                "monthly_history": monthly_history,
            }

        return await get_cache().cache_or_fetch("eia:us_crude_production_v1", fetch, ttl=3600)

    async def get_us_rig_count(self) -> dict[str, Any]:
        """Monthly EIA-republished rig counts: total, oil, gas, onshore, offshore.

        One request filtered by the five series IDs in US_RIG_SERIES.
        """
        async def fetch() -> dict[str, Any]:
            # 5 series × 60 months = 300 rows.
            rows = await self._fetch_eia_series(
                _ROUTE_DRILL,
                {"series": list(US_RIG_SERIES.keys())},
                frequency="monthly", length=300,
            )

            by_period: dict[str, dict[str, int]] = {}
            for row in rows:
                period = row.get("period", "")
                label  = US_RIG_SERIES.get(row.get("series", ""))
                if not period or not label:
                    continue
                try:
                    val = int(float(row["value"]))
                except (TypeError, ValueError):
                    continue
                by_period.setdefault(period, {})[label] = val

            history = [
                {
                    "date":     f"{p}-01",
                    "total":    by_period[p].get("total"),
                    "oil":      by_period[p].get("oil"),
                    "gas":      by_period[p].get("gas"),
                    "onshore":  by_period[p].get("onshore"),
                    "offshore": by_period[p].get("offshore"),
                }
                for p in sorted(by_period.keys(), reverse=True)
            ]

            latest = history[0] if history else {}
            prev   = history[1] if len(history) > 1 else {}
            yr_ago = history[12] if len(history) > 12 else {}

            def _diff(a: int | None, b: int | None) -> int | None:
                return (a - b) if (a is not None and b is not None) else None

            return {
                "latest_total": latest.get("total"),
                "latest_oil":   latest.get("oil"),
                "latest_gas":   latest.get("gas"),
                "mom_change":   _diff(latest.get("total"), prev.get("total")),
                "yoy_change":   _diff(latest.get("total"), yr_ago.get("total")),
                "history":      history,
            }

        return await get_cache().cache_or_fetch("eia:us_rig_count_v1", fetch, ttl=21600)

    async def get_us_production_by_region(self) -> dict[str, Any]:
        """Monthly crude production by state/PADD/region — 36 months for small multiples."""
        async def fetch() -> dict[str, Any]:
            # 6 regions × 2 unit rows × 36 months ≈ 430 rows; request 500.
            # crpdn returns both MBBL (monthly total) and MBBL/D rows — filter
            # to the per-day rate so we don't accidentally store volume totals.
            rows_raw = await self._fetch_eia_series(
                _ROUTE_MONTHLY_PRODUCTION,
                {
                    "duoarea": list(US_PRODUCTION_REGIONS.keys()),
                    "product": ["EPC0"],
                    "process": ["FPF"],
                },
                frequency="monthly", length=500,
            )
            rows = [r for r in rows_raw if r.get("units") == "MBBL/D"]

            by_period: dict[str, dict[str, float]] = {}
            for row in rows:
                period = row.get("period", "")
                label  = US_PRODUCTION_REGIONS.get(row.get("duoarea", ""))
                if not period or not label:
                    continue
                try:
                    val = float(row["value"])
                except (TypeError, ValueError):
                    continue
                by_period.setdefault(period, {})[label] = round(val / 1000, 3)

            sorted_periods = sorted(by_period.keys(), reverse=True)
            history = [
                {"date": f"{p}-01", **{lbl: by_period[p].get(lbl) for lbl in US_PRODUCTION_REGIONS.values()}}
                for p in sorted_periods
            ]

            regions: dict[str, dict[str, float | None]] = {}
            for label in US_PRODUCTION_REGIONS.values():
                series = [by_period[p].get(label) for p in sorted_periods]
                series = [v for v in series if v is not None]
                current = series[0]  if series             else None
                prev    = series[1]  if len(series) > 1   else None
                yr_ago  = series[12] if len(series) > 12  else None
                regions[label] = {
                    "current":    current,
                    "mom_change": round(current - prev,   3) if (current is not None and prev    is not None) else None,
                    "yoy_change": round(current - yr_ago, 3) if (current is not None and yr_ago  is not None) else None,
                }

            return {"regions": regions, "history": history}

        return await get_cache().cache_or_fetch("eia:us_prod_by_region_v1", fetch, ttl=21600)

    async def get_us_api_gravity(self) -> dict[str, Any]:
        """Lower-48 crude production by API gravity bucket — summed across states, monthly.

        EIA publishes gravity-bucket data at state level only; we aggregate 15
        states + the R98 "Other" group server-side to get an L48 view. The
        endpoint times out on the full state list, so we split the query in
        two halves and merge.
        """
        async def fetch() -> dict[str, Any]:
            half = len(API_GRAVITY_STATES) // 2
            states_a, states_b = API_GRAVITY_STATES[:half], API_GRAVITY_STATES[half:]
            results = await asyncio.gather(
                self._fetch_eia_series(
                    _ROUTE_API_GRAVITY,
                    {"duoarea": states_a, "product": list(API_GRAVITY_BUCKETS.keys())},
                    frequency="monthly", length=2000,
                ),
                self._fetch_eia_series(
                    _ROUTE_API_GRAVITY,
                    {"duoarea": states_b, "product": list(API_GRAVITY_BUCKETS.keys())},
                    frequency="monthly", length=2000,
                ),
                return_exceptions=True,
            )
            rows: list[dict[str, Any]] = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.warning("eia.us_api_gravity: half %d failed: %s", i, r)
                else:
                    rows.extend(r)  # type: ignore[arg-type]

            # Sum per (period, bucket) across states.
            by_period_bucket: dict[str, dict[str, float]] = {}
            for row in rows:
                period = row.get("period", "")
                bucket = API_GRAVITY_BUCKETS.get(row.get("product", ""))
                if not period or not bucket:
                    continue
                try:
                    val = float(row["value"])
                except (TypeError, ValueError):
                    continue
                by_period_bucket.setdefault(period, {}).setdefault(bucket, 0.0)
                by_period_bucket[period][bucket] += val

            sorted_periods = sorted(by_period_bucket.keys(), reverse=True)
            # Only keep periods where at least one bucket reported (avoids
            # leading months that haven't been published yet).
            sorted_periods = [p for p in sorted_periods if by_period_bucket[p]]
            history = [
                {
                    "date":       f"{p}-01",
                    "heavy":      round(by_period_bucket[p].get("heavy",      0.0) / 1000, 3) or None,
                    "medium":     round(by_period_bucket[p].get("medium",     0.0) / 1000, 3) or None,
                    "light":      round(by_period_bucket[p].get("light",      0.0) / 1000, 3) or None,
                    "condensate": round(by_period_bucket[p].get("condensate", 0.0) / 1000, 3) or None,
                }
                for p in sorted_periods[:36]
            ]

            latest = history[0] if history else {}
            total  = sum(v for v in (latest.get("heavy"), latest.get("medium"), latest.get("light"), latest.get("condensate")) if v) or None

            def _pct(bucket: str) -> float | None:
                v = latest.get(bucket)
                if not v or not total:
                    return None
                return round(v / total * 100, 1)

            return {
                "latest_heavy_pct":      _pct("heavy"),
                "latest_medium_pct":     _pct("medium"),
                "latest_light_pct":      _pct("light"),
                "latest_condensate_pct": _pct("condensate"),
                "history": history,
            }

        return await get_cache().cache_or_fetch("eia:us_api_gravity_v1", fetch, ttl=86400)

    async def get_us_crude_imports(self) -> dict[str, Any]:
        """Crude imports by country — weekly preliminary + monthly final, both bundled."""
        async def fetch() -> dict[str, Any]:
            # Monthly feed: constrain to destinationType=US so we get the
            # already-aggregated US-total row per origin (still split by grade,
            # which we sum in _build_monthly_imports). If either feed errors,
            # the other still renders.
            weekly_rows, monthly_rows = await asyncio.gather(
                self._fetch_eia_series(
                    _ROUTE_WEEKLY_IMPORTS_CTY,
                    {"product": ["EPC0"], "process": ["IM0"]},
                    frequency="weekly", length=400,
                ),
                self._fetch_eia_series(
                    _ROUTE_CRUDE_IMPORTS,
                    {"originType": ["CTY"], "destinationType": ["US"]},
                    frequency="monthly", length=2000,
                    data_columns=["quantity"],
                ),
                return_exceptions=True,
            )
            if isinstance(weekly_rows, Exception):
                logger.warning("eia.us_crude_imports: weekly failed: %s", weekly_rows)
                weekly_rows = []
            if isinstance(monthly_rows, Exception):
                logger.warning("eia.us_crude_imports: monthly failed: %s", monthly_rows)
                monthly_rows = []
            return {
                "weekly_preliminary": self._build_weekly_imports(weekly_rows),
                "monthly_final":      self._build_monthly_imports(monthly_rows),
            }

        return await get_cache().cache_or_fetch("eia:us_crude_imports_v1", fetch, ttl=3600)

    @staticmethod
    def _build_weekly_imports(rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Aggregate wimpc rows into latest top-10 + 26-week history total."""
        # Group by (period, country_iso) where country_iso is the area-name field.
        by_period: dict[str, dict[str, float]] = {}
        for row in rows:
            period = row.get("period", "")
            iso    = row.get("area-name") or row.get("duoarea-name") or ""
            if not period or not iso:
                continue
            try:
                val = float(row.get("value") or 0)
            except (TypeError, ValueError):
                continue
            by_period.setdefault(period, {})
            by_period[period][iso] = by_period[period].get(iso, 0.0) + val

        sorted_periods = sorted(by_period.keys(), reverse=True)
        if not sorted_periods:
            return {"total_mbd": None, "top_origins": [], "history": []}

        latest = by_period[sorted_periods[0]]
        prev   = by_period.get(sorted_periods[1], {}) if len(sorted_periods) > 1 else {}
        total  = sum(latest.values())

        top = sorted(latest.items(), key=lambda x: x[1], reverse=True)[:10]
        top_origins = [
            {
                "country":      iso,
                "volume_mbd":   round(vol / 1000, 3),
                "share_pct":    round(vol / total * 100, 1) if total else 0.0,
                "mom_change":   round((vol - prev.get(iso, 0.0)) / 1000, 3) if prev else None,
                "is_opec_plus": iso in OPEC_PLUS_ISO3,
            }
            for iso, vol in top
        ]
        history = [
            {"date": p, "value": round(sum(by_period[p].values()) / 1000, 3)}
            for p in sorted_periods[:26]
        ]
        return {
            "total_mbd":   round(total / 1000, 3),
            "top_origins": top_origins,
            "history":     history,
        }

    @staticmethod
    def _build_monthly_imports(rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Aggregate crude-oil-imports rows (CTY-typed) into top-10 + 12-month history.

        crude-oil-imports returns `quantity` in thousand barrels per month, not
        per day. Convert to MBD with a 30.5-day reference month.
        """
        _DAYS = 30.5

        by_period: dict[str, dict[str, float]] = {}
        for row in rows:
            period = row.get("period", "")
            origin = row.get("originName") or row.get("originId") or ""
            if not period or not origin:
                continue
            try:
                qty = float(row.get("quantity") or 0)
            except (TypeError, ValueError):
                continue
            by_period.setdefault(period, {})
            by_period[period][origin] = by_period[period].get(origin, 0.0) + qty

        sorted_periods = sorted(by_period.keys(), reverse=True)
        if not sorted_periods:
            return {"total_mbd": None, "top_origins": [], "history": []}

        latest = by_period[sorted_periods[0]]
        prev   = by_period.get(sorted_periods[1], {}) if len(sorted_periods) > 1 else {}
        total_kbbl = sum(latest.values())
        total_mbd  = total_kbbl / _DAYS / 1000

        top = sorted(latest.items(), key=lambda x: x[1], reverse=True)[:10]
        top_origins = [
            {
                "country":      country,
                "volume_mbd":   round(vol / _DAYS / 1000, 3),
                "share_pct":    round(vol / total_kbbl * 100, 1) if total_kbbl else 0.0,
                "mom_change":   round((vol - prev.get(country, 0.0)) / _DAYS / 1000, 3) if prev else None,
                "is_opec_plus": country in OPEC_PLUS_NAMES,
            }
            for country, vol in top
        ]
        history = [
            {"date": f"{p}-01", "value": round(sum(by_period[p].values()) / _DAYS / 1000, 3)}
            for p in sorted_periods[:12]
        ]
        return {
            "total_mbd":   round(total_mbd, 3),
            "top_origins": top_origins,
            "history":     history,
        }

    async def get_us_natural_gas(self) -> dict[str, Any]:
        """Monthly US natural gas — gross withdrawals + dry production, 5Y history.

        Unit handling is asymmetric on this route:
          * FGW (gross withdrawals) is published in both MMCF (monthly total)
            and MMCF/D (per-day rate) — use the MMCF/D row.
          * FPD (dry production) is published in MMCF only — convert with a
            30.5-day reference month.
        Both flow into Bcf/d (divide MMcf/d by 1000).
        """
        _DAYS = 30.5

        async def fetch() -> dict[str, Any]:
            rows = await self._fetch_eia_series(
                _ROUTE_NG_PRODUCTION,
                {"duoarea": ["NUS"], "process": [NG_PROCESS_GROSS, NG_PROCESS_DRY]},
                frequency="monthly", length=300,
            )

            by_period: dict[str, dict[str, float]] = {}
            for row in rows:
                period = row.get("period", "")
                proc   = row.get("process", "")
                units  = row.get("units", "")
                if not period:
                    continue
                try:
                    val = float(row["value"])
                except (TypeError, ValueError):
                    continue
                if proc == NG_PROCESS_GROSS and units == "MMCF/D":
                    by_period.setdefault(period, {})["gross"] = val
                elif proc == NG_PROCESS_DRY and units == "MMCF":
                    by_period.setdefault(period, {})["dry"] = val / _DAYS

            sorted_periods = sorted(by_period.keys(), reverse=True)
            history = [
                {
                    "date":              f"{p}-01",
                    "gross_withdrawals": round(by_period[p].get("gross", 0) / 1000, 3) or None,
                    "dry_production":    round(by_period[p].get("dry",   0) / 1000, 3) or None,
                }
                for p in sorted_periods[:60]
            ]

            latest = history[0]  if history             else {}
            yr_ago = history[12] if len(history) > 12   else {}
            yoy_pct: float | None = None
            if latest.get("gross_withdrawals") and yr_ago.get("gross_withdrawals"):
                yoy_pct = round(
                    (latest["gross_withdrawals"] - yr_ago["gross_withdrawals"])
                    / yr_ago["gross_withdrawals"] * 100,
                    2,
                )

            return {
                "latest_gross_withdrawals": latest.get("gross_withdrawals"),
                "latest_dry_production":    latest.get("dry_production"),
                "yoy_change_pct":           yoy_pct,
                "history":                  history,
            }

        return await get_cache().cache_or_fetch("eia:us_natural_gas_v1", fetch, ttl=21600)

    async def get_us_reserves(self) -> dict[str, Any]:
        """Annual US proved reserves — crude (BBbl) and dry natural gas (Tcf)."""
        async def fetch() -> dict[str, Any]:
            # Process codes: R01 = crude proved reserves at end of year (pres).
            # R11 = "Dry, Expected Future Production" = proved dry-gas reserves
            # (enr/dry). Other R-codes on these routes are reserves changes.
            crude_rows, ng_rows = await asyncio.gather(
                self._fetch_eia_series(
                    _ROUTE_CRUDE_RESERVES,
                    {"duoarea": ["NUS"], "product": ["EPC0"], "process": ["R01"]},
                    frequency="annual", length=30,
                ),
                self._fetch_eia_series(
                    _ROUTE_NG_RESERVES,
                    {"duoarea": ["NUS"], "process": ["R11"]},
                    frequency="annual", length=30,
                ),
                return_exceptions=True,
            )
            if isinstance(crude_rows, Exception):
                logger.warning("eia.us_reserves: crude failed: %s", crude_rows)
                crude_rows = []
            if isinstance(ng_rows, Exception):
                logger.warning("eia.us_reserves: ng failed: %s", ng_rows)
                ng_rows = []

            def _build_history(rows: list[dict[str, Any]], divisor: float) -> list[dict[str, Any]]:
                history: list[dict[str, Any]] = []
                for row in rows:
                    try:
                        val = float(row["value"])
                    except (TypeError, ValueError):
                        continue
                    history.append({"year": row["period"], "value": round(val / divisor, 3)})
                history.sort(key=lambda x: x["year"], reverse=True)
                return history

            # Crude pres units are MMBBL (million bbl) → /1000 → billion bbl.
            # NG enr/dry units are BCF (billion cf) → /1000 → trillion cf.
            crude_history = _build_history(crude_rows, 1000.0)
            ng_history    = _build_history(ng_rows,    1000.0)

            return {
                "crude_latest_year": crude_history[0]["year"]  if crude_history else None,
                "crude_proved_bbbl": crude_history[0]["value"] if crude_history else None,
                "ng_latest_year":    ng_history[0]["year"]     if ng_history    else None,
                "ng_proved_tcf":     ng_history[0]["value"]    if ng_history    else None,
                "crude_history":     crude_history,
                "ng_history":        ng_history,
            }

        return await get_cache().cache_or_fetch("eia:us_reserves_v1", fetch, ttl=604800)

    # =========================================================================
    # Upstream — OPEC+ subtab
    # =========================================================================

    async def get_opec_production(self, basis: str = "crude") -> dict[str, Any]:
        """Monthly OPEC+ production — hero KPIs, country table, 36M sparklines.

        Fetches 21 members × 36 months from EIA international in one request.
        Values are in MBD (input is TBPD from EIA, /1000). Returned newest-first
        so the frontend's toLwPoints() can reverse for chart consumption.

        basis: "crude" (productId 57, crude+condensate — default) or "liquids"
               (productId 55, total liquids incl. NGPL).
        """
        basis = basis if basis in _INTL_PRODUCT_BY_BASIS else "crude"
        product_id = _INTL_PRODUCT_BY_BASIS[basis]

        async def fetch() -> dict[str, Any]:
            # 21 countries × 36 months + generous buffer = 800 rows.
            rows = await self._fetch_eia_series(
                _ROUTE_INTERNATIONAL,
                {
                    "countryRegionId": list(OPEC_PLUS_PROD_MEMBERS.keys()),
                    "productId":       [product_id],
                    "activityId":      [_INTL_ACTIVITY_ID],
                },
                frequency="monthly", length=800,
            )

            # Build lookup: {period: {iso3: value_tbpd}}
            by_period_country: dict[str, dict[str, float]] = {}
            for row in rows:
                period = row.get("period", "")
                iso3   = row.get("countryRegionId", "")
                if not period or iso3 not in OPEC_PLUS_PROD_MEMBERS:
                    continue
                try:
                    val = float(row["value"])
                except (TypeError, ValueError):
                    continue
                by_period_country.setdefault(period, {})[iso3] = val

            sorted_periods = sorted(by_period_country.keys(), reverse=True)
            if not sorted_periods:
                return {"hero": {}, "table": [], "sparklines": {}}

            latest_p   = sorted_periods[0]
            prev_p     = sorted_periods[1]  if len(sorted_periods) > 1  else None
            yr_ago_p   = sorted_periods[12] if len(sorted_periods) > 12 else None

            latest = by_period_country[latest_p]
            prev   = by_period_country.get(prev_p,   {}) if prev_p   else {}
            yr_ago = by_period_country.get(yr_ago_p, {}) if yr_ago_p else {}

            total_tbpd = sum(latest.values())

            def _mbd(iso: str, src: dict[str, float]) -> float | None:
                v = src.get(iso)
                return round(v / 1000, 3) if v is not None else None

            def _mom_mbd(iso: str) -> float | None:
                cur, prv = latest.get(iso), prev.get(iso)
                return round((cur - prv) / 1000, 3) if (cur is not None and prv is not None) else None

            hero: dict[str, Any] = {
                "total_mbd":     round(total_tbpd / 1000, 3),
                "total_mom":     round((total_tbpd - sum(prev.values())) / 1000, 3) if prev else None,
                "latest_period": latest_p,
                "saudi_mbd":     _mbd("SAU", latest),
                "saudi_mom":     _mom_mbd("SAU"),
                "russia_mbd":    _mbd("RUS", latest),
                "russia_mom":    _mom_mbd("RUS"),
                "iraq_mbd":      _mbd("IRQ", latest),
                "iraq_mom":      _mom_mbd("IRQ"),
            }

            table: list[dict[str, Any]] = []
            for iso3, display_name in OPEC_PLUS_PROD_MEMBERS.items():
                cur = latest.get(iso3)
                if cur is None:
                    continue
                prv = prev.get(iso3)
                yra = yr_ago.get(iso3)
                table.append({
                    "iso3":       iso3,
                    "country":    display_name,
                    "latest_mbd": round(cur / 1000, 3),
                    "mom":        round((cur - prv) / 1000, 3)        if prv is not None else None,
                    "mom_pct":    round((cur - prv) / prv * 100, 2)   if (prv and prv > 0) else None,
                    "yoy":        round((cur - yra) / 1000, 3)        if yra is not None else None,
                    "yoy_pct":    round((cur - yra) / yra * 100, 2)   if (yra and yra > 0) else None,
                    "share_pct":  round(cur / total_tbpd * 100, 1)    if total_tbpd else None,
                })
            table.sort(key=lambda x: x["latest_mbd"], reverse=True)

            # Sparklines: newest-first (toLwPoints reverses on the frontend).
            sparklines: dict[str, list[dict[str, Any]]] = {}
            for p in sorted_periods[:36]:
                for iso3, val in by_period_country[p].items():
                    if iso3 in OPEC_PLUS_PROD_MEMBERS:
                        sparklines.setdefault(iso3, []).append({
                            "period": f"{p}-01",
                            "value":  round(val / 1000, 3),
                        })

            return {"hero": hero, "table": table, "sparklines": sparklines}

        # v2 + basis suffix: v1 served productId 55 (total liquids) mislabeled as
        # crude — bust it, and cache crude/liquids independently.
        return await get_cache().cache_or_fetch(f"eia:opec_production_v2:{basis}", fetch, ttl=21600)

    async def get_opec_history(self, basis: str = "crude") -> dict[str, Any]:
        """Monthly OPEC+ production — all members, 10Y history for stacked area.

        Returns per-member lists, newest-first, in MBD. Frontend reverses for LWCharts.
        Request is one shot (21 × 120 = 2520 rows) — EIA international handles this
        in a single page at the given length.

        basis: "crude" (productId 57 — default) or "liquids" (productId 55).
        """
        basis = basis if basis in _INTL_PRODUCT_BY_BASIS else "crude"
        product_id = _INTL_PRODUCT_BY_BASIS[basis]

        async def fetch() -> dict[str, Any]:
            rows = await self._fetch_eia_series(
                _ROUTE_INTERNATIONAL,
                {
                    "countryRegionId": list(OPEC_PLUS_PROD_MEMBERS.keys()),
                    "productId":       [product_id],
                    "activityId":      [_INTL_ACTIVITY_ID],
                },
                frequency="monthly", length=2600,
            )

            by_period_country: dict[str, dict[str, float]] = {}
            for row in rows:
                period = row.get("period", "")
                iso3   = row.get("countryRegionId", "")
                if not period or iso3 not in OPEC_PLUS_PROD_MEMBERS:
                    continue
                try:
                    val = float(row["value"])
                except (TypeError, ValueError):
                    continue
                by_period_country.setdefault(period, {})[iso3] = val

            sorted_periods = sorted(by_period_country.keys(), reverse=True)[:120]

            # Build {iso3: [{period, value}]} newest-first.
            members: dict[str, list[dict[str, Any]]] = {}
            for p in sorted_periods:
                for iso3, val in by_period_country[p].items():
                    if iso3 in OPEC_PLUS_PROD_MEMBERS:
                        members.setdefault(iso3, []).append({
                            "period": f"{p}-01",
                            "value":  round(val / 1000, 3),
                        })

            return {"members": members, "periods_available": len(sorted_periods)}

        # v2 + basis suffix — see get_opec_production note.
        return await get_cache().cache_or_fetch(f"eia:opec_history_v2:{basis}", fetch, ttl=86400)

    async def _opec_actual_cutoff(self) -> str:
        """Latest OPEC actual month (YYYY-MM) from EIA international.

        Used as the actual/forecast boundary for STEO-derived OPEC series: STEO's
        two most recent monthly aggregates are typically incomplete (they inflate
        spare/disruption figures), so periods after this clean cutoff are treated
        as forecast / excluded from headline snapshots.
        """
        intl_rows = await self._fetch_eia_series(
            _ROUTE_INTERNATIONAL,
            {
                "countryRegionId": ["OPEC"],
                "productId":       [_INTL_PRODUCT_CRUDE],
                "activityId":      [_INTL_ACTIVITY_ID],
            },
            frequency="monthly", length=6,
        )
        return max((r.get("period", "") for r in intl_rows if r.get("period")), default="")

    async def get_opec_overview(self) -> dict[str, Any]:
        """OPEC+ overview from EIA STEO, anchored to EIA international.

        Split-sources design: production *totals* come from /opec/production
        (international). This endpoint supplies what international lacks — OPEC
        spare/production capacity, the structural split (OPEC vs OPEC+ other vs
        non-OPEC+ ex-US), and the world balance (STEO T3 net inventory
        withdrawals). STEO values are already mb/d (NOT divided).

        Actual/forecast boundary = international's latest OPEC month. STEO periods
        at or before it are actuals; later periods are forecast (for the cone).
        STEO's two most recent monthly aggregates are typically incomplete (OPEC
        capacity appears to drop by ~10 mb/d), so anchoring to international keeps
        them on the forecast side and out of the hero. Basis-independent (crude).
        """
        async def fetch() -> dict[str, Any]:
            # 1) Cutoff = latest OPEC actual month from international (clean source).
            cutoff = await self._opec_actual_cutoff()

            # 2) STEO capacity / spare / structural split / balance.
            steo_series = [
                _STEO_CAPACITY, _STEO_SPARE, _STEO_BALANCE,
                _STEO_SPLIT_OPEC, _STEO_SPLIT_OPEC_OTHER, _STEO_SPLIT_NON,
            ]
            rows = await self._fetch_eia_series(
                _ROUTE_STEO,
                {"seriesId": steo_series},
                frequency="monthly", length=1600,
            )

            # Partition by seriesId → {period: value}.
            by_series: dict[str, dict[str, float]] = {}
            for row in rows:
                sid    = row.get("seriesId", "")
                period = row.get("period", "")
                if not sid or not period:
                    continue
                try:
                    by_series.setdefault(sid, {})[period] = float(row["value"])
                except (TypeError, ValueError):
                    continue

            def _v(sid: str, period: str | None) -> float | None:
                return by_series.get(sid, {}).get(period) if period else None

            def _r(v: float | None) -> float | None:
                return round(v, 3) if v is not None else None

            def _is_forecast(period: str) -> bool:
                return bool(cutoff) and period > cutoff

            # Hero: latest actual capacity period (<= cutoff). With cutoff from
            # international, STEO's broken trailing months are excluded.
            cap_actual = sorted(
                (p for p in by_series.get(_STEO_CAPACITY, {}) if not cutoff or p <= cutoff),
                reverse=True,
            )
            latest = cap_actual[0] if cap_actual else None

            capacity = _v(_STEO_CAPACITY, latest)
            spare    = _v(_STEO_SPARE, latest)
            opec_pr  = _v(_STEO_SPLIT_OPEC, latest)
            t3       = _v(_STEO_BALANCE, latest)
            # Implied supply−demand balance = −(net inventory withdrawals).
            balance  = round(-t3, 3) if t3 is not None else None
            util     = round(opec_pr / capacity * 100, 1) if (opec_pr and capacity) else None

            hero = {
                "last_actual_period":       latest,
                "spare_capacity_mbd":       _r(spare),
                "production_capacity_mbd":  _r(capacity),
                "capacity_utilization_pct": util,
                "market_balance_mbd":       balance,
                "market_balance_label":     None if balance is None else ("surplus" if balance >= 0 else "deficit"),
            }

            # Histories (newest-first, most recent ~180 months incl. forecast).
            all_periods = sorted({p for s in by_series.values() for p in s}, reverse=True)[:180]

            capacity_history = [
                {
                    "period":      f"{p}-01",
                    "is_forecast": _is_forecast(p),
                    "production":  _r(_v(_STEO_SPLIT_OPEC, p)),
                    "capacity":    _r(_v(_STEO_CAPACITY, p)),
                    "spare":       _r(_v(_STEO_SPARE, p)),
                }
                for p in all_periods
            ]
            split_history = [
                {
                    "period":          f"{p}-01",
                    "is_forecast":     _is_forecast(p),
                    "opec":            _r(_v(_STEO_SPLIT_OPEC, p)),
                    "opec_plus_other": _r(_v(_STEO_SPLIT_OPEC_OTHER, p)),
                    "non_opec_plus":   _r(_v(_STEO_SPLIT_NON, p)),
                }
                for p in all_periods
            ]
            balance_history = [
                {
                    "period":          f"{p}-01",
                    "is_forecast":     _is_forecast(p),
                    "net_withdrawals": _r(_v(_STEO_BALANCE, p)),
                    "implied_balance": (_r(-_v(_STEO_BALANCE, p)) if _v(_STEO_BALANCE, p) is not None else None),
                }
                for p in all_periods
            ]

            return {
                "last_actual_period": latest,
                "hero":               hero,
                "capacity_history":   capacity_history,
                "split_history":      split_history,
                "balance_history":    balance_history,
            }

        return await get_cache().cache_or_fetch("eia:opec_overview_v2", fetch, ttl=21600)

    async def get_opec_disruptions(self) -> dict[str, Any]:
        """OPEC+ unplanned production disruptions (barrels offline) — STEO PADI_*.

        Values already mb/d. Returns a latest-month snapshot per country (+MoM)
        and per-country history (newest-first, ~48 months) for a stacked area.
        PADI series track current/near-term disruptions and are more timely than
        the international production data.
        """
        async def fetch() -> dict[str, Any]:
            # Anchor to international's latest clean OPEC month — STEO's broken
            # trailing months inflate OPEC-member disruptions (Saudi 0.07 → 3.57).
            cutoff = await self._opec_actual_cutoff()

            rows = await self._fetch_eia_series(
                _ROUTE_STEO,
                {"seriesId": list(_STEO_PADI.keys())},
                frequency="monthly", length=1200,
            )

            by_series: dict[str, dict[str, float]] = {}
            for row in rows:
                sid    = row.get("seriesId", "")
                period = row.get("period", "")
                if sid not in _STEO_PADI or not period:
                    continue
                try:
                    by_series.setdefault(sid, {})[period] = float(row["value"])
                except (TypeError, ValueError):
                    continue

            # Only periods at/before the clean cutoff.
            all_periods = sorted(
                {p for s in by_series.values() for p in s if not cutoff or p <= cutoff},
                reverse=True,
            )
            latest = all_periods[0] if all_periods else None
            prev   = all_periods[1] if len(all_periods) > 1 else None

            countries: list[dict[str, Any]] = []
            for code, name in _STEO_PADI.items():
                series = by_series.get(code, {})
                cur = series.get(latest) if latest else None
                if cur is None:
                    continue
                prv = series.get(prev) if prev else None
                countries.append({
                    "code":       code,
                    "name":       name,
                    "latest_mbd": round(cur, 3),
                    "mom":        round(cur - prv, 3) if prv is not None else None,
                })
            countries.sort(key=lambda c: c["latest_mbd"], reverse=True)
            total = round(sum(c["latest_mbd"] for c in countries), 3) if countries else None

            # Per-country history (newest-first, last 48 months). Drop countries
            # with no disruption in the window to keep the stacked chart legible.
            recent = all_periods[:48]
            series_out: dict[str, list[dict[str, Any]]] = {}
            for code, name in _STEO_PADI.items():
                svals = by_series.get(code, {})
                pts = [{"period": f"{p}-01", "value": round(svals[p], 3)} for p in recent if p in svals]
                if any(pt["value"] > 0 for pt in pts):
                    series_out[name] = pts

            return {
                "latest_period": latest,
                "total_mbd":     total,
                "countries":     countries,
                "series":        series_out,
            }

        return await get_cache().cache_or_fetch("eia:opec_disruptions_v1", fetch, ttl=21600)

    async def get_opec_compliance(self) -> dict[str, Any]:
        """OPEC+ quota compliance — required (hand-curated JSON) vs actual production.

        Actual is crude + lease condensate (international productId 57) at the
        latest available month (lags ~4-5 months). Required levels are the current
        OPEC+ targets. delta = actual − required; positive = over-producing.
        Exempt members (Iran/Libya/Venezuela) are not in the quota file.
        """
        async def fetch() -> dict[str, Any]:
            quotas = _load_opec_quotas()
            required = quotas.get("required_mbd", {})

            prod = await self.get_opec_production(basis="crude")
            actual_by_iso = {r["iso3"]: r["latest_mbd"] for r in prod.get("table", [])}
            actual_period = prod.get("hero", {}).get("latest_period")

            rows: list[dict[str, Any]] = []
            for iso, req in required.items():
                actual = actual_by_iso.get(iso)
                delta = round(actual - req, 3) if actual is not None else None
                status = None
                if delta is not None:
                    status = "over" if delta > 0 else "under" if delta < 0 else "on"
                rows.append({
                    "iso3":         iso,
                    "country":      OPEC_PLUS_PROD_MEMBERS.get(iso, iso),
                    "required_mbd": round(req, 3),
                    "actual_mbd":   round(actual, 3) if actual is not None else None,
                    "delta_mbd":    delta,
                    "status":       status,
                })
            # Biggest over-producers first; missing-actual rows sink to the bottom.
            rows.sort(key=lambda r: r["delta_mbd"] if r["delta_mbd"] is not None else -1e9, reverse=True)

            present = [r for r in rows if r["actual_mbd"] is not None]
            total_req = round(sum(r["required_mbd"] for r in present), 3) if present else None
            total_act = round(sum(r["actual_mbd"] for r in present), 3) if present else None
            total_delta = (
                round(total_act - total_req, 3)
                if (total_req is not None and total_act is not None) else None
            )

            return {
                "as_of":              quotas.get("as_of"),
                "source":             quotas.get("source"),
                "actual_period":      actual_period,
                "total_required_mbd": total_req,
                "total_actual_mbd":   total_act,
                "total_delta_mbd":    total_delta,
                "rows":               rows,
            }

        return await get_cache().cache_or_fetch("eia:opec_compliance_v1", fetch, ttl=21600)

    async def _fetch_jodi_opec_crude(self) -> dict[str, float]:
        """Sum the 5 JODI-reporting OPEC members' crude production per period.

        JODI primary CSV cols: REF_AREA, TIME_PERIOD, ENERGY_PRODUCT,
        FLOW_BREAKDOWN, UNIT_MEASURE, OBS_VALUE, ASSESSMENT_CODE. We keep
        ENERGY_PRODUCT=CRUDEOIL, FLOW=INDPROD (indigenous production), UNIT=KBD,
        then KBD → mb/d (/1000). Missing values are "-"/"x" → skipped.
        """
        by_period: dict[str, float] = {}
        for url in _jodi_csv_urls():
            try:
                resp = await request_with_retry(
                    "GET", url,
                    timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("eia.cross_check: JODI fetch failed (%s): %s", url, exc)
                continue
            for line in resp.text.splitlines():
                parts = line.split(",")
                if len(parts) < 6:
                    continue
                area, period, product, flow, unit, value = parts[:6]
                if product != "CRUDEOIL" or flow != "INDPROD" or unit != "KBD":
                    continue
                if area not in _JODI_OPEC:
                    continue
                try:
                    by_period[period] = by_period.get(period, 0.0) + float(value) / 1000.0
                except ValueError:
                    continue
        return by_period

    async def get_opec_cross_check(self) -> dict[str, Any]:
        """Cross-check OPEC crude production: EIA international vs JODI.

        Compared on the SAME five OPEC members that report to JODI (Saudi,
        Kuwait, Nigeria, Algeria, Venezuela) — apples to apples. Returns aligned
        monthly series (newest-first) + the latest month where both report.
        """
        async def fetch() -> dict[str, Any]:
            prod = await self.get_opec_production(basis="crude")
            spark = prod.get("sparklines", {})

            eia_by_period: dict[str, float] = {}
            for iso3 in _JODI_OPEC.values():
                for pt in spark.get(iso3, []):
                    if pt.get("value") is None:
                        continue
                    key = pt["period"][:7]  # YYYY-MM
                    eia_by_period[key] = eia_by_period.get(key, 0.0) + pt["value"]

            jodi_by_period = await self._fetch_jodi_opec_crude()

            keys = sorted(set(eia_by_period) | set(jodi_by_period), reverse=True)[:36]
            history = [
                {
                    "period": f"{k}-01",
                    "eia":    round(eia_by_period[k], 3) if k in eia_by_period else None,
                    "jodi":   round(jodi_by_period[k], 3) if k in jodi_by_period else None,
                }
                for k in keys
            ]

            latest = next(
                (h for h in history if h["eia"] is not None and h["jodi"] is not None), None
            )
            return {
                "members":       [OPEC_PLUS_PROD_MEMBERS[i] for i in _JODI_OPEC.values()],
                "latest_period": latest["period"] if latest else None,
                "eia_latest":    latest["eia"] if latest else None,
                "jodi_latest":   latest["jodi"] if latest else None,
                "diff_latest":   round(latest["eia"] - latest["jodi"], 3) if latest else None,
                "history":       history,
            }

        return await get_cache().cache_or_fetch("eia:opec_cross_check_v1", fetch, ttl=86400)

    # =========================================================================
    # Midstream sub-endpoints
    # =========================================================================

    async def get_midstream_stocks(self) -> dict[str, Any]:
        """Weekly US commercial petroleum stocks — crude, Cushing, gasoline, distillate,
        jet fuel, SPR — with 2Y history, latest values, WoW changes, and days-of-supply.

        Units: all stock values in Thousand Barrels (KBBL) as published by EIA.
        Days-of-supply = latest stocks (KBBL) / latest 4-week-avg demand (kbpd).
        """
        _HISTORY = 104  # 2 years of weekly data

        async def fetch() -> dict[str, Any]:
            results = await asyncio.gather(
                self._fetch_eia_series(_ROUTE_STOCKS, CRUDE_STOCKS,     length=_HISTORY),
                self._fetch_eia_series(_ROUTE_STOCKS, CUSHING_STOCKS,   length=_HISTORY),
                self._fetch_eia_series(_ROUTE_STOCKS, GASOLINE_STOCKS,  length=_HISTORY),
                self._fetch_eia_series(_ROUTE_STOCKS, DISTILLATE_STOCKS,length=_HISTORY),
                self._fetch_eia_series(_ROUTE_STOCKS, JET_FUEL_STOCKS,  length=_HISTORY),
                self._fetch_eia_series(_ROUTE_STOCKS, SPR_LEVEL,        length=_HISTORY),
                self._fetch_eia_series(_ROUTE_PRODUCT_SUPPLIED, PRODUCT_SUPPLIED),
                return_exceptions=True,
            )

            labels = ["crude", "cushing", "gasoline", "distillate", "jet", "spr", "demand"]
            parsed: dict[str, Any] = {}
            for label, result in zip(labels, results):
                if isinstance(result, Exception):
                    logger.warning("midstream.stocks: %s failed: %s", label, result)
                    parsed[label] = []
                else:
                    parsed[label] = self._parse_series(result)  # type: ignore[arg-type]

            demand = parsed["demand"]  # product_supplied returns all products mixed
            # _parse_series strips the product key; demand rows already parsed by _parse_grouped
            # but here we used the generic PRODUCT_SUPPLIED facet — get product-level demand
            # properly via re-parsing the raw rows with grouping
            gasoline_demand_rows = await asyncio.gather(
                self._fetch_eia_series(_ROUTE_PRODUCT_SUPPLIED, {"product": ["EPM0F"], "duoarea": ["NUS"], "process": ["VPP"]}),
                self._fetch_eia_series(_ROUTE_PRODUCT_SUPPLIED, {"product": ["EPD0"],  "duoarea": ["NUS"], "process": ["VPP"]}),
                self._fetch_eia_series(_ROUTE_PRODUCT_SUPPLIED, {"product": ["EPJK"],  "duoarea": ["NUS"], "process": ["VPP"]}),
                return_exceptions=True,
            )
            gas_demand  = self._parse_series(gasoline_demand_rows[0]) if not isinstance(gasoline_demand_rows[0], Exception) else []
            dist_demand = self._parse_series(gasoline_demand_rows[1]) if not isinstance(gasoline_demand_rows[1], Exception) else []
            jet_demand  = self._parse_series(gasoline_demand_rows[2]) if not isinstance(gasoline_demand_rows[2], Exception) else []

            def _series(rows: list[dict[str, Any]]) -> dict[str, Any]:
                if not rows:
                    return {"latest_kbbl": None, "wow_kbbl": None, "history": []}
                latest = rows[0]
                return {
                    "latest_kbbl": latest["value"],
                    "wow_kbbl":    latest.get("wow_change"),
                    "history":     [{"period": r["period"], "value": r["value"]} for r in rows],
                }

            def _dos(stocks: list[dict[str, Any]], demand_rows: list[dict[str, Any]]) -> float | None:
                s = stocks[0]["value"]  if stocks  else None
                d = demand_rows[0]["value"] if demand_rows else None
                if s is None or not d:
                    return None
                return round(s / d, 1)

            return {
                "crude":        _series(parsed["crude"]),
                "cushing":      _series(parsed["cushing"]),
                "gasoline":     _series(parsed["gasoline"]),
                "distillate":   _series(parsed["distillate"]),
                "jet":          _series(parsed["jet"]),
                "spr":          _series(parsed["spr"]),
                "dos_gasoline":  _dos(parsed["gasoline"],  gas_demand),
                "dos_distillate":_dos(parsed["distillate"], dist_demand),
                "dos_jet":       _dos(parsed["jet"],        jet_demand),
            }

        return await get_cache().cache_or_fetch("eia:midstream_stocks_v1", fetch, ttl=3600)

    async def get_crude_exports(self) -> dict[str, Any]:
        """US crude oil exports — weekly MBD history + monthly PADD breakdown.

        Weekly series: petroleum/sum/sndw, duoarea NUS-Z00, process EEX (MBBL/D).
        Monthly PADD series: petroleum/move/expcp, one row per PADD of origin (MBBL/month).
        """
        _DAYS = 30.5

        async def fetch() -> dict[str, Any]:
            weekly_rows, padd_rows = await asyncio.gather(
                self._fetch_eia_series(
                    _ROUTE_WEEKLY_SUPPLY, CRUDE_EXPORTS_WEEKLY,
                    frequency="weekly", length=260,
                ),
                self._fetch_eia_series(
                    _ROUTE_CRUDE_EXPCP,
                    {"product": ["EPC0"], "process": ["EEX"],
                     "duoarea": CRUDE_EXPORTS_PADD_AREAS},
                    frequency="monthly", length=300,
                ),
                return_exceptions=True,
            )
            if isinstance(weekly_rows, Exception):
                logger.warning("midstream.exports: weekly failed: %s", weekly_rows)
                weekly_rows = []
            if isinstance(padd_rows, Exception):
                logger.warning("midstream.exports: padd monthly failed: %s", padd_rows)
                padd_rows = []

            weekly = self._parse_series(weekly_rows)
            weekly_history = [
                {"date": r["period"], "value": round(r["value"] / 1000, 3)}
                for r in weekly
            ]

            # Monthly PADD: aggregate to US total per period + keep per-PADD latest
            by_period_padd: dict[str, dict[str, float]] = {}
            for row in padd_rows:
                period = row.get("period", "")
                area   = row.get("duoarea", "")      # e.g., "R30-Z00"
                padd   = area.split("-")[0]           # → "R30"
                label  = PADD_SHORT.get(padd, "")
                if not period or not label:
                    continue
                try:
                    val = float(row["value"])
                except (TypeError, ValueError):
                    continue
                by_period_padd.setdefault(period, {})[label] = val

            sorted_periods_m = sorted(by_period_padd.keys(), reverse=True)
            monthly_history = [
                {
                    "date":  f"{p}-01",
                    "value": round(sum(by_period_padd[p].values()) / _DAYS / 1000, 3),
                }
                for p in sorted_periods_m[:36]
            ]

            latest_padd = by_period_padd.get(sorted_periods_m[0], {}) if sorted_periods_m else {}

            return {
                "latest_mbd":        round(weekly[0]["value"] / 1000, 3) if weekly else None,
                "wow_mbd":           round(weekly[0].get("wow_change", 0) / 1000, 3) if weekly and weekly[0].get("wow_change") is not None else None,
                "weekly_history":    weekly_history,
                "latest_period_m":   sorted_periods_m[0] if sorted_periods_m else None,
                "padd1_mbbl":        latest_padd.get("padd1"),
                "padd2_mbbl":        latest_padd.get("padd2"),
                "padd3_mbbl":        latest_padd.get("padd3"),
                "padd4_mbbl":        latest_padd.get("padd4"),
                "padd5_mbbl":        latest_padd.get("padd5"),
                "monthly_history":   monthly_history,
            }

        return await get_cache().cache_or_fetch("eia:crude_exports_v1", fetch, ttl=3600)

    async def get_midstream_imports(self) -> dict[str, Any]:
        """Monthly US crude imports by country — top origins table + history + OPEC+ share.

        Reuses the same crude-oil-imports route as the upstream tab but returns
        only the monthly final feed enriched with an OPEC+ aggregate share.
        """
        async def fetch() -> dict[str, Any]:
            rows = await self._fetch_eia_series(
                _ROUTE_CRUDE_IMPORTS,
                {"originType": ["CTY"], "destinationType": ["US"]},
                frequency="monthly", length=2000,
                data_columns=["quantity"],
            )
            feed = self._build_monthly_imports(rows)

            # Compute OPEC+ share of latest month imports.
            origins = feed.get("top_origins", [])
            total   = feed.get("total_mbd")
            opec_mbd = sum(o["volume_mbd"] for o in origins if o.get("is_opec_plus"))
            opec_share = round(opec_mbd / total * 100, 1) if total else None

            return {
                "total_mbd":        total,
                "top_origins":      origins,
                "history":          feed.get("history", []),
                "opec_plus_mbd":    round(opec_mbd, 3),
                "opec_plus_share":  opec_share,
            }

        return await get_cache().cache_or_fetch("eia:midstream_imports_v1", fetch, ttl=21600)

    async def get_padd_movements(self) -> dict[str, Any]:
        """Monthly inter-PADD crude pipeline movements — all 13 directional pairs.

        Route: petroleum/move/pipe, product EPC0, process LMV (movements by pipeline).
        duoarea format: "DEST-SRC" — e.g., "R20-R30" = PADD 2 receives crude from PADD 3.
        Units: MBBL/month (no per-day variant on this route).

        Returns: per-pair history (newest-first) + latest-month net receipts per PADD
        (positive = net importer, negative = net shipper).
        """
        async def fetch() -> dict[str, Any]:
            rows = await self._fetch_eia_series(
                _ROUTE_PADD_PIPE,
                {
                    "product":  ["EPC0"],
                    "process":  ["LMV"],
                    "duoarea":  PADD_PIPE_PAIRS,
                },
                frequency="monthly", length=800,
            )

            # Build {pair: {period: mbbl}}
            by_pair_period: dict[str, dict[str, float]] = {}
            for row in rows:
                pair   = row.get("duoarea", "")
                period = row.get("period",  "")
                if not pair or not period or pair not in PADD_PIPE_PAIRS:
                    continue
                try:
                    val = float(row["value"])
                except (TypeError, ValueError):
                    continue
                by_pair_period.setdefault(pair, {})[period] = val

            # Build history lists (newest-first) per pair
            flows: dict[str, list[dict[str, Any]]] = {}
            for pair, by_period in by_pair_period.items():
                flows[pair] = [
                    {"period": f"{p}-01", "value": round(v, 1)}
                    for p, v in sorted(by_period.items(), reverse=True)[:36]
                ]

            # Compute latest-month net receipts per PADD
            all_periods = sorted(
                {p for by_period in by_pair_period.values() for p in by_period},
                reverse=True,
            )
            latest_p = all_periods[0] if all_periods else None
            net: dict[str, float] = {label: 0.0 for label in PADD_SHORT.values()}
            if latest_p:
                for pair, by_period in by_pair_period.items():
                    val = by_period.get(latest_p, 0.0)
                    dest, src = pair[:3], pair[4:]
                    dest_lbl = PADD_SHORT.get(dest)
                    src_lbl  = PADD_SHORT.get(src)
                    if dest_lbl:
                        net[dest_lbl] = net.get(dest_lbl, 0.0) + val   # receipts +
                    if src_lbl:
                        net[src_lbl]  = net.get(src_lbl,  0.0) - val   # shipments −

            # Human-readable labels for each pair
            flow_labels = {
                pair: f"{PADD_LABELS.get(pair[:3], pair[:3])} from {PADD_LABELS.get(pair[4:], pair[4:])}"
                for pair in PADD_PIPE_PAIRS
            }

            return {
                "latest_period": latest_p,
                "flows":         flows,
                "net_receipts":  {k: round(v, 1) for k, v in net.items()},
                "flow_labels":   flow_labels,
            }

        return await get_cache().cache_or_fetch("eia:padd_movements_v1", fetch, ttl=86400)
