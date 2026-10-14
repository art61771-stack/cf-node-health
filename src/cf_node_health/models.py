from __future__ import annotations

from dataclasses import dataclass

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
