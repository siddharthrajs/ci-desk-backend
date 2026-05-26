"""
EIA Open Data API v2 service.

All public methods return data sorted newest-first with week-over-week deltas added.
Series-filter constants are defined at module level — verify or explore codes at:
  https://www.eia.gov/opendata/browser/petroleum
"""

from __future__ import annotations

import asyncio
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
_ROUTE_MONTHLY_PRODUCTION = "petroleum/crd/crpdn"
_ROUTE_DUC = "petroleum/crd/duc"
_ROUTE_CRUDE_IMPORTS = "crude-oil-imports"

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

# ---------------------------------------------------------------------------
# petroleum/crd/crpdn — Monthly crude production by PADD/region (Thousand Bbl/Day)
#   product: EPC0=crude oil including condensates
#   process: FPF=field production of crude
#   duoarea: NUS=US total, R30=PADD 3 Gulf Coast, R20=PADD 2 Midwest, GOMS=GOM offshore
# ---------------------------------------------------------------------------

MONTHLY_PROD_AREAS: dict[str, str] = {
    "NUS":  "us_total",
    "R30":  "padd3",
    "R20":  "padd2",
    "GOMS": "gom",
}

MONTHLY_PROD_FACETS: dict[str, list[str]] = {
    "duoarea": list(MONTHLY_PROD_AREAS.keys()),
    "product": ["EPC0"],
    "process": ["FPF"],
}

# ---------------------------------------------------------------------------
# petroleum/crd/duc — DUC (Drilled but Uncompleted) wells by basin, monthly
#
# Facet values discovered via GET /v2/petroleum/crd/duc (no /data).
# Known duoarea codes as of 2024:
#   NUS=US Total, PERM=Permian, EFRD=Eagle Ford, BKKN=Bakken,
#   NBRR=DJ Niobrara, APPS=Appalachia, ANK=Anadarko, HSVL=Haynesville
# ---------------------------------------------------------------------------

DUC_AREAS: dict[str, str] = {
    "NUS":  "total",
    "PERM": "permian",
    "EFRD": "eagle_ford",
    "BKKN": "bakken",
    "NBRR": "niobrara",
    "APPS": "appalachia",
    "ANK":  "anadarko",
    "HSVL": "haynesville",
}

