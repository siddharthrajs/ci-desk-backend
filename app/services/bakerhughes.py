"""Baker Hughes weekly rig count service.

Primary data source: FRED proxy series RIGTNXUS (U.S. Total Rotary Rigs in
Operation, Baker Hughes).

Known limitation: the oil/gas breakdown is not available via FRED or any
reliable free public API. Baker Hughes publishes a weekly Excel spreadsheet,
but the download URL and sheet format change without notice and are not
suitable as a production data source. A paid provider (Bloomberg, Refinitiv,
or a direct Baker Hughes data agreement) is required for a robust oil/gas
split. See README.md for details.

The `available` field in every response signals whether usable data was
retrieved — consumers must check this flag rather than assuming success.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.services.fred import FREDService

logger = logging.getLogger(__name__)

# FRED proxy series for Baker Hughes weekly U.S. rig count (not seasonally adjusted)
_FRED_TOTAL_RIGS = "RIGTNXUS"


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "available":   False,
        "source":      "unavailable",
        "reason":      reason,
        "report_date": None,
        "total":       None,
        "oil":         None,
        "gas":         None,
        "wow_change":  None,
    }


class BakerHughesService:
    """Baker Hughes rig count sourced from the FRED RIGTNXUS proxy series.

    Oil/gas breakdown fields are always None — see module docstring for why.
    Caching is delegated to the underlying FREDService.get_fred_series call.
    """

    def __init__(self, fred_service: FREDService) -> None:
        self._fred = fred_service

    async def get_rig_count(self) -> dict[str, Any]:
        """Return the latest U.S. rig count with week-over-week change.

        Returns:
            {
                available:   bool — False if all data sources failed,
                source:      "FRED/BakerHughes" | "unavailable",
                reason:      error description (only present when available=False),
                report_date: ISO date of the most recent observation (or None),
                total:       total rig count integer (or None),
                oil:         always None — not available via free sources,
                gas:         always None — not available via free sources,
                wow_change:  WoW change in total count (or None if < 2 obs),
            }
        """
        try:
            data = await self._fred.get_fred_series(_FRED_TOTAL_RIGS)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Baker Hughes FRED proxy unavailable: series=%s status=%s",
                _FRED_TOTAL_RIGS,
                exc.response.status_code,
            )
            return _unavailable(f"FRED returned HTTP {exc.response.status_code}")
        except Exception as exc:
            logger.warning("Baker Hughes fetch failed: %s", exc)
            return _unavailable(str(exc))

        latest_value = data.get("latest_value")
        if latest_value is None:
            return _unavailable("No parseable observations in FRED series")

        obs = data.get("observations", [])
        wow_change: float | None = None
        if len(obs) >= 2:
            prev = obs[1]["value"]
            wow_change = round(latest_value - prev, 0)

        return {
            "available":   True,
            "source":      "FRED/BakerHughes",
            "report_date": data.get("latest_date"),
            "total":       int(latest_value),
            "oil":         None,
            "gas":         None,
            "wow_change":  wow_change,
        }
