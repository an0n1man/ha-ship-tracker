"""Constants for AIS Vessel Tracker."""

DOMAIN = "ais_vessel_tracker"
NAME = "AIS Vessel Tracker"
STREAM_URL = "wss://stream.aisstream.io/v0/stream"
STREAM_UNIQUE_ID = "aisstream_account"

CONF_API_KEY = "api_key"
CONF_VESSELS = "vessels"
CONF_MAX_AGE_MINUTES = "max_age_minutes"

DEFAULT_MAX_AGE_MINUTES = 1440
MAX_VESSELS = 200

# One global bounding box, with a server-side MMSI filter for a small stream.
# AISStream requires at least one BoundingBoxes entry, even when MMSIs are set.
GLOBAL_BBOX = [[[-90.0, -180.0], [90.0, 180.0]]]
POSITION_MESSAGE_TYPES = (
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "LongRangeAisBroadcastMessage",
)
STATIC_MESSAGE_TYPES = ("ShipStaticData", "StaticDataReport")
MESSAGE_TYPES = (*POSITION_MESSAGE_TYPES, *STATIC_MESSAGE_TYPES)
