"""Validation and AIS message parsing; independent of Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
import re
from typing import Any

from .const import MAX_VESSELS, POSITION_MESSAGE_TYPES, STATIC_MESSAGE_TYPES

_MMSI_LINE_RE = re.compile(r"^(\d{9})(?:\s*[|;]\s*(.{1,80}))?$", re.UNICODE)
_GO_TIME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})"
    r"(?:\.(\d+))?\s+([+-])(\d{2})(\d{2})(?:\s+\S+)?$"
)


def parse_vessels(value: str) -> dict[str, str]:
    """Parse one MMSI per line, optionally followed by | or ; and a vessel name."""
    result: dict[str, str] = {}
    for line in value.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _MMSI_LINE_RE.fullmatch(line)
        if match is None:
            raise ValueError("Each line must be: 9-digit MMSI [| or ; Vessel name]")
        mmsi, name = match.group(1), (match.group(2) or "").strip()
        if mmsi in result:
            raise ValueError(f"Duplicate MMSI: {mmsi}")
        result[mmsi] = name
    if not result:
        raise ValueError("At least one MMSI is required")
    if len(result) > MAX_VESSELS:
        raise ValueError(f"AISStream supports at most {MAX_VESSELS} MMSIs")
    return result


def format_vessels(vessels: dict[str, str]) -> str:
    """Format vessel mapping for an editable UI text area."""
    return "\n".join(f"{mmsi} | {name}" if name else mmsi for mmsi, name in vessels.items())


def parse_timestamp(value: Any) -> datetime | None:
    """Parse either ISO 8601 or AISStream Go-format time_utc."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    match = _GO_TIME_RE.fullmatch(value.strip())
    if match is None:
        return None
    date, clock, fraction, sign, hours, minutes = match.groups()
    try:
        parsed = datetime.fromisoformat(f"{date}T{clock}")
        parsed = parsed.replace(microsecond=int(((fraction or "") + "000000")[:6]))
        offset = timedelta(hours=int(hours), minutes=int(minutes))
        if sign == "-":
            offset = -offset
        return parsed.replace(tzinfo=timezone(offset)).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def parse_subscription_confirmation(message: Any) -> bool | None:
    """Return negotiated compression from an AISStream confirmation envelope."""
    if not isinstance(message, dict) or message.get("MessageType") != "SubscriptionConfirmation":
        return None
    payload = message.get("Message")
    if not isinstance(payload, dict):
        return None
    compression_enabled = payload.get("CompressionEnabled")
    return compression_enabled if isinstance(compression_enabled, bool) else None


def _coordinate(value: Any, limit: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) and -limit <= number <= limit else None


def _value_in_range(value: Any, minimum: float, maximum: float) -> float | None:
    number = _coordinate(value, float("inf"))
    return number if number is not None and minimum <= number <= maximum else None


@dataclass(slots=True)
class AISUpdate:
    """One ship's decoded data, possibly just static metadata."""

    mmsi: str
    name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    reported_at: datetime | None = None
    speed_knots: float | None = None
    course_deg: float | None = None
    heading_deg: float | None = None
    navigation_status: int | None = None

    @property
    def has_position(self) -> bool:
        return self.latitude is not None and self.longitude is not None


def parse_ais_message(message: Any, wanted: set[str]) -> AISUpdate | None:
    """Extract an update from documented AISStream message envelopes."""
    if not isinstance(message, dict):
        return None
    message_type = message.get("MessageType")
    if message_type not in (*POSITION_MESSAGE_TYPES, *STATIC_MESSAGE_TYPES):
        return None
    metadata = message.get("MetaData")
    payload_container = message.get("Message")
    if not isinstance(metadata, dict) or not isinstance(payload_container, dict):
        return None
    payload = payload_container.get(message_type)
    if not isinstance(payload, dict):
        return None
    raw_mmsi = metadata.get("MMSI", payload.get("UserID"))
    mmsi = str(raw_mmsi)
    if mmsi not in wanted:
        return None

    name = (payload.get("Name") if message_type in STATIC_MESSAGE_TYPES else None) or metadata.get("ShipName")
    if isinstance(name, str):
        name = name.strip()
        if not name or name.upper() in {"UNKNOWN", "N/A"}:
            name = None
    else:
        name = None
    result = AISUpdate(mmsi=mmsi, name=name)
    if message_type in STATIC_MESSAGE_TYPES or payload.get("Valid") is False:
        return result

    # Use *message payload* coordinates only. Metadata may contain a cached
    # location, even for a static AIS message, and could be misleading.
    latitude = _coordinate(payload.get("Latitude"), 90.0)
    longitude = _coordinate(payload.get("Longitude"), 180.0)
    if latitude is None or longitude is None:
        return result

    result.latitude = latitude
    result.longitude = longitude
    result.reported_at = parse_timestamp(metadata.get("time_utc"))
    result.speed_knots = _value_in_range(payload.get("Sog"), 0.0, 102.2)
    result.course_deg = _value_in_range(payload.get("Cog"), 0.0, 359.9)
    result.heading_deg = _value_in_range(payload.get("TrueHeading"), 0.0, 359.0)
    status = payload.get("NavigationalStatus")
    if isinstance(status, int) and not isinstance(status, bool) and 0 <= status <= 15:
        result.navigation_status = status
    return result
