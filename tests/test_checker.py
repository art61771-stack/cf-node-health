import unittest
import uuid
from unittest.mock import patch

from cf_node_health.checker import TARGET_HOST, TARGET_PORT, VlessChecker, _vless_request
from cf_node_health.models import Node

UUID = "00000000-0000-0000-0000-000000000001"


class CheckerTests(unittest.TestCase):
    def test_vless_request_layout(self):
        packet = _vless_request(UUID)
        self.assertEqual(0, packet[0])
        self.assertEqual(uuid.UUID(UUID).bytes, packet[1:17])
        self.assertIn(TARGET_HOST.encode(), packet)
        self.assertIn(TARGET_PORT.to_bytes(2, "big"), packet)

    def test_three_attempts_all_must_succeed(self):
        checker = VlessChecker(UUID)
        with patch.object(VlessChecker, "_attempt", autospec=True) as attempt:
            result = checker.check(Node("example.com:443", "/x", "HK", 1))
        self.assertTrue(result.ok)
        self.assertEqual(3, attempt.call_count)

    def test_one_failure_rejects_immediately(self):
        checker = VlessChecker(UUID)
        with patch.object(VlessChecker, "_attempt", autospec=True, side_effect=TimeoutError):
            result = checker.check(Node("example.com:443", "/x", "HK", 1))
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
