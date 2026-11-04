import unittest

from cf_node_health.manager import run_rebuild_best
from cf_node_health.models import BenchmarkResult, Node, REGIONS

UUID = "00000000-0000-0000-0000-000000000000"
MARKERS = {"HK": "HKG", "TW": "TPE", "SG": "SIN", "US": "LAX", "DE": "FRA"}


def current_nodes():
    return [Node(f"old-{region.lower()}-{i}.example:443", f"/old/{i}", region, i)
            for block, region in enumerate(REGIONS)
            for i in range(block * 10 + 1, block * 10 + 11)]


def sources(count=12):
    def load(region):
        return "\n".join(f"198.{block}.{n // 250}.{n % 250}:443 {MARKERS[region]}"
                         for block, r in enumerate(REGIONS, 10) if r == region
                         for n in range(1, count + 1))
    return load


class BenchmarkRankingTests(unittest.TestCase):
    def test_stability_beats_latency(self):
        stable = BenchmarkResult(3, 3, (1.0, 1.1, 1.2))
        flaky_fast = BenchmarkResult(2, 3, (0.01, 0.02))
        self.assertLess(stable.rank_key, flaky_fast.rank_key)
        self.assertTrue(stable.eligible)
        self.assertFalse(flaky_fast.eligible)

    def test_latency_ties_use_median(self):
        lower_median = BenchmarkResult(3, 3, (0.1, 0.2, 1.0))
        higher_median = BenchmarkResult(3, 3, (0.1, 0.8, 1.0))
        self.assertEqual(lower_median.p95_latency, higher_median.p95_latency)
        self.assertLess(lower_median.rank_key, higher_median.rank_key)

    def test_shortage_fails_closed(self):
        def benchmark(node):
            # Only nine successful endpoints in HK; all other regions have enough.
            if node.region == "HK" and node.endpoint.startswith("old-hk-10"):
                return BenchmarkResult(0, 3)
            if node.region == "HK" and node.endpoint.startswith("198."):
                return BenchmarkResult(0, 3)
            return BenchmarkResult(3, 3, (0.1, 0.1, 0.1))

        out = run_rebuild_best(current_nodes(), sources(), benchmark, UUID)
        self.assertFalse(out.complete)
        self.assertEqual("", out.add_txt)
        self.assertEqual(1, out.report["shortages"]["HK"])
        self.assertNotIn("endpoint", str(out.report).lower())

    def test_complete_shape_fixed_slots_and_global_uniqueness(self):
        def benchmark(node):
            suffix = sum(node.endpoint.encode()) % 50
            latency = 0.05 + suffix / 1000
            return BenchmarkResult(3, 3, (latency, latency + 0.01, latency + 0.02))

        out = run_rebuild_best(current_nodes(), sources(), benchmark, UUID)
        self.assertTrue(out.complete)
        self.assertEqual(list(range(1, 51)), [node.index for node in out.nodes])
        self.assertEqual(50, len({node.endpoint.lower() for node in out.nodes}))
        self.assertEqual(50, len(out.add_txt.strip().splitlines()))
        self.assertEqual([10] * 5, [row["selected"] for row in out.report["regions"]])
        self.assertTrue(all(row["pool_checked"] == 22 for row in out.report["regions"]))


if __name__ == "__main__":
    unittest.main()
