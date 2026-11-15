"""Diagnostic sensors for the shared AISStream connection."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import AISStreamClient
from .const import DOMAIN, STREAM_UNIQUE_ID


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create diagnostic sensors for the shared AISStream client."""
    client: AISStreamClient = entry.runtime_data
    async_add_entities(
        [
            AISStreamConnectionSensor(client, entry),
            AISStreamLastMessageSensor(client, entry),
            AISStreamMessagesReceivedSensor(client, entry),
            AISStreamPositionReportsSensor(client, entry),
        ]
    )


class AISStreamSensor(SensorEntity):
    """Base entity for AISStream communication diagnostics."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, client: AISStreamClient, entry: ConfigEntry, key: str) -> None:
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{STREAM_UNIQUE_ID}_{entry.entry_id}")},
            name="AISStream",
            manufacturer="AISStream",
            model="AIS data stream",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to communication changes after the entity is added."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._client.subscribe_communication(self.schedule_update_ha_state)
        )


class AISStreamConnectionSensor(AISStreamSensor):
    """Show whether the shared AISStream WebSocket is connected."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:connection"
    _attr_name = "Connection"
    _attr_options = ["connected", "disconnected"]

    def __init__(self, client: AISStreamClient, entry: ConfigEntry) -> None:
        super().__init__(client, entry, "connection")

    @property
    def native_value(self) -> str:
        """Return the current WebSocket state."""
        return "connected" if self._client.connected else "disconnected"

    @property
    def extra_state_attributes(self) -> dict[str, str | int | None]:
        """Return non-sensitive stream health metadata."""
        return {
            "connection_count": self._client.connection_count,
            "messages_received": self._client.messages_received,
            "position_reports_received": self._client.position_reports_received,
            "last_connected": _timestamp(self._client.last_connected_at),
            "last_disconnected": _timestamp(self._client.last_disconnected_at),
            "last_message": _timestamp(self._client.last_message_at),
            "last_error_at": _timestamp(self._client.last_error_at),
            "last_error_type": self._client.last_error_type,
        }


class AISStreamLastMessageSensor(AISStreamSensor):
    """Show when the stream most recently delivered a data message."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:message-text-clock-outline"
    _attr_name = "Last message"

    def __init__(self, client: AISStreamClient, entry: ConfigEntry) -> None:
        super().__init__(client, entry, "last_message")

    @property
    def native_value(self) -> datetime | None:
        """Return the receipt time of the most recent data message."""
        return self._client.last_message_at


class AISStreamMessagesReceivedSensor(AISStreamSensor):
    """Count all decoded AISStream data messages for this session."""

    _attr_icon = "mdi:message-arrow-down-outline"
    _attr_name = "Messages received"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, client: AISStreamClient, entry: ConfigEntry) -> None:
        super().__init__(client, entry, "messages_received")

    @property
    def native_value(self) -> int:
        """Return the number of decoded stream messages."""
        return self._client.messages_received


class AISStreamPositionReportsSensor(AISStreamSensor):
    """Count valid AIS position reports for this session."""

    _attr_icon = "mdi:map-marker-path"
    _attr_name = "Position reports received"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, client: AISStreamClient, entry: ConfigEntry) -> None:
        super().__init__(client, entry, "position_reports_received")

    @property
    def native_value(self) -> int:
        """Return the number of valid position reports."""
        return self._client.position_reports_received


def _timestamp(value: datetime | None) -> str | None:
    """Convert optional timestamps to Home Assistant state attributes."""
    return value.isoformat() if value else None