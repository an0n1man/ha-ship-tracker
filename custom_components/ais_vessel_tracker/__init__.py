"""AIS Vessel Tracker Home Assistant integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import AISStreamClient
from .const import (
    CONF_API_KEY,
    CONF_MAX_AGE_MINUTES,
    CONF_VESSELS,
    DEFAULT_MAX_AGE_MINUTES,
)

PLATFORMS = ["device_tracker", "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the stream and entities from UI configuration."""
    options = entry.options
    vessels = options.get(CONF_VESSELS, entry.data[CONF_VESSELS])
    max_age = options.get(CONF_MAX_AGE_MINUTES, DEFAULT_MAX_AGE_MINUTES)

    client = AISStreamClient(
        session=async_get_clientsession(hass),
        api_key=entry.data[CONF_API_KEY],
        vessels=vessels,
        max_age_minutes=max_age,
    )
    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    client.start(hass, entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop the stream and unload entities."""
    await entry.runtime_data.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
