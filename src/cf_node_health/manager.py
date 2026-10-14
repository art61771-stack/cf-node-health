from __future__ import annotations

import concurrent.futures
import hashlib
import json
import random
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

import requests

from .models import CheckResult, Node, REGIONS, REGION_COLO, REGION_LABEL, RANGES

CANDIDATE_URL = "https://raw.githubusercontent.com/wanwushequ/cfyxip/main/random-region/{region}/100.txt"
_ENDPOINT_RE = re.compile(r"(?<![\w.-])(\[[0-9a-fA-F:]+\]|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}|(?:\d{1,3}\.){3}\d{1,3})(?::(\d{1,5}))?")


@dataclass(slots=True)
class RunOutcome:
    nodes: list[Node]
    state: dict
    report: dict
    add_txt: str
    complete: bool


def fetch_text(url: str, timeout: float = 20.0) -> str:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "cf-node-health/1"})
    response.raise_for_status()
    return response.text


def load_state(text: str | None) -> dict:
    if not text:
        return {"version": 1, "failures": {}}
    state = json.loads(text)
    failures = state.get("failures", {})
    if not isinstance(failures, dict):
        raise ValueError("invalid state failures")
    return {"version": 1, "failures": {str(k): max(0, int(v)) for k, v in failures.items()}}


def parse_candidates(text: str, region: str) -> list[str]:
    marker = REGION_COLO[region]
    result: list[str] = []
    for line in text.splitlines():
        if marker and marker.upper() not in line.upper():
            continue
        match = _ENDPOINT_RE.search(line)
        if not match:
            continue
        host, port = match.group(1), match.group(2) or "443"
        endpoint = f"{host}:{port}"
        if endpoint not in result:
            result.append(endpoint)
    return result


def check_parallel(nodes: Iterable[Node], check: Callable[[Node], CheckResult], max_workers: int = 8) -> dict[str, CheckResult]:
    items = list(nodes)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(check, node): node.key for node in items}
        return {futures[future]: future.result() for future in concurrent.futures.as_completed(futures)}


def run_manager(nodes: list[Node], state: dict, checker: Callable[[Node], CheckResult],
                candidate_loader: Callable[[str], str], node_uuid: str,
                force_replace_failed: bool = False, rng: random.Random | None = None) -> RunOutcome:
    rng = rng or random.SystemRandom()
    previous = state.get("failures", {})
    results = check_parallel(nodes, checker)
    failures: dict[str, int] = {}
    replace: list[Node] = []
    check_rows: list[dict] = []
    threshold = 1 if force_replace_failed else 2
    for node in nodes:
        result = results[node.key]
        count = 0 if result.ok else int(previous.get(node.key, 0)) + 1
        failures[node.key] = count
        if not result.ok and count >= threshold:
            replace.append(node)
        check_rows.append({"region": node.region, "index": node.index, "ok": result.ok, "failure_rounds": count})

    used = {node.endpoint.lower() for node in nodes}
    replacements: dict[int, Node] = {}
    replacement_rows: list[dict] = []
    shortages: dict[str, int] = {}
    for region in REGIONS:
        targets = [node for node in replace if node.region == region]
        if not targets:
            continue
        candidates = parse_candidates(candidate_loader(region), region)
        rng.shuffle(candidates)
        available = [value for value in candidates if value.lower() not in used]
        for old in targets:
            accepted: Node | None = None
            while available:
                endpoint = available.pop()
                candidate = Node(endpoint, f"/region-auto/{region}{old.index:02d}", region, old.index, old.tag)
                used.add(endpoint.lower())
                if checker(candidate).ok:
                    accepted = candidate
                    break
            if accepted is None:
                shortages[region] = shortages.get(region, 0) + 1
                replacement_rows.append({"region": region, "index": old.index, "replaced": False})
            else:
                replacements[old.index] = accepted
                failures.pop(old.key, None)
                failures[accepted.key] = 0
                replacement_rows.append({"region": region, "index": old.index, "replaced": True})

    final_nodes = [replacements.get(node.index, node) for node in nodes]
    complete = not shortages and _valid_shape(final_nodes)
    clean_keys = {node.key for node in final_nodes}
    clean_failures = {key: failures.get(key, 0) for key in sorted(clean_keys)}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "healthy": sum(1 for row in check_rows if row["ok"]),
        "failed": sum(1 for row in check_rows if not row["ok"]),
        "replaced": sum(1 for row in replacement_rows if row["replaced"]),
        "checks": check_rows,
        "replacements": replacement_rows,
        "shortages": shortages,
        "complete": complete,
    }
    return RunOutcome(final_nodes, {"version": 1, "failures": clean_failures}, report,
                      render_add(final_nodes, node_uuid) if complete else "", complete)


def _valid_shape(nodes: list[Node]) -> bool:
    return len(nodes) == 50 and {(n.region, n.index) for n in nodes} == {
        (region, index) for region, values in RANGES.items() for index in values
    } and len({n.endpoint.lower() for n in nodes}) == 50


def render_add(nodes: list[Node], node_uuid: str) -> str:
    lines: list[str] = []
    for node in sorted(nodes, key=lambda n: n.index):
        endpoint = node.endpoint
        label = f"{REGION_LABEL[node.region]} {node.index:02d}"
        canonical_path = f"/region-auto/{node.region}{node.index:02d}"
        query = urllib.parse.urlencode({
            "encryption": "none", "security": "tls", "sni": "quiet-meadow-pages-qx.pages.dev",
            "type": "ws", "host": "quiet-meadow-pages-qx.pages.dev", "path": canonical_path,
        })
        lines.append(f"vless://{node_uuid}@{endpoint}?{query}#{urllib.parse.quote(label)}")
    return "\n".join(lines) + "\n"


def post_maintenance(url: str, token: str, add_txt: str, report: dict, timeout: float = 30.0) -> None:
    digest = hashlib.sha256(add_txt.encode()).hexdigest()
    response = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                             json={"add_txt": add_txt, "expected_sha256": digest, "report": report}, timeout=timeout)
    response.raise_for_status()
