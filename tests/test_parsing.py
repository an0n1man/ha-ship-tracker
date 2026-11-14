"""Offline unit tests: no HA installation or API key required.

Run from the ZIP's extracted root using: python -m unittest discover -s tests -v
"""

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "ais_vessel_tracker"
PACKAGE_NAME = "_ais_test_pkg"
package = ModuleType(PACKAGE_NAME)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE_NAME] = package


def load(name):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE_NAME}.{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


load("const")
parsing = load("parsing")

# The streaming client only uses this annotation at runtime; fake it offline.
if "homeassistant.core" not in sys.modules:
    ha = ModuleType("homeassistant")
    ha.__path__ = []
    sys.modules["homeassistant"] = ha
    core = ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    sys.modules["homeassistant.core"] = core
client_module = load("client")


MMSI = "211234567"


def example_message(latitude=0.0, longitude=0.0, stamp=None, message_type="PositionReport"):
    return {
        "MessageType": message_type,
        "MetaData": {
            "MMSI": int(MMSI),
            "ShipName": "TEST BOAT",
            "time_utc": stamp or "2026-11-14 20:00:00.123456789 +0000 UTC",
            "latitude": 70.0,
            "longitude": 80.0,
        },
        "Message": {
            message_type: {
                "Valid": True,
                "UserID": int(MMSI),
                "Latitude": latitude,
                "Longitude": longitude,
                "Sog": 12.5,
                "Cog": 90.5,
                "TrueHeading": 90,
                "NavigationalStatus": 0,
            }
        },
    }


class TestParsing(unittest.TestCase):
    def test_vessel_list(self):
        vessels = parsing.parse_vessels("211234567 | My boat\n219876543\n")
        self.assertEqual(vessels, {"211234567": "My boat", "219876543": ""})
        self.assertEqual(parsing.format_vessels(vessels), "211234567 | My boat\n219876543")

    def test_reject_invalid_and_duplicate_mmsis(self):
        for value in ("", "21123", "211234567\n211234567", "211234567, 219876543", "211234567 | " + "x" * 81):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parsing.parse_vessels(value)

    def test_reject_too_many(self):
        with self.assertRaises(ValueError):
            parsing.parse_vessels("\n".join(str(100000000 + i) for i in range(201)))

    def test_zero_coordinates_and_go_timestamp(self):
        update = parsing.parse_ais_message(example_message(), {MMSI})
        self.assertTrue(update.has_position)
        self.assertEqual((update.latitude, update.longitude), (0.0, 0.0))
        self.assertEqual(update.name, "TEST BOAT")
        self.assertEqual(update.reported_at.microsecond, 123456)
        self.assertEqual(update.reported_at.utcoffset(), timedelta(0))
        self.assertEqual(update.speed_knots, 12.5)

    def test_discard_invalid_coordinates(self):
        self.assertFalse(parsing.parse_ais_message(example_message(91.0, 10), {MMSI}).has_position)
        self.assertFalse(parsing.parse_ais_message(example_message(50, 181), {MMSI}).has_position)
        self.assertFalse(parsing.parse_ais_message(example_message(float("nan"), 10), {MMSI}).has_position)

    def test_static_data_does_not_reuse_cached_position(self):
        msg = example_message()
        msg["MessageType"] = "ShipStaticData"
        msg["Message"] = {"ShipStaticData": {"Name": "NEW NAME", "UserID": int(MMSI)}}
        update = parsing.parse_ais_message(msg, {MMSI})
        self.assertFalse(update.has_position)
        self.assertEqual(update.name, "NEW NAME")

    def test_filter_mmsi(self):
        self.assertIsNone(parsing.parse_ais_message(example_message(), {"123456789"}))

    def test_class_b_report(self):
        self.assertTrue(parsing.parse_ais_message(example_message(message_type="StandardClassBPositionReport"), {MMSI}).has_position)

    def test_ais_sentinel_values(self):
        msg = example_message()
        payload = msg["Message"]["PositionReport"]
        payload["Sog"] = 102.3
        payload["Cog"] = 360
        payload["TrueHeading"] = 511
        update = parsing.parse_ais_message(msg, {MMSI})
        self.assertIsNone(update.speed_knots)
        self.assertIsNone(update.course_deg)
        self.assertIsNone(update.heading_deg)


class TestClient(unittest.TestCase):
    def setUp(self):
        self.client = client_module.AISStreamClient(None, "test-key", {MMSI: "My boat"}, 60)

    def test_subscription(self):
        sub = self.client.subscription()
        self.assertEqual(sub["FiltersShipMMSI"], [MMSI])
        self.assertIn("PositionReport", sub["FilterMessageTypes"])
        self.assertEqual(sub["APIKey"], "test-key")

    def test_incoming_and_callback(self):
        called = []
        unsubscribe = self.client.subscribe(MMSI, lambda: called.append(True))
        now = datetime.now(timezone.utc)
        self.client.process_message(example_message(stamp=now.isoformat()))
        self.assertTrue(self.client.has_fresh_position(MMSI))
        self.assertEqual(self.client.states[MMSI].latitude, 0.0)
        self.assertEqual(len(called), 1)
        unsubscribe()
        self.client.process_message(example_message(1.0, 2.0, stamp=(now + timedelta(seconds=1)).isoformat()))
        self.assertEqual(len(called), 1)

    def test_out_of_order_ignored(self):
        now = datetime.now(timezone.utc)
        self.client.process_message(example_message(1, 2, now.isoformat()))
        self.client.process_message(example_message(3, 4, (now - timedelta(minutes=5)).isoformat()))
        self.assertEqual((self.client.states[MMSI].latitude, self.client.states[MMSI].longitude), (1, 2))

    def test_expired_and_restored(self):
        self.client.restore_position(MMSI, 10, 20, datetime.now(timezone.utc) - timedelta(hours=2))
        self.assertFalse(self.client.has_fresh_position(MMSI))
        self.client.restore_position(MMSI, 11, 21, datetime.now(timezone.utc))
        self.assertTrue(self.client.has_fresh_position(MMSI))

    def test_binary_frame_decode(self):
        from aiohttp import WSMsgType
        frame = SimpleNamespace(type=WSMsgType.BINARY, data=json.dumps(example_message()).encode())
        self.assertEqual(client_module._decode_frame(frame)["MessageType"], "PositionReport")


if __name__ == "__main__":
    unittest.main()
