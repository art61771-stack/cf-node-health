import hashlib
import json
import random
import unittest
from unittest.mock import Mock, patch

from cf_node_health.manager import parse_candidates, post_maintenance, run_manager
from cf_node_health.models import CheckResult, Node, REGIONS

UUID = "00000000-0000-0000-0000-000000000000"


def nodes():
    return [Node(f"old-{i}.example:443", f"/old/{i}", region, i)
            for block, region in enumerate(REGIONS)
            for i in range(block * 10 + 1, block * 10 + 11)]


class ManagerTests(unittest.TestCase):
    def test_two_round_replacement_and_shape(self):
        original = nodes()
        bad_key = original[0].key
        state = {"version": 1, "failures": {bad_key: 1}}

        def checker(node):
            return CheckResult(not node.endpoint.startswith("old-1."))

        def loader(region):
            marker = {"HK": "HKG", "TW": "TPE", "SG": "SIN", "US": "LAX", "DE": "FRA"}[region]
            return "\n".join(f"198.51.{block}.{i}:443 {marker}" for block, i in [(1, 1), (1, 2)])

        out = run_manager(original, state, checker, loader, UUID, rng=random.Random(1))
        self.assertTrue(out.complete)
        self.assertEqual(1, out.report["replaced"])
        self.assertNotEqual(original[0].endpoint, out.nodes[0].endpoint)
        self.assertEqual(50, len(out.add_txt.strip().splitlines()))
        self.assertIn("path=%2Fregion-auto%2FHK01", out.add_txt)

    def test_first_failure_not_replaced(self):
        original = nodes()
        out = run_manager(original, {"failures": {}}, lambda n: CheckResult(n.index != 1),
                          lambda r: "", UUID)
        self.assertTrue(out.complete)
        self.assertEqual(original[0], out.nodes[0])
        self.assertEqual(1, out.state["failures"][original[0].key])

    def test_shortage_blocks_update(self):
        original = nodes()
        state = {"failures": {original[0].key: 1}}
        out = run_manager(original, state, lambda n: CheckResult(n.index != 1), lambda r: "", UUID)
        self.assertFalse(out.complete)
        self.assertEqual("", out.add_txt)

    def test_candidate_region_marker(self):
        values = parse_candidates("1.1.1.1:443 HKG\n2.2.2.2:443 LAX", "HK")
        self.assertEqual(["1.1.1.1:443"], values)
        self.assertEqual(["1.1.1.1:443", "2.2.2.2:443"], parse_candidates("1.1.1.1 HKG\n2.2.2.2 LAX", "US"))

    @patch("cf_node_health.manager.requests.post")
    def test_maintenance_payload(self, request):
        response = Mock()
        response.raise_for_status.return_value = None
        request.return_value = response
        post_maintenance("https://maintenance.invalid", "secret", "abc\n", {"complete": True})
        payload = request.call_args.kwargs["json"]
        self.assertEqual(hashlib.sha256(b"abc\n").hexdigest(), payload["expected_sha256"])
        self.assertEqual("Bearer secret", request.call_args.kwargs["headers"]["Authorization"])


if __name__ == "__main__":
    unittest.main()
