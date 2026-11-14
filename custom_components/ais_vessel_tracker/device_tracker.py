"""GPS device tracker entities for individual vessels."""

from __future__ import annotations

import logging

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .client import AISStreamClient
from .const import DOMAIN
from .parsing import parse_timestamp

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one device and GPS entity for each configured MMSI."""
    client: AISStreamClient = entry.runtime_data
    async_add_entities(AISVesselTracker(client, mmsi) for mmsi in client.states)


class AISVesselTracker(TrackerEntity, RestoreEntity):
    """A vessel tracked by its AIS maritime identifier."""

    _attr_should_poll = False
    _attr_entity_category = None  # Show the tracked vessel as a regular entity.
    _attr_icon = "mdi:ferry"
    _attr_location_accuracy = 0  # AIS does not provide a guaranteed meter accuracy.

    def __init__(self, client: AISStreamClient, mmsi: str) -> None:
        self._client = client
        self._mmsi = mmsi
        self._attr_unique_id = f"mmsi_{mmsi}"
        self._attr_name = client.states[mmsi].configured_name or f"Vessel {mmsi}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mmsi)},
            name=self._attr_name,
            model="AIS vessel",
        )

    @property
    def available(self) -> bool:
        """A location becomes unavailable when no fresh report exists."""
        return self._client.has_fresh_position(self._mmsi)

    @property
    def latitude(self) -> float | None:
        """Last AIS position latitude."""
        return self._client.states[self._mmsi].latitude

    @property
    def longitude(self) -> float | None:
        """Last AIS position longitude."""
        return self._client.states[self._mmsi].longitude

    @property
    def extra_state_attributes(self) -> dict:
        """Provide operational and AIS navigation data."""
        state = self._client.states[self._mmsi]
        return {
            "mmsi": self._mmsi,
            "ship_name": state.ais_name,
            "last_report": state.last_seen.isoformat() if state.last_seen else None,
            "speed_knots": state.speed_knots,
            "course_deg": state.course_deg,
            "heading_deg": state.heading_deg,
            "navigation_status": state.navigation_status,
            "stream_connected": self._client.connected,
        }

    async def async_added_to_hass(self) -> None:
        """Restore a previous fix before subscribing to live updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._client.subscribe(self._mmsi, self.schedule_update_ha_state)
        )
        old = await self.async_get_last_state()
        if old is None:
            return
        try:
            latitude = float(old.attributes["latitude"])
            longitude = float(old.attributes["longitude"])
            recorded = parse_timestamp(old.attributes.get("last_report"))
            if recorded is not None and -90 <= latitude <= 90 and -180 <= longitude <= 180:
                self._client.restore_position(self._mmsi, latitude, longitude, recorded)
        except (KeyError, TypeError, ValueError):
            _LOGGER.debug("No restorable AIS position for MMSI %s", self._mmsi)
