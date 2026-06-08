#!/usr/bin/env python3
"""Wyoming live admin capture (W52).

Captures a consolidated, pasteable JSON diagnostic bundle by calling the
running Agent Zero framework's `wyoming_status` admin endpoint over HTTP. Unlike
`wyoming_live_smoke_capture.py` (W51), which inspects the on-disk config and
static helpers without talking to the framework, this tool reports the actual
live runtime state as seen by the framework process.

Calls these admin actions (best-effort, skipped on failure):

  - status
  - dom_integration
  - validate
  - readiness
  - smoke
  - checklist

Authentication: supports `--cookie` for session cookies and `--csrf-token` for
the framework CSRF header. If neither is provided, expect HTTP 302 -> /login.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 80
ENDPOINT = "/api/plugins/a0_voqualizer/wyoming_status"


def _post_action(
    base_url: str,
    payload: dict[str, Any],
    *,
    cookie: str | None,
    csrf_token: str | None,
    timeout: float,
) -> dict[str, Any]:
    """POST one wyoming_status action and return a normalized result envelope."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}{ENDPOINT}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if cookie:
        req.add_header("Cookie", cookie)
    if csrf_token:
        req.add_header("X-CSRF-Token", csrf_token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {
            "ok": False,
            "http_status": exc.code,
            "error": "http_error",
            "reason": str(exc.reason),
            "raw": raw[:2048],
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "error": "connection_failed", "reason": str(exc)}
    try:
        parsed = json.loads(raw) if raw else {}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "http_status": status,
            "error": "json_decode_failed",
            "reason": str(exc),
            "raw": raw[:2048],
        }
    if isinstance(parsed, dict):
        parsed.setdefault("http_status", status)
    return parsed if isinstance(parsed, dict) else {"ok": True, "http_status": status, "data": parsed}


def capture(
    *,
    host: str,
    port: int,
    scheme: str,
    cookie: str | None,
    csrf_token: str | None,
    interface_id: str,
    tcp_describe: bool,
    timeout: float,
) -> dict[str, Any]:
    base_url = f"{scheme}://{host}:{port}"
    bundle: dict[str, Any] = {
        "ok": True,
        "tool": "wyoming_live_admin_capture",
        "endpoint": f"{base_url}{ENDPOINT}",
        "interface_id": interface_id or None,
        "tcp_describe": tcp_describe,
        "authenticated": bool(cookie or csrf_token),
        "actions": {},
        "blockers": [],
        "next_actions": [],
    }
    actions = [
        ("status", {"action": "status"}),
        ("dom_integration", {"action": "dom_integration"}),
        ("validate", {"action": "validate"}),
        (
            "readiness",
            {
                "action": "readiness",
                "interface_id": interface_id or "",
                "tcp_describe": tcp_describe,
            },
        ),
        (
            "smoke",
            {
                "action": "smoke",
                "interface_id": interface_id or "",
                "tcp_describe": tcp_describe,
            },
        ),
        (
            "checklist",
            {
                "action": "checklist",
                "interface_id": interface_id or "",
                "tcp_describe": tcp_describe,
            },
        ),
    ]
    for name, payload in actions:
        result = _post_action(
            base_url,
            payload,
            cookie=cookie,
            csrf_token=csrf_token,
            timeout=timeout,
        )
        bundle["actions"][name] = result
        if result.get("error") == "connection_failed":
            bundle["blockers"].append(f"{name}: framework_unreachable")
        elif result.get("http_status") in (301, 302, 303, 307, 308):
            bundle["blockers"].append(f"{name}: auth_required")
        elif result.get("http_status") == 500:
            bundle["blockers"].append(f"{name}: server_error_500")
        elif isinstance(result, dict) and result.get("ok") is False and "http_status" in result:
            # Action-level failure surfaced by the endpoint.
            bundle["blockers"].append(f"{name}: action_failed")

    # Synthesize next actions.
    if "framework_unreachable" in " ".join(bundle["blockers"]):
        bundle["ok"] = False
        bundle["next_actions"].append(
            "Verify the Agent Zero framework is running on the chosen host/port."
        )
    if "auth_required" in " ".join(bundle["blockers"]):
        bundle["ok"] = False
        bundle["next_actions"].append(
            "Re-run with --cookie 'session=...' and --csrf-token '...' captured from an authenticated browser session."
        )
    if "server_error_500" in " ".join(bundle["blockers"]):
        bundle["ok"] = False
        bundle["next_actions"].append(
            "Inspect framework logs for tracebacks under wyoming_status; check api/wyoming_status.py imports."
        )
    if not bundle["blockers"]:
        bundle["next_actions"].append(
            "Live admin capture succeeded. Paste this JSON when reporting Wyoming runtime issues."
        )
    return bundle


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Wyoming live admin capture (W52).")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--scheme", default="http", choices=["http", "https"])
    p.add_argument("--cookie", default=None, help="Raw Cookie header value")
    p.add_argument("--csrf-token", default=None)
    p.add_argument("--interface-id", default="")
    p.add_argument("--tcp-describe", action="store_true")
    p.add_argument("--timeout", type=float, default=5.0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    bundle = capture(
        host=args.host,
        port=args.port,
        scheme=args.scheme,
        cookie=args.cookie,
        csrf_token=args.csrf_token,
        interface_id=args.interface_id,
        tcp_describe=args.tcp_describe,
        timeout=args.timeout,
    )
    json.dump(bundle, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if bundle.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
