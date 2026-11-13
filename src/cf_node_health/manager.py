from __future__ import annotations

import concurrent.futures
import hashlib
import json
import random
import re
import statistics
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

import requests

from .models import BenchmarkResult, CheckResult, Node, REGIONS, REGION_COLO, REGION_LABEL, RANGES

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
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "Quantumult X"})
    response.raise_for_status()
    return response.text


def load_state(text: str | None) -> dict:
    if not text:
        return {"version": 2, "failures": {}, "rejected": []}
    state = json.loads(text)
    failures = state.get("failures", {})
    rejected = state.get("rejected", [])
    if not isinstance(failures, dict) or not isinstance(rejected, list):
        raise ValueError("invalid state")
    clean_rejected = sorted({str(x).lower() for x in rejected if re.fullmatch(r"[0-9a-f]{64}", str(x).lower())})
    return {"version": 2, "failures": {str(k): max(0, int(v)) for k, v in failures.items()},
            "rejected": clean_rejected}


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
                force_replace_failed: bool = False, rng: random.Random | None = None,
                force_slots: set[str] | None = None) -> RunOutcome:
    rng = rng or random.SystemRandom()
    previous = state.get("failures", {})
    rejected = set(state.get("rejected", []))
    endpoint_hash = lambda value: hashlib.sha256(value.lower().encode()).hexdigest()
    results = check_parallel(nodes, checker)
    failures: dict[str, int] = {}
    slot_key = lambda node: f"{node.region}:{node.index:02d}"
    replace: list[Node] = []
    check_rows: list[dict] = []
    threshold = 1 if force_replace_failed else 2
    protected_slots = {"HK:06"}
    force_slots = force_slots or set()
    for node in nodes:
        result = results[node.key]
        key = slot_key(node)
        forced = key in force_slots
        protected = key in protected_slots and not forced
        count = (0 if protected else (threshold if forced else (0 if result.ok else int(previous.get(key, 0)) + 1)))
        failures[key] = count
        if not protected and (forced or (not result.ok and count >= threshold)):
            replace.append(node)
        check_rows.append({"region": node.region, "index": node.index, "ok": (result.ok or protected) and not forced,
                           "forced": forced, "protected": protected, "failure_rounds": count})

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
        def candidate_priority(value: str) -> tuple[int, str]:
            host, _, port = value.rpartition(":")
            ipv4 = bool(re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", host))
            return (0 if ipv4 and port == "443" else 1, value.lower())
        candidates.sort(key=candidate_priority)
        available = [value for value in candidates if value.lower() not in used and endpoint_hash(value) not in rejected]
        for old in targets:
            accepted: Node | None = None
            while available:
                endpoint = available.pop(0)
                candidate = Node(endpoint, old.path, region, old.index, old.tag)
                # Prefer deterministic characteristics observed on phone-stable
                # anchors: numeric IPv4 on standard TLS 443. Other endpoints
                # remain fallback candidates for regions with small pools.
                used.add(endpoint.lower())
                if checker(candidate).ok:
                    accepted = candidate
                    break
            if accepted is None:
                shortages[region] = shortages.get(region, 0) + 1
                replacement_rows.append({"region": region, "index": old.index, "replaced": False})
            else:
                replacements[old.index] = accepted
                rejected.add(endpoint_hash(old.endpoint))
                failures[slot_key(old)] = 0
                replacement_rows.append({"region": region, "index": old.index, "replaced": True})

    final_nodes = [replacements.get(node.index, node) for node in nodes]
    complete = not shortages and _valid_shape(final_nodes)
    clean_keys = {slot_key(node) for node in final_nodes}
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
    return RunOutcome(final_nodes, {"version": 2, "failures": clean_failures, "rejected": sorted(rejected)}, report,
                      render_add(final_nodes, node_uuid) if complete else "", complete)


def run_rebuild_best(nodes: list[Node], candidate_loader: Callable[[str], str],
                     benchmark: Callable[[Node], BenchmarkResult], node_uuid: str,
                     max_workers: int = 12, pool_limit: int = 100) -> RunOutcome:
    """Benchmark a bounded full pool and atomically select the best ten per region."""
    if not 1 <= max_workers <= 12:
        raise ValueError("rebuild max_workers must be between 1 and 12")
    if not 10 <= pool_limit <= 100:
        raise ValueError("pool_limit must be between 10 and 100")

    pools: dict[str, list[Node]] = {}
    for region in REGIONS:
        endpoints: list[str] = []
        seen: set[str] = set()
        # Preserve current endpoints in the pool, then fill from the source.
        values = [n.endpoint for n in nodes if n.region == region]
        values.extend(parse_candidates(candidate_loader(region), region))
        for endpoint in values:
            key = endpoint.lower()
            if key in seen:
                continue
            seen.add(key)
            endpoints.append(endpoint)
            if len(endpoints) >= pool_limit:
                break
        first_index = RANGES[region].start
        pools[region] = [Node(endpoint, f"/region-auto/{region}{first_index:02d}", region, first_index)
                         for endpoint in endpoints]

    all_candidates = [candidate for region in REGIONS for candidate in pools[region]]
    measured: dict[tuple[str, str], BenchmarkResult] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(benchmark, node): (node.region, node.endpoint.lower())
                   for node in all_candidates}
        for future in concurrent.futures.as_completed(futures):
            measured[futures[future]] = future.result()

    selected: list[Node] = []
    used: set[str] = set()
    region_rows: list[dict] = []
    shortages: dict[str, int] = {}
    for region in REGIONS:
        ranked = sorted(
            pools[region],
            key=lambda node: (measured[(region, node.endpoint.lower())].rank_key, node.endpoint.lower()),
        )
        eligible = [node for node in ranked if measured[(region, node.endpoint.lower())].eligible]
        chosen: list[Node] = []
        for node in eligible:
            endpoint_key = node.endpoint.lower()
            if endpoint_key in used:
                continue
            index = RANGES[region].start + len(chosen)
            chosen.append(Node(node.endpoint, f"/region-auto/{region}{index:02d}", region, index))
            used.add(endpoint_key)
            if len(chosen) == 10:
                break
        selected.extend(chosen)
        if len(chosen) < 10:
            shortages[region] = 10 - len(chosen)
        chosen_results = [measured[(region, node.endpoint.lower())] for node in chosen]
        region_rows.append({
            "region": region,
            "pool_checked": len(pools[region]),
            "eligible": len(eligible),
            "selected": len(chosen),
            "selected_latency_ms": _latency_summary(chosen_results),
        })

    complete = not shortages and _valid_shape(selected)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "rebuild_best",
        "regions": region_rows,
        "shortages": shortages,
        "complete": complete,
    }
    state = {"version": 1, "failures": {f"{n.region}:{n.index:02d}": 0 for n in selected}} if complete else {
        "version": 1, "failures": {}
    }
    return RunOutcome(selected, state, report, render_add(selected, node_uuid) if complete else "", complete)


def _latency_summary(results: list[BenchmarkResult]) -> dict | None:
    if not results:
        return None
    worst = [result.worst_latency * 1000 for result in results]
    medians = [result.median_latency * 1000 for result in results]
    return {
        "worst_max": round(max(worst), 1),
        "worst_median": round(statistics.median(worst), 1),
        "median_median": round(statistics.median(medians), 1),
    }


def _valid_shape(nodes: list[Node]) -> bool:
    return len(nodes) == 50 and {(n.region, n.index) for n in nodes} == {
        (region, index) for region, values in RANGES.items() for index in values
    } and len({n.endpoint.lower() for n in nodes}) == 50


def render_add(nodes: list[Node], node_uuid: str) -> str:
    lines: list[str] = []
    for node in sorted(nodes, key=lambda n: n.index):
        endpoint = node.endpoint
        label = f"{REGION_LABEL[node.region]} {node.index:02d}"
        canonical_path = node.path
        if not canonical_path.startswith('/') or '\r' in canonical_path or '\n' in canonical_path:
            raise ValueError('invalid websocket path')
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