DUC_FACETS: dict[str, list[str]] = {
    "duoarea": list(DUC_AREAS.keys()),
}

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

    # -------------------------------------------------------------------------
    # Upstream sub-endpoints (new)
    # -------------------------------------------------------------------------

    async def get_us_production_monthly(self) -> dict[str, Any]:
        """Monthly U.S. crude production by region (MBD) — US total, PADD 2, PADD 3, GOM — 36 months.

        length=144 = 4 areas × 36 months.
        """
        async def fetch() -> dict[str, Any]:
            rows = await self._fetch_eia_series(
                _ROUTE_MONTHLY_PRODUCTION,
                MONTHLY_PROD_FACETS,
                frequency="monthly",
                length=144,
            )
            by_period: dict[str, dict[str, float]] = {}
            for row in rows:
                period = row.get("period", "")
                label = MONTHLY_PROD_AREAS.get(row.get("duoarea", ""))
                if not period or not label:
                    continue
                try:
                    val = float(row["value"])
                except (TypeError, ValueError):
                    continue
                by_period.setdefault(period, {})[label] = val

            history = []
            for period in sorted(by_period.keys(), reverse=True):
                d = by_period[period]
                history.append({
                    "date":     f"{period}-01",
                    "us_total": round(d["us_total"] / 1000, 3) if "us_total" in d else None,
                    "padd3":    round(d["padd3"]    / 1000, 3) if "padd3"    in d else None,
                    "padd2":    round(d["padd2"]    / 1000, 3) if "padd2"    in d else None,
                    "gom":      round(d["gom"]      / 1000, 3) if "gom"      in d else None,
                })
            return {"monthly_history": history}

        return await get_cache().cache_or_fetch("eia:us_production_monthly_v1", fetch, ttl=86400)

    async def get_duc_wells(self) -> dict[str, Any]:
        """Monthly DUC well counts by basin — EIA DPR, 36 months.

        length=288 = 8 areas × 36 months.
        Signal: DRAW if US MoM < -50, BUILD if > +50, else NEUTRAL.
        """
        async def fetch() -> dict[str, Any]:
            rows = await self._fetch_eia_series(
                _ROUTE_DUC, DUC_FACETS, frequency="monthly", length=288,
            )

            by_area: dict[str, list[tuple[str, int]]] = {label: [] for label in DUC_AREAS.values()}
            for row in rows:
                label = DUC_AREAS.get(row.get("duoarea", ""))
                if not label:
                    continue
                try:
                    val = int(float(row["value"]))
                except (TypeError, ValueError):
                    continue
                by_area[label].append((row["period"], val))

            for label in by_area:
                by_area[label].sort(key=lambda x: x[0], reverse=True)

            total_series = by_area.get("total", [])
            latest_total    = total_series[0][1]  if total_series            else None
            prev_total      = total_series[1][1]  if len(total_series) >  1  else None
            year_ago_total  = total_series[12][1] if len(total_series) > 12  else None

            mom_change = (latest_total - prev_total)     if (latest_total is not None and prev_total     is not None) else None
            yoy_change = (latest_total - year_ago_total) if (latest_total is not None and year_ago_total is not None) else None
            mom_pct = round(mom_change / prev_total     * 100, 1) if (mom_change is not None and prev_total)     else None
            yoy_pct = round(yoy_change / year_ago_total * 100, 1) if (yoy_change is not None and year_ago_total) else None

            if mom_change is not None:
                signal = "DRAW" if mom_change < -50 else "BUILD" if mom_change > 50 else "NEUTRAL"
            else:
                signal = "NEUTRAL"

            all_periods = sorted({p for series in by_area.values() for p, _ in series}, reverse=True)
            by_period_basin: dict[str, dict[str, int]] = {}
            for label, series in by_area.items():
                for period, val in series:
                    by_period_basin.setdefault(period, {})[label] = val

            history = []
            for period in all_periods[:36]:
                d = by_period_basin.get(period, {})
                history.append({
                    "date":        f"{period}-01",
                    "total":       d.get("total"),
                    "permian":     d.get("permian"),
                    "eagle_ford":  d.get("eagle_ford"),
                    "bakken":      d.get("bakken"),
                    "niobrara":    d.get("niobrara"),
                    "appalachia":  d.get("appalachia"),
                    "anadarko":    d.get("anadarko"),
                    "haynesville": d.get("haynesville"),
                })

            basins: dict[str, dict[str, Any]] = {}
            for basin_label in ["permian", "eagle_ford", "bakken", "niobrara", "appalachia", "anadarko", "haynesville"]:
                series = by_area.get(basin_label, [])
                current = series[0][1] if series           else None
                prev    = series[1][1] if len(series) > 1 else None
                basins[basin_label] = {
                    "current":    current,
                    "mom_change": (current - prev) if (current is not None and prev is not None) else None,
                }

            return {
                "total_duc":  latest_total,
                "mom_change": mom_change,
                "mom_pct":    mom_pct,
                "yoy_change": yoy_change,
                "yoy_pct":    yoy_pct,
                "signal":     signal,
                "history":    history,
                "basins":     basins,
            }

        return await get_cache().cache_or_fetch("eia:duc_wells_v1", fetch, ttl=86400)

    async def get_crude_imports(self) -> dict[str, Any]:
        """Monthly U.S. crude imports by country of origin — top 10, last 12 months.

        EIA crude-oil-imports returns quantity in thousand barrels (total, not per day).
        Converts to MBD using a 30.5-day average month.
        length=500 covers ~12 months × ~40 active origin countries with buffer.
        """
        _DAYS = 30.5

        async def fetch() -> dict[str, Any]:
            rows = await self._fetch_eia_series(
                _ROUTE_CRUDE_IMPORTS,
                {},
                frequency="monthly",
                length=500,
                data_columns=["quantity"],
            )

            by_period: dict[str, dict[str, float]] = {}
            for row in rows:
                period = row.get("period", "")
                origin = row.get("originName", "")
                if not period or not origin:
                    continue
                try:
                    qty = float(row.get("quantity") or 0)
                except (TypeError, ValueError):
                    continue
                by_period.setdefault(period, {}).setdefault(origin, 0.0)
                by_period[period][origin] += qty

            sorted_periods = sorted(by_period.keys(), reverse=True)
            if not sorted_periods:
                return {"total_imports_mbd": None, "top_origins": [], "history_total": []}

            latest_data = by_period[sorted_periods[0]]
            prev_data   = by_period.get(sorted_periods[1], {}) if len(sorted_periods) > 1 else {}

            total_kbbl  = sum(latest_data.values())
            total_mbd   = round(total_kbbl / _DAYS / 1000, 2)

            sorted_origins = sorted(latest_data.items(), key=lambda x: x[1], reverse=True)
            top_origins: list[dict[str, Any]] = []
            for country, volume_kbbl in sorted_origins[:10]:
                volume_mbd = round(volume_kbbl / _DAYS / 1000, 2)
                share_pct  = round(volume_kbbl / total_kbbl * 100, 1) if total_kbbl else 0.0
                prev_mbd   = round(prev_data.get(country, 0) / _DAYS / 1000, 2) if prev_data else None
                mom_change = round(volume_mbd - prev_mbd, 2) if prev_mbd is not None else None
                top_origins.append({
                    "country":    country,
                    "volume_mbd": volume_mbd,
                    "share_pct":  share_pct,
                    "mom_change": mom_change,
                })

            history_total = [
                {
                    "date":  f"{p}-01",
                    "value": round(sum(by_period[p].values()) / _DAYS / 1000, 2),
                }
                for p in sorted_periods[:12]
            ]

            return {
                "total_imports_mbd": total_mbd,
                "top_origins":       top_origins,
                "history_total":     history_total,
            }

        return await get_cache().cache_or_fetch("eia:crude_imports_v1", fetch, ttl=86400)
