import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.instruments import load_instruments
import app.services.lightstreamer_broadcaster as ls_module

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("/instruments")
def get_instruments() -> dict:
    return load_instruments()


@router.post("/ingest")
async def ingest_tick(tick: dict[str, Any]) -> dict:
    if ls_module.broadcaster is None:
        return {"ok": False, "reason": "not initialised"}
    ls_module.broadcaster.ingest(tick)
    return {"ok": True}


@router.post("/heartbeat")
async def ingest_heartbeat() -> dict:
    if ls_module.broadcaster is None:
        return {"ok": False, "reason": "not initialised"}
    ls_module.broadcaster.heartbeat()
    return {"ok": True}


@router.websocket("/ws")
async def markets_ws(websocket: WebSocket) -> None:
    await websocket.accept()

    if ls_module.broadcaster is None:
        await websocket.send_text(
            json.dumps({"type": "error", "message": "broadcaster not initialised"})
        )
        await websocket.close()
        return

    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    ls_module.broadcaster.add_subscriber(q)

    last_live = ls_module.broadcaster.is_live()
    await websocket.send_text(
        json.dumps({"type": "feed_up" if last_live else "feed_down"})
    )

    try:
        ping_interval = 5.0
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=ping_interval)
                live = ls_module.broadcaster.is_live()
                if live != last_live:
                    await websocket.send_text(
                        json.dumps({"type": "feed_up" if live else "feed_down"})
                    )
                    last_live = live
                msg["type"] = "quote"
                await websocket.send_text(json.dumps(msg))
            except asyncio.TimeoutError:
                live = ls_module.broadcaster.is_live()
                if live != last_live:
                    await websocket.send_text(
                        json.dumps({"type": "feed_up" if live else "feed_down"})
                    )
                    last_live = live
                await websocket.send_text(json.dumps({"type": "ping"}))
    except (WebSocketDisconnect, Exception) as exc:
        if not isinstance(exc, WebSocketDisconnect):
            logger.debug("Markets WS closed: %s", exc)
    finally:
        ls_module.broadcaster.remove_subscriber(q)
