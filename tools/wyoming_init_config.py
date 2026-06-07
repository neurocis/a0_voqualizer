#!/usr/bin/env python3
"""Initialize a concrete Voqualizer Wyoming interface config (W42).

This CLI is intentionally small and deterministic so administrators and
external-device users can create `config/wyoming_interfaces.json` without using
browser setup controls. It refuses placeholder ctxIDs and preserves existing
configs unless `--overwrite` is supplied.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helpers.wyoming_config_init import init_wyoming_config  # noqa: E402
from helpers.wyoming_runtime import DEFAULT_INTERFACE_CONFIG  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize a Voqualizer Wyoming interface config")
    parser.add_argument("--ctxid", required=True, help="Agent Zero ctxID to bind 1:1 to this Wyoming interface")
    parser.add_argument("--interface", default="default", help="Wyoming interface id, default: default")
    parser.add_argument("--name", default="Voqualizer Wyoming", help="Human-friendly interface name")
    parser.add_argument("--bind-host", default="127.0.0.1", help="TCP bind host, default: 127.0.0.1")
    parser.add_argument("--bind-port", type=int, default=10701, help="TCP bind port, default: 10701")
    parser.add_argument("--config", default=str(DEFAULT_INTERFACE_CONFIG), help="Output config path")
    parser.add_argument("--disabled", action="store_true", help="Create interface disabled")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing config")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = init_wyoming_config(
        ctxid=args.ctxid,
        interface_id=args.interface,
        name=args.name,
        bind_host=args.bind_host,
        bind_port=args.bind_port,
        enabled=not args.disabled,
        config_path=args.config,
        overwrite=args.overwrite,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
