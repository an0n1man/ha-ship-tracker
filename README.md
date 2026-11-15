# AIS Vessel Tracker for Home Assistant

A Home Assistant custom integration that tracks multiple vessels via their 9-digit MMSIs using the AISStream.io live AIS feed. It creates **one device and one GPS `device_tracker` entity per vessel**.

Designed for Home Assistant Core **2026.11**. This is a community-style custom integration, not an official Home Assistant integration.

## Features

- One shared, compressed WebSocket connection with server-side filters for up to 200 MMSIs
- A config flow in the Home Assistant UI (no YAML configuration required)
- Friendly vessel names (optional)
- Editable vessel list and configurable maximum position age (default: 1440 minutes / 24 h)
- Separate device tracker entities and devices, with latitude/longitude
- Ship name, MMSI, UTC report timestamp, speed over ground in knots, course, heading, navigational status, and stream connection status as entity attributes
- Automatic reconnect with exponential backoff on network interruptions
- Invalid coordinates and out-of-order position reports are ignored
- Previous positions restored after restarting Home Assistant if the recorder retained the last attributes
- German and English UI strings

## Requirements

1. A Home Assistant installation with access to the public Internet (outgoing WebSocket `wss://stream.aisstream.io/v0/stream`).
2. An **AISStream.io API key** from https://aisstream.io/ .
3. A list of 9-digit MMSIs. AISStream accepts up to **200 MMSIs in one subscription**.

## Installation

1. Download and extract the ZIP.
2. Copy `custom_components/ais_vessel_tracker/` (the entire folder) into your Home Assistant configuration directory at `/config/custom_components/ais_vessel_tracker/`.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**.
5. Find **AIS Vessel Tracker** (or **AIS-Schiffsverfolgung**).
6. Paste your AISStream API key and MMSIs (one per line). You may add a friendly name after a pipe (`|`).

Example vessel list (illustrative MMSIs; replace with real vessels):

```text
211234567 | My ship
219876543 | Second ship
368207620
```

The integration checks that AISStream confirms the subscription. A confirmation does **not** imply the chosen vessels are currently transmitting or in coverage.

### Docker / Docker Compose

If the Home Assistant container maps a host directory to `/config`, place the `custom_components` folder in the mapped **host directory**, not in a temporary container layer. Then restart the container.

### HACS / Marketplace

Add `https://github.com/an0n1man/ha-ship-tracker` as an **Integration** custom repository in HACS / Marketplace, then install **AIS Vessel Tracker** from the integration list. The repository includes the HACS manifest and local brand icon required for validation.

## Changing the list of ships

Open **Settings → Devices & services → AIS Vessel Tracker → Configure**. Add/remove MMSIs or change the maximum permitted position age. The integration automatically reloads the WebSocket connection after options change.

To change the API key, use the integration's **Reconfigure** action (if exposed by your Home Assistant frontend); otherwise remove the integration and add it again.

## Entities

For each MMSI, Home Assistant creates a GPS tracker such as:

```text
device_tracker.my_ship
```

Actual entity IDs depend on the names and existing entity registry entries. The *unique ID* is `mmsi_211234567`, and the corresponding device registry identifier is `ais_vessel_tracker / 211234567`. All MMSIs have their own stable identifiers.

Example attributes after a report:

```yaml
source_type: gps
latitude: 52.1
longitude: 4.3
mmsi: "211234567"
ship_name: EXAMPLE SHIP
last_report: "2026-11-14T20:00:00+00:00"
speed_knots: 12.5
course_deg: 90.5
heading_deg: 91
navigation_status: 0
stream_connected: true
```

The example is illustrative and not an actual vessel report. GPS position and course are based only on validated AIS position reports; static-data messages cannot change coordinates. A ship with no known, sufficiently recent position has an **unavailable** tracker.

### Lovelace map

```yaml
type: map
entities:
  - entity: device_tracker.my_ship
  - entity: device_tracker.second_ship
hours_to_show: 24
```

Use the real entity IDs assigned by Home Assistant. A map card can show the ship's last position, and the history depends on your recorder retention settings.

### Stream diagnostics

The integration also creates diagnostic sensors for its shared AISStream connection:

- **Connection**: current WebSocket state, with connection/message counts and last connection, disconnection, message, and error timestamps as attributes.
- **Last message**: local receipt time of the most recent decoded AISStream message.
- **Messages received**: total decoded stream messages since the integration started.
- **Position reports received**: total valid position reports since the integration started.

The two counters reset whenever Home Assistant reloads or restarts the integration.

## Data accuracy, limitations, and privacy

- **AIS is event-driven**. There is no fixed refresh frequency, and receiving data can be delayed or stop when a ship is out of AIS reception coverage. AISStream does not guarantee historic replay or service uptime.
- The provider **requires bounding boxes even for MMSI-filtered streams**. This integration uses a global bounding box and a *server-side MMSI filter*, so it does not subscribe to data for every vessel.
- The key is stored inside Home Assistant's configuration entry. Do not paste keys in Git repositories, screenshots, or logs.
- Accuracy in meters is *not guaranteed* by the data source. This integration reports 0 for unspecified accuracy, not an assertion of perfect precision.
- AISStream allows up to three connections per account and three per source IP. Avoid running many parallel integrations using the same key/IP.
- Reports are not replayed if Home Assistant was offline; restored state may contain a previous position.
- This is **not suitable for maritime navigation or safety-critical decisions**.

Official API documentation: https://aisstream.io/documentation

## Diagnostics

To troubleshoot, enable debug logging:

```yaml
logger:
  logs:
    custom_components.ais_vessel_tracker: debug
```

Check that an AISStream `SubscriptionConfirmation` is received and that at least one configured vessel transmits an AIS position report in a covered area.

## Test

Run offline parser and state-management tests without a Home Assistant installation:

```bash
python -m unittest discover -s tests -v
```

These tests do not establish a live AISStream connection or run inside Home Assistant. A live integration test requires an API key and a Home Assistant environment.

## License

This example implementation is provided as-is. You may adapt it for your own Home Assistant setup.
