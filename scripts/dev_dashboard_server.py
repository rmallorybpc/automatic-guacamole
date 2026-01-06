#!/usr/bin/env python3
"""Dev server for the dashboard pages with a "refresh" endpoint.

Why this exists:
- The dashboard HTML pages in `docs/` are static and only *render* JSON.
- Regenerating JSON requires running Python scripts (and often GitHub API calls),
  which cannot happen on static hosting or `python -m http.server`.

This server:
- Serves `docs/` as static files.
- Exposes POST endpoints to run the existing generator scripts.

Endpoints:
- POST /__refresh_all  -> runs scripts/build_dashboard_from_repo_issues.py
- POST /__refresh_meta -> runs scripts/build_dashboard_from_meta_issue.py
- POST /__refresh_both -> runs both (meta then all)

Environment:
- Passes through current environment (e.g. GITHUB_TOKEN).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from socketserver import ThreadingTCPServer
from pathlib import Path
from typing import Any, Dict, Tuple


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_script(script_rel: str, timeout_s: int) -> Dict[str, Any]:
    root = _repo_root()
    script_path = root / script_rel
    if not script_path.exists():
        return {
            "ok": False,
            "returncode": 2,
            "script": script_rel,
            "stdout": "",
            "stderr": f"Missing script: {script_path}",
        }

    try:
        cp = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(root),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return {
            "ok": cp.returncode == 0,
            "returncode": cp.returncode,
            "script": script_rel,
            "stdout": cp.stdout,
            "stderr": cp.stderr,
        }
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        err = (e.stderr or "") if isinstance(e.stderr, str) else ""
        return {
            "ok": False,
            "returncode": 124,
            "script": script_rel,
            "stdout": out,
            "stderr": (err + "\n" if err else "") + f"Timed out after {timeout_s}s.",
        }


class DashboardDevHandler(SimpleHTTPRequestHandler):
    # Python's SimpleHTTPRequestHandler supports the `directory` kwarg.

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path not in {"/__refresh_all", "/__refresh_meta", "/__refresh_both"}:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "error": f"Unknown endpoint: {self.path}"},
            )
            return

        timeout_s = getattr(self.server, "refresh_timeout_s", 120)

        if self.path == "/__refresh_all":
            results = [_run_script("scripts/build_dashboard_from_repo_issues.py", timeout_s)]
        elif self.path == "/__refresh_meta":
            results = [_run_script("scripts/build_dashboard_from_meta_issue.py", timeout_s)]
        else:
            results = [
                _run_script("scripts/build_dashboard_from_meta_issue.py", timeout_s),
                _run_script("scripts/build_dashboard_from_repo_issues.py", timeout_s),
            ]

        ok = all(r.get("ok") for r in results)
        status = HTTPStatus.OK if ok else HTTPStatus.INTERNAL_SERVER_ERROR

        self._send_json(
            status,
            {
                "ok": ok,
                "results": results,
                "hint": "Set GITHUB_TOKEN to avoid GitHub API rate limits.",
            },
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Serve docs/ and provide dashboard refresh endpoints.")
    p.add_argument("--bind", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    p.add_argument(
        "--docs-dir",
        default=str(_repo_root() / "docs"),
        help="Directory to serve (default: <repo>/docs)",
    )
    p.add_argument(
        "--refresh-timeout",
        type=int,
        default=120,
        help="Max seconds allowed for each refresh script (default: 120)",
    )

    args = p.parse_args(argv)

    docs_dir = Path(args.docs_dir).resolve()
    if not docs_dir.exists() or not docs_dir.is_dir():
        print(f"docs dir not found: {docs_dir}", file=sys.stderr)
        return 2

    handler = lambda *h_args, **h_kwargs: DashboardDevHandler(  # noqa: E731
        *h_args, directory=str(docs_dir), **h_kwargs
    )

    with ThreadingTCPServer((args.bind, args.port), handler) as httpd:
        httpd.refresh_timeout_s = args.refresh_timeout
        url = f"http://{args.bind}:{args.port}/"
        print("Serving dashboard at:")
        print(f"  {url} (redirects to issues.html)")
        print(f"  {url}issues.html")
        print(f"  {url}issue-223.html")
        print("Refresh endpoints:")
        print(f"  POST {url}__refresh_all")
        print(f"  POST {url}__refresh_meta")
        print(f"  POST {url}__refresh_both")
        httpd.serve_forever()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
