from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import unittest

from dashboard.render import (
    lovelace_view_yaml,
    lovelace_yaml,
    telemetry_json,
    validate_mapping,
)


ROOT = Path(__file__).parents[1]
SAMPLE_PATH = ROOT / "dashboard/chengde.sample.json"


def sample() -> dict:
    return json.loads(SAMPLE_PATH.read_text())


class DashboardRendererTests(unittest.TestCase):
    def test_non_fourteen_channels_local_ping_statistics_and_center_contract(self) -> None:
        mapping = sample()
        self.assertEqual([3, 8, 21], [item["channel_id"] for item in mapping["channels"]])
        yaml = lovelace_yaml(mapping)
        self.assertEqual(3, yaml.count("type: entities"))
        self.assertIn("NVR / Recording", yaml)
        self.assertIn("Ping / Network", yaml)
        self.assertIn("Diagnostics", yaml)
        self.assertIn("Sample East - Live video", yaml)
        self.assertIn("Sample East - Reachable", yaml)
        self.assertIn("type: statistic", yaml)
        self.assertIn("Sample East - 延遲/時", yaml)
        self.assertIn("Sample East - 丟包率/日", yaml)
        self.assertIn("rolling_window", yaml)
        self.assertIn("hours: 1", yaml)
        self.assertIn("hours: 24", yaml)
        self.assertIn("stat_type: mean", yaml)
        self.assertIn("name: \"CPU\"", yaml)
        self.assertNotIn("name: \"sensor.sample_cpu\"", yaml)
        telemetry = json.loads(telemetry_json(mapping))
        self.assertEqual(telemetry, validate_mapping(mapping)["telemetry"])
        self.assertNotIn("ha.ping", {item["kind"] for item in telemetry})
        self.assertTrue(all(item["channel_id"] is None for item in telemetry))

        view_yaml = lovelace_view_yaml(mapping)
        self.assertTrue(view_yaml.startswith('title: "承德"\npath: "chengde"\ncards:\n'))
        self.assertNotIn("\nviews:", view_yaml)
        self.assertIn("type: statistic", view_yaml)

    def test_empty_diagnostics_does_not_render_empty_card(self) -> None:
        mapping = sample()
        mapping["diagnostics"] = []
        yaml = lovelace_view_yaml(mapping)
        self.assertNotIn('title: "Diagnostics"', yaml)
        self.assertIn('title: "NVR / Recording"', yaml)
        self.assertIn('title: "Ping / Network"', yaml)

    def test_rejects_duplicate_unsafe_and_snapshot_input_without_echo(self) -> None:
        cases = []
        duplicate = sample()
        duplicate["channels"][1]["channel_id"] = 3
        cases.append(duplicate)
        target = sample()
        target["channels"][1]["ping"][0]["channel_id"] = 8
        target["channels"][1]["ping"].append(deepcopy(target["channels"][1]["ping"][0]))
        target["channels"][1]["ping"][1]["entity_id"] = "binary_sensor.sample_other"
        cases.append(target)
        unsafe = sample()
        unsafe["channels"][0]["label"] = "snapshot forbidden"
        cases.append(unsafe)
        ipv6 = sample()
        ipv6["display_name"] = "2001:db8::1"
        cases.append(ipv6)
        auth = sample()
        auth["display_name"] = "Basic sample"
        cases.append(auth)
        encoded = sample()
        encoded["display_name"] = "A" * 80
        cases.append(encoded)
        for value in cases:
            with self.subTest(value=value["site_id"]):
                with self.assertRaisesRegex(ValueError, "^invalid mapping$"):
                    validate_mapping(value)

    def test_sample_is_synthetic_and_private_pattern_is_ignored(self) -> None:
        text = SAMPLE_PATH.read_text().lower()
        self.assertNotIn("192.168.", text)
        self.assertNotIn("password", text)
        self.assertNotIn("jpeg", text)
        self.assertIn("dashboard/*.private.json", (ROOT / ".gitignore").read_text())

    def test_output_is_independent_of_input_order(self) -> None:
        ordered = sample()
        original = json.dumps(ordered, sort_keys=True)
        shuffled = sample()
        shuffled["channels"].reverse()
        shuffled["diagnostics"].reverse()
        for channel in shuffled["channels"]:
            channel["ping"].reverse()
            channel["nvr_entities"] = dict(reversed(list(channel["nvr_entities"].items())))
        self.assertEqual(telemetry_json(ordered), telemetry_json(shuffled))
        self.assertEqual(lovelace_yaml(ordered), lovelace_yaml(shuffled))
        validate_mapping(ordered)
        self.assertEqual(original, json.dumps(ordered, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
