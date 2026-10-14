from __future__ import annotations

import re
import urllib.parse

from .models import Node, RANGES

_FIELDS = re.compile(r"\s*,\s*")
_REGION_WORDS = {
    "HK": ("香港", "hong kong", " hk", "hk ", "🇭🇰"),
    "TW": ("台湾", "台灣", "taiwan", " tw", "tw ", "🇹🇼"),
    "SG": ("新加坡", "singapore", " sg", "sg ", "🇸🇬"),
    "US": ("美国", "美國", "united states", " usa", " us", "us ", "🇺🇸"),
    "DE": ("德国", "德國", "germany", " de", "de ", "🇩🇪"),
}


def _region_and_index(tag: str) -> tuple[str, int]:
    lower = f" {tag.lower()} "
    region = next((r for r, words in _REGION_WORDS.items() if any(w in lower for w in words)), "")
    numbers = [int(x) for x in re.findall(r"(?<!\d)(\d{1,2})(?!\d)", tag)]
    index = numbers[-1] if numbers else 0
    if not region and index:
        region = next((r for r, values in RANGES.items() if index in values), "")
    if region and not index:
        raise ValueError(f"tag lacks index for region {region}")
    if not region or index not in RANGES[region]:
        raise ValueError("tag cannot be mapped to required region/index")
    return region, index


def parse_qx(text: str) -> list[Node]:
    nodes: list[Node] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")) or not line.lower().startswith("vless ="):
            continue
        parts = _FIELDS.split(line)
        endpoint = parts[0].split("=", 1)[1].strip()
        fields: dict[str, str] = {}
        for part in parts[1:]:
            if "=" in part:
                key, value = part.split("=", 1)
                fields[key.strip().lower()] = value.strip()
        if fields.get("obfs", "").lower() != "wss":
            raise ValueError("all managed nodes must use obfs=wss")
        for required in ("password", "obfs-host", "obfs-uri", "tag"):
            if not fields.get(required):
                raise ValueError(f"missing QX field: {required}")
        region, index = _region_and_index(fields["tag"])
        nodes.append(Node(endpoint, fields["obfs-uri"], region, index, fields["tag"]))
    if len(nodes) != 50:
        raise ValueError(f"subscription must contain exactly 50 VLESS nodes, got {len(nodes)}")
    expected = {(region, index) for region, values in RANGES.items() for index in values}
    actual = {(n.region, n.index) for n in nodes}
    if actual != expected or len(actual) != len(nodes):
        raise ValueError("subscription must contain every required region/index exactly once")
    return sorted(nodes, key=lambda n: n.index)


def split_endpoint(endpoint: str) -> tuple[str, int]:
    value = endpoint.strip()
    parsed = urllib.parse.urlsplit("//" + value)
    if not parsed.hostname:
        raise ValueError("invalid endpoint")
    return parsed.hostname, parsed.port or 443
