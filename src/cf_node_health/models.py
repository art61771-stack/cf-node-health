from __future__ import annotations

from dataclasses import dataclass
import math
import statistics

REGIONS = ("HK", "TW", "SG", "US", "DE")
REGION_COLO = {"HK": "HKG", "TW": "TPE", "SG": "SIN", "US": None, "DE": "FRA"}
REGION_LABEL = {
    "HK": "🇭🇰 香港",
    "TW": "🇹🇼 台湾",
    "SG": "🇸🇬 新加坡",
    "US": "🇺🇸 美国",
    "DE": "🇩🇪 德国",
}
RANGES = {region: range(i * 10 + 1, i * 10 + 11) for i, region in enumerate(REGIONS)}


@dataclass(frozen=True, slots=True)
class Node:
    endpoint: str
    path: str
    region: str
    index: int
    tag: str = ""

    @property
    def key(self) -> str:
        return f"{self.endpoint.lower()}|{self.path}"


@dataclass(frozen=True, slots=True)
class CheckResult:
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Three complete end-to-end probe outcomes for one endpoint."""

    successful: int
    attempts: int
    latency_samples: tuple[float, ...] = ()

    @property
    def eligible(self) -> bool:
        return self.attempts == 3 and self.successful == 3 and len(self.latency_samples) == 3

    @property
    def worst_latency(self) -> float:
        return max(self.latency_samples, default=math.inf)

    @property
    def p95_latency(self) -> float:
        # With exactly three samples, nearest-rank p95 is the worst sample.
        if not self.latency_samples:
            return math.inf
        ordered = sorted(self.latency_samples)
        return ordered[math.ceil(0.95 * len(ordered)) - 1]

    @property
    def median_latency(self) -> float:
        return statistics.median(self.latency_samples) if self.latency_samples else math.inf

    @property
    def rank_key(self) -> tuple[float, float, float]:
        return (-self.successful, self.p95_latency, self.median_latency)
