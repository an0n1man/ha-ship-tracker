"""UI setup and options for AIS Vessel Tracker."""

from __future__ import annotations

import asyncio
import json

from aiohttp import ClientError, WSMsgType
import probatio
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .const import (
    CONF_API_KEY,
    CONF_MAX_AGE_MINUTES,
    CONF_VESSELS,
    DEFAULT_MAX_AGE_MINUTES,
    DOMAIN,
    GLOBAL_BBOX,
    MESSAGE_TYPES,
    STREAM_UNIQUE_ID,
    STREAM_URL,
)
from .parsing import format_vessels, parse_vessels


class CannotConnect(Exception):
    """Could not reach the streaming service."""


class SubscriptionRejected(Exception):
    """The streaming service refused our subscription."""


async def verify_connection(hass, api_key: str, mmsis: list[str]) -> None:
    """Check that AISStream accepts a subscription (not vessel availability)."""
    session = async_get_clientsession(hass)
    try:
        async with session.ws_connect(
            STREAM_URL, heartbeat=30, compress=15, timeout=10
        ) as ws:
            await ws.send_json(
                {
                    "APIKey": api_key,
                    "BoundingBoxes": GLOBAL_BBOX,
                    "FiltersShipMMSI": sorted(mmsis),
                    "FilterMessageTypes": list(MESSAGE_TYPES),
                }
            )
            response = await asyncio.wait_for(ws.receive(), timeout=12)
            if response.type not in (WSMsgType.BINARY, WSMsgType.TEXT):
                raise SubscriptionRejected
            try:
                result = json.loads(response.data)
            except (ValueError, TypeError) as exc:
                raise SubscriptionRejected from exc
            if not isinstance(result, dict) or result.get("MessageType") != "SubscriptionConfirmation":
                raise SubscriptionRejected
    except (ClientError, OSError, TimeoutError) as exc:
        raise CannotConnect from exc


def _vessel_selector():
    return selector.TextSelector(
        selector.TextSelectorConfig(multiline=True, type=selector.TextSelectorType.TEXT)
    )


def _api_key_selector():
    return selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
    )


class AISVesselTrackerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the one AISStream integration entry."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Open editable vessel list and staleness settings."""
        return AISVesselTrackerOptionsFlow()

    async def async_step_user(self, user_input=None):
        """Set up API key and one or more ships."""
        errors = {}
        if user_input is not None:
            try:
                vessels = parse_vessels(user_input[CONF_VESSELS])
            except ValueError:
                errors["base"] = "invalid_mmsi"
            else:
                api_key = user_input[CONF_API_KEY].strip()
                if not api_key:
                    errors["base"] = "invalid_auth"
                else:
                    try:
                        await verify_connection(self.hass, api_key, list(vessels))
                    except CannotConnect:
                        errors["base"] = "cannot_connect"
                    except SubscriptionRejected:
                        errors["base"] = "subscription_rejected"
                    else:
                        await self.async_set_unique_id(STREAM_UNIQUE_ID)
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title="AISStream",
                            data={CONF_API_KEY: api_key, CONF_VESSELS: vessels},
                        )

        schema = probatio.Schema(
            {
                probatio.Required(CONF_API_KEY): _api_key_selector(),
                probatio.Required(CONF_VESSELS): _vessel_selector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        """Allow the AISStream API key to be replaced without deleting ships."""
        errors = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            if not api_key:
                errors["base"] = "invalid_auth"
            else:
                vessels = entry.options.get(CONF_VESSELS, entry.data[CONF_VESSELS])
                try:
                    await verify_connection(self.hass, api_key, list(vessels))
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except SubscriptionRejected:
                    errors["base"] = "subscription_rejected"
                else:
                    await self.async_set_unique_id(STREAM_UNIQUE_ID)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_API_KEY: api_key}
                    )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_API_KEY): _api_key_selector()}
            ),
            errors=errors,
        )


class AISVesselTrackerOptionsFlow(config_entries.OptionsFlowWithReload):
    """Update the list of MMSIs or maximum report age, reloading the stream."""

    async def async_step_init(self, user_input=None):
        """Show and validate the options form."""
        entry = self.config_entry
        vessels = entry.options.get(CONF_VESSELS, entry.data[CONF_VESSELS])
        age = entry.options.get(CONF_MAX_AGE_MINUTES, DEFAULT_MAX_AGE_MINUTES)
        errors = {}
        if user_input is not None:
            try:
                parsed = parse_vessels(user_input[CONF_VESSELS])
            except ValueError:
                errors["base"] = "invalid_mmsi"
            else:
                return self.async_create_entry(
                    data={
                        CONF_VESSELS: parsed,
                        CONF_MAX_AGE_MINUTES: user_input[CONF_MAX_AGE_MINUTES],
                    }
                )
        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_VESSELS, default=user_input[CONF_VESSELS] if user_input else format_vessels(vessels)
                ): _vessel_selector(),
                probatio.Required(
                    CONF_MAX_AGE_MINUTES,
                    default=user_input[CONF_MAX_AGE_MINUTES] if user_input else age,
                ): probatio.All(probatio.Coerce(int), probatio.Range(min=1, max=10080)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
