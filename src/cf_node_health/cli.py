from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .checker import VlessChecker
from .manager import CANDIDATE_URL, fetch_text, load_state, post_maintenance, run_manager
from .parser import parse_qx


def _secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"required environment variable {name} is missing")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-replace-failed", action="store_true")
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--status", default="data/status.json")
    parser.add_argument("--add", default="data/add-current.txt")
    args = parser.parse_args(argv)
    try:
        subscription_url = _secret("SUBSCRIPTION_URL")
        node_uuid = _secret("NODE_UUID")
        maintenance_url = _secret("MAINT_URL")
        maintenance_token = _secret("MAINT_TOKEN")
        nodes = parse_qx(fetch_text(subscription_url))
        state_path = Path(args.state)
        state = load_state(state_path.read_text() if state_path.exists() else None)
        checker = VlessChecker(node_uuid)
        outcome = run_manager(nodes, state, checker.check,
                              lambda region: fetch_text(CANDIDATE_URL.format(region=region)),
                              node_uuid, args.force_replace_failed)
        _write_json(state_path, outcome.state)
        _write_json(Path(args.status), outcome.report)
        if not outcome.complete:
            print("Health run incomplete: one or more anonymous region slots could not be filled.", file=sys.stderr)
            for region, count in sorted(outcome.report["shortages"].items()):
                print(f"- {region}: {count} slot(s)", file=sys.stderr)
            return 2
        Path(args.add).parent.mkdir(parents=True, exist_ok=True)
        Path(args.add).write_text(outcome.add_txt)
        post_maintenance(maintenance_url, maintenance_token, outcome.add_txt, outcome.report)
        print(f"Health run complete: healthy={outcome.report['healthy']} failed={outcome.report['failed']} replaced={outcome.report['replaced']}")
        for row in outcome.report["replacements"]:
            print(f"- {row['region']}/{row['index']:02d}: " + ("replaced" if row["replaced"] else "unfilled"))
        return 0
    except Exception as exc:
        print(f"Health run failed safely: {type(exc).__name__}. See implementation/tests; sensitive details suppressed.", file=sys.stderr)
        return 1


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
