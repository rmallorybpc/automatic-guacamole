#!/usr/bin/env python3
"""Generate an Issues dashboard JSON from a GitHub meta issue.

This script is designed for Repo B (automatic-guacamole). It fetches a meta issue
(default: githubpartners/microsoft-learn#223), parses its body into category
buckets, extracts referenced issue numbers, fetches each issue (best-effort), and
writes a dashboard JSON file matching the schema expected by the dashboard UI in
Repo A.

Stdlib only: argparse, datetime, json, os, re, urllib.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


GITHUB_API_BASE = "https://api.github.com"
DEFAULT_REPO = "githubpartners/microsoft-learn"
DEFAULT_ISSUE_NUMBER = 223
DEFAULT_OUT = "docs/reports/dashboard_issue_223.json"

KNOWN_CATEGORIES = [
    "Grammar/Spelling",
    "Deprecated/Potentially Outdated Content",
    "Suggested Content Updates",
    "Other (Support/Ambiguous/Process)",
    "Placeholders (Template-Incomplete)",
]


@dataclass(frozen=True)
class Feature:
    issue_number: int
    id: str
    title: str
    source_type: str
    product_area: str
    source_url: str
    date_discovered: str


def _iso_now_utc() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso_datetime(value: str) -> dt.datetime:
    v = (value or "").strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    return dt.datetime.fromisoformat(v)


def _date_yyyy_mm_dd(iso_dt: str) -> str:
    try:
        return _parse_iso_datetime(iso_dt).date().isoformat()
    except Exception:
        return (iso_dt or "")[:10] if iso_dt and len(iso_dt) >= 10 else "1970-01-01"


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def _normalize_key(s: str) -> str:
    return _normalize_ws(s).casefold()


def _make_request(url: str, token: Optional[str]) -> urllib.request.Request:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "automatic-guacamole-issues-dashboard-builder",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def github_get_json(url: str, token: Optional[str]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    req = _make_request(url, token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            headers = {k: v for k, v in resp.headers.items()}
            return data, headers
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        msg = f"HTTP {e.code} for {url}"
        if raw:
            msg += f"; body={raw[:2000]}"
        raise RuntimeError(msg) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error for {url}: {e}") from e


def fetch_issue(repo: str, issue_number: int, token: Optional[str]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{issue_number}"
    return github_get_json(url, token)


_ISSUE_PATH_RE = re.compile(r"/issues/(?P<num>\d+)\b")


def extract_issue_numbers(text: str) -> Set[int]:
    nums: Set[int] = set()
    for m in _ISSUE_PATH_RE.finditer(text or ""):
        try:
            nums.add(int(m.group("num")))
        except ValueError:
            continue
    return nums


def _strip_md_decorations(s: str) -> str:
    s = s.strip()
    # Strip heading markers like ###
    s = re.sub(r"^#{1,6}\s+", "", s)
    # Strip list markers
    s = re.sub(r"^[-*+]\s+", "", s)
    # Strip ordered-list markers like "1. " or "2) "
    s = re.sub(r"^\d+[.)]\s+", "", s)
    # Strip bold/italics wrappers for the full string
    s = re.sub(r"^\*\*(.+)\*\*$", r"\1", s)
    s = re.sub(r"^__(.+)__$", r"\1", s)
    s = re.sub(r"^\*(.+)\*$", r"\1", s)
    s = re.sub(r"^_(.+)_$", r"\1", s)
    # Strip trailing colon
    s = re.sub(r":\s*$", "", s)
    return _normalize_ws(s)


def parse_meta_issue_body(body: str) -> Dict[int, str]:
    """Return a mapping of issue_number -> category label (exact text from body).

    The meta issue body tends to be markdown with headings or bolded section names.
    We detect category section boundaries in a tolerant way and collect issue links
    under each section.
    """

    known_keys = [_normalize_key(c) for c in KNOWN_CATEGORIES]

    current_category: Optional[str] = None
    mapping: Dict[int, str] = {}

    for raw_line in (body or "").splitlines():
        line = raw_line.rstrip("\n")
        candidate = _strip_md_decorations(line)

        # Heuristic: treat a line as a category marker if, once stripped of markdown
        # decorations, it matches (or starts with) a known category.
        key = _normalize_key(candidate)
        if candidate and any(key == k or key.startswith(k + " ") for k in known_keys):
            # Preserve exact label as displayed in the body.
            current_category = candidate
            continue

        if not current_category:
            continue

        for n in extract_issue_numbers(line):
            if n not in mapping:
                mapping[n] = current_category

    return mapping


def build_dashboard(repo: str, meta_issue_number: int, token: Optional[str]) -> Dict[str, Any]:
    meta_issue, _meta_headers = fetch_issue(repo, meta_issue_number, token)
    meta_created_at = meta_issue.get("created_at") or _iso_now_utc()
    meta_body = meta_issue.get("body") or ""

    issue_to_category = parse_meta_issue_body(meta_body)
    issue_to_category.pop(meta_issue_number, None)

    # Deterministic issue processing order before date-based sort.
    ordered_issue_numbers = sorted(issue_to_category.keys())

    features: List[Feature] = []
    rate_limited = False

    for n in ordered_issue_numbers:
        category = issue_to_category[n]

        if rate_limited:
            features.append(
                Feature(
                    issue_number=n,
                    id=f"microsoft_learn_issue_{n}",
                    title=f"Issue #{n}",
                    source_type="issue",
                    product_area=category,
                    source_url=f"https://github.com/{repo}/issues/{n}",
                    date_discovered=meta_created_at,
                )
            )
            continue

        try:
            issue, headers = fetch_issue(repo, n, token)
            title = issue.get("title") or f"Issue #{n}"
            html_url = issue.get("html_url") or f"https://github.com/{repo}/issues/{n}"
            created_at = issue.get("created_at") or meta_created_at

            features.append(
                Feature(
                    issue_number=n,
                    id=f"microsoft_learn_issue_{n}",
                    title=title,
                    source_type="issue",
                    product_area=category,
                    source_url=html_url,
                    date_discovered=created_at,
                )
            )

            remaining = headers.get("X-RateLimit-Remaining")
            if remaining is not None and not token:
                try:
                    if int(remaining) <= 0:
                        rate_limited = True
                except ValueError:
                    pass

        except Exception:
            # Graceful degradation on per-issue failures.
            features.append(
                Feature(
                    issue_number=n,
                    id=f"microsoft_learn_issue_{n}",
                    title=f"Issue #{n}",
                    source_type="issue",
                    product_area=category,
                    source_url=f"https://github.com/{repo}/issues/{n}",
                    date_discovered=meta_created_at,
                )
            )

    # Deterministic ordering: date_discovered ASC, then issue number ASC.
    def _sort_key(f: Feature) -> Tuple[dt.datetime, int]:
        try:
            d = _parse_iso_datetime(f.date_discovered)
        except Exception:
            d = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return (d, f.issue_number)

    features.sort(key=_sort_key)

    total = len(features)

    # time_series
    counts_by_date: Dict[str, int] = {}
    for f in features:
        day = _date_yyyy_mm_dd(f.date_discovered)
        counts_by_date[day] = counts_by_date.get(day, 0) + 1

    ts_rows: List[Dict[str, Any]] = []
    cumulative = 0
    for day in sorted(counts_by_date.keys()):
        count = counts_by_date[day]
        cumulative += count
        ts_rows.append({"date": day, "count": count, "cumulative": cumulative})

    # source_breakdown
    source_breakdown = {"sources": [{"name": "issue", "count": total}]}

    # product_area_breakdown
    counts_by_area: Dict[str, int] = {}
    for f in features:
        counts_by_area[f.product_area] = counts_by_area.get(f.product_area, 0) + 1

    area_rows = [{"name": name, "count": count} for name, count in counts_by_area.items()]
    area_rows.sort(key=lambda r: (-r["count"], r["name"]))

    dashboard: Dict[str, Any] = {
        "generated_at": _iso_now_utc(),
        "summary": {"total_features": total},
        "time_series": {"time_series": ts_rows, "total": total},
        "source_breakdown": source_breakdown,
        "product_area_breakdown": {"product_areas": area_rows},
        "features": [
            {
                "id": f.id,
                "title": f.title,
                "source_type": f.source_type,
                "product_area": f.product_area,
                "source_url": f.source_url,
                "date_discovered": f.date_discovered,
            }
            for f in features
        ],
        "content_checks": [],
        "gaps": [],
    }

    # Validations
    if dashboard["summary"]["total_features"] != len(dashboard["features"]):
        raise RuntimeError("Validation failed: summary.total_features != len(features)")
    if dashboard["time_series"]["total"] != len(dashboard["features"]):
        raise RuntimeError("Validation failed: time_series.total != len(features)")
    if sum(r["count"] for r in dashboard["product_area_breakdown"]["product_areas"]) != len(
        dashboard["features"]
    ):
        raise RuntimeError("Validation failed: product area counts do not sum to total")

    return dashboard


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build issues dashboard JSON from a GitHub meta issue")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo in OWNER/REPO format")
    parser.add_argument("--issue-number", type=int, default=DEFAULT_ISSUE_NUMBER, help="Meta issue number")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output JSON path")

    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN")

    try:
        dashboard = build_dashboard(repo=args.repo, meta_issue_number=args.issue_number, token=token)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    out_path = args.out
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, indent=2)
        f.write("\n")

    print(f"Wrote {out_path} with {dashboard['summary']['total_features']} features")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
