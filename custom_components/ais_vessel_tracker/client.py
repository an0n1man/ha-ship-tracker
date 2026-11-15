"""Long-running AISStream WebSocket client with reconnection and expiry."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import random
from typing import Callable

from aiohttp import ClientError, ClientSession, WSMsgType
from homeassistant.core import HomeAssistant

from .const import GLOBAL_BBOX, MESSAGE_TYPES, STREAM_URL
from .parsing import parse_ais_message, parse_subscription_confirmation

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class VesselState:
    """Latest information about one vessel."""

    configured_name: str
    ais_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    last_seen: datetime | None = None
    speed_knots: float | None = None
    course_deg: float | None = None
    heading_deg: float | None = None
    navigation_status: int | None = None

    @property
    def display_name(self) -> str:
        return self.configured_name or self.ais_name or "Vessel"


class AISStreamClient:
    """Manage one AISStream subscription for all configured MMSIs."""

    def __init__(
        self,
        session: ClientSession,
        api_key: str,
        vessels: dict[str, str],
        max_age_minutes: int,
    ) -> None:
        self._session = session
        self._api_key = api_key
        self.states = {mmsi: VesselState(name) for mmsi, name in vessels.items()}
        self._wanted = set(self.states)
        self._max_age = timedelta(minutes=max_age_minutes)
        self._callbacks: dict[str, set[Callable[[], None]]] = defaultdict(set)
        self._communication_callbacks: set[Callable[[], None]] = set()
        self._worker: asyncio.Task[None] | None = None
        self._expiry_worker: asyncio.Task[None] | None = None
        self.connected = False
        self.compression_enabled: bool | None = None
        self.connection_count = 0
        self.messages_received = 0
        self.position_reports_received = 0
        self.last_connected_at: datetime | None = None
        self.last_disconnected_at: datetime | None = None
        self.last_message_at: datetime | None = None
        self.last_error_at: datetime | None = None
        self.last_error_type: str | None = None

    def subscription(self) -> dict:
        """Build a complete filter subscription for the stream."""
        return {
            "APIKey": self._api_key,
            "BoundingBoxes": GLOBAL_BBOX,
            "FiltersShipMMSI": sorted(self._wanted),
            "FilterMessageTypes": list(MESSAGE_TYPES),
        }

    def start(self, hass: HomeAssistant, entry_id: str) -> None:
        """Start both background tasks without blocking HA startup."""
        self._worker = hass.async_create_background_task(
            self._run(), f"ais_vessel_tracker_{entry_id}"
        )
        self._expiry_worker = hass.async_create_background_task(
            self._expiry_loop(), f"ais_vessel_tracker_expiry_{entry_id}"
        )

    async def stop(self) -> None:
        """Cancel workers, closing the WebSocket cleanly."""
        tasks = [task for task in (self._worker, self._expiry_worker) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._worker = self._expiry_worker = None
        self._set_connected(False)

    def subscribe(self, mmsi: str, callback: Callable[[], None]) -> Callable[[], None]:
        """Register an entity update callback and return an unsubscribe function."""
        callbacks = self._callbacks[mmsi]
        callbacks.add(callback)

        def unsubscribe() -> None:
            callbacks.discard(callback)

        return unsubscribe

    def subscribe_communication(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for connection and stream-message diagnostics."""
        self._communication_callbacks.add(callback)

        def unsubscribe() -> None:
            self._communication_callbacks.discard(callback)

        return unsubscribe

    def has_fresh_position(self, mmsi: str) -> bool:
        """Return True only when actual reported position is within max age."""
        state = self.states[mmsi]
        return (
            state.latitude is not None
            and state.longitude is not None
            and state.last_seen is not None
            and datetime.now(timezone.utc) - state.last_seen <= self._max_age
        )

    def restore_position(
        self, mmsi: str, latitude: float, longitude: float, last_seen: datetime
    ) -> None:
        """Restore an old position only when a newer live position is absent."""
        state = self.states[mmsi]
        if state.last_seen is None or last_seen > state.last_seen:
            state.latitude = latitude
            state.longitude = longitude
            state.last_seen = last_seen

    def process_message(self, message: dict) -> None:
        """Process one AISStream message and notify the affected tracker."""
        self.messages_received += 1
        self.last_message_at = datetime.now(timezone.utc)
        update = parse_ais_message(message, self._wanted)
        if update is None:
            self._notify_communication()
            return
        state = self.states[update.mmsi]
        changed = False
        if update.name and update.name != state.ais_name:
            state.ais_name = update.name
            changed = True
        if update.has_position:
            self.position_reports_received += 1
            # Use the observation timestamp from the service. Never let a
            # delayed/out-of-order message move the device backwards in time.
            timestamp = update.reported_at or datetime.now(timezone.utc)
            now = datetime.now(timezone.utc)
            if timestamp > now:
                timestamp = now
            if state.last_seen is None or timestamp >= state.last_seen:
                state.latitude = update.latitude
                state.longitude = update.longitude
                state.last_seen = timestamp
                state.speed_knots = update.speed_knots
                state.course_deg = update.course_deg
                state.heading_deg = update.heading_deg
                state.navigation_status = update.navigation_status
                changed = True
        if changed:
            self._notify(update.mmsi)
        self._notify_communication()

    def _notify(self, mmsi: str) -> None:
        for callback in tuple(self._callbacks[mmsi]):
            callback()

    def _notify_communication(self) -> None:
        for callback in tuple(self._communication_callbacks):
            callback()

    def _accept_subscription_confirmation(self, payload: dict | None) -> None:
        """Record compression negotiated by a documented subscription confirmation."""
        compression_enabled = parse_subscription_confirmation(payload)
        if compression_enabled is None:
            raise ConnectionError("AISStream subscription confirmation was invalid")
        self.compression_enabled = compression_enabled
        if not compression_enabled:
            _LOGGER.warning(
                "AISStream did not negotiate permessage-deflate; messages may be dropped under load"
            )

    def _set_connected(self, value: bool) -> None:
        if self.connected != value:
            self.connected = value
            now = datetime.now(timezone.utc)
            if value:
                self.connection_count += 1
                self.last_connected_at = now
            else:
                self.last_disconnected_at = now
            for mmsi in self.states:
                self._notify(mmsi)
            self._notify_communication()

    def _record_error(self, err: BaseException) -> None:
        self.last_error_at = datetime.now(timezone.utc)
        self.last_error_type = type(err).__name__
        self._notify_communication()

    async def _expiry_loop(self) -> None:
        """Update HA when a ship's report ages out without any new messages."""
        try:
            previous = {mmsi: self.has_fresh_position(mmsi) for mmsi in self.states}
            while True:
                await asyncio.sleep(60)
                for mmsi in self.states:
                    current = self.has_fresh_position(mmsi)
                    if current != previous[mmsi]:
                        self._notify(mmsi)
                        previous[mmsi] = current
        except asyncio.CancelledError:
            raise

    async def _run(self) -> None:
        """Reconnect forever with exponential backoff and occasional jitter."""
        delay = 5.0
        while True:
            try:
                async with self._session.ws_connect(
                    STREAM_URL, compress=15, heartbeat=30, timeout=10
                ) as ws:
                    # The server requires a complete subscription within 3 s.
                    await ws.send_json(self.subscription())
                    response = await asyncio.wait_for(ws.receive(), timeout=12)
                    payload = _decode_frame(response)
                    self._accept_subscription_confirmation(payload)
                    self._set_connected(True)
                    _LOGGER.info("AISStream subscribed for %s vessels", len(self.states))
                    delay = 5.0
                    async for frame in ws:
                        message = _decode_frame(frame)
                        if message is not None:
                            self.process_message(message)
                    raise ConnectionError("AISStream disconnected")
            except asyncio.CancelledError:
                raise
            except (ClientError, ConnectionError, OSError, TimeoutError, ValueError) as err:
                self._record_error(err)
                _LOGGER.warning("AISStream connection lost: %s; reconnecting", err)
            except Exception as err:
                self._record_error(err)
                _LOGGER.exception("Unexpected AISStream error; reconnecting")
            finally:
                self._set_connected(False)
            await asyncio.sleep(delay + random.uniform(0.0, min(delay * 0.2, 5.0)))
            delay = min(delay * 2.0, 300.0)


def _decode_frame(frame: object) -> dict | None:
    """Decode AISStream's binary or text JSON WebSocket frames."""
    message_type = getattr(frame, "type", None)
    if message_type not in (WSMsgType.TEXT, WSMsgType.BINARY):
        return None
    try:
        result = json.loads(frame.data)
    except (ValueError, TypeError, UnicodeError):
        return None
    return result if isinstance(result, dict) else None
