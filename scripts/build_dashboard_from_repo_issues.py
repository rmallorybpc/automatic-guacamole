#!/usr/bin/env python3
"""Generate an Issues dashboard JSON from *all* issues in a GitHub repo.

This script is designed for Repo B (automatic-guacamole). It fetches issues from
GitHub's REST API (paginated), filters out pull requests, categorizes each issue
into a "product_area" bucket (best-effort), and writes a dashboard JSON file
matching the schema expected by the dashboard UI in `docs/issues.html`.

Stdlib only: argparse, datetime, json, os, re, urllib.

Notes:
- GitHub's `/repos/{owner}/{repo}/issues` endpoint returns both issues and PRs.
  PRs are filtered out by checking for the `pull_request` key.
- For rate limits, set `GITHUB_TOKEN` to a PAT (classic) or fine-grained token
  with access to the target repo.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


GITHUB_API_BASE = "https://api.github.com"
DEFAULT_REPO = "githubpartners/microsoft-learn"
DEFAULT_OUT = "docs/reports/dashboard_all_issues.json"


# Existing buckets from the meta-issue dashboard.
KNOWN_CATEGORIES = [
    "Grammar/Spelling",
    "Deprecated/Potentially Outdated Content",
    "Suggested Content Updates",
    "Other (Support/Ambiguous/Process)",
    "Placeholders (Template-Incomplete)",
]

# Additional bucket requested by user: map certain issue titles into this category.
MODULE_UPDATE_REQUEST_CATEGORY = "MS Learn Module Update Request"


@dataclass(frozen=True)
class Feature:
    issue_number: int
    id: str
    title: str
    state: str
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


def _utc_now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _parse_iso_datetime_or_none(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        d = _parse_iso_datetime(value)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d
    except Exception:
        return None


def should_include_issue(*, issue: Dict[str, Any], now_utc: dt.datetime, hide_closed_older_than_days: int) -> bool:
    state = str(issue.get("state") or "").lower()
    if state != "closed":
        return True

    # Prefer closed_at; fallback to updated_at if closed_at is missing.
    closed_at = _parse_iso_datetime_or_none(issue.get("closed_at"))
    fallback = _parse_iso_datetime_or_none(issue.get("updated_at"))
    effective_closed_dt = closed_at or fallback
    if not effective_closed_dt:
        # If we can't determine age, keep it (best-effort).
        return True

    age = now_utc - effective_closed_dt
    return age <= dt.timedelta(days=hide_closed_older_than_days)


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _make_request(url: str, token: Optional[str]) -> urllib.request.Request:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "automatic-guacamole-issues-dashboard-builder",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def github_get_json(url: str, token: Optional[str]) -> Tuple[Any, Dict[str, str]]:
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


def list_issues_page(
    *,
    repo: str,
    state: str,
    page: int,
    per_page: int,
    token: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    params = {
        "state": state,
        "page": str(page),
        "per_page": str(per_page),
        # This endpoint supports a `since` param, but we don't use it here.
    }
    url = f"{GITHUB_API_BASE}/repos/{repo}/issues?{urllib.parse.urlencode(params)}"
    data, headers = github_get_json(url, token)
    if not isinstance(data, list):
        raise RuntimeError(f"Expected list from {url}, got {type(data).__name__}")
    return data, headers


_TITLE_PREFIX_RE = re.compile(r"^\s*ms\s*learn\s*module\s*update\s*request\s*:\s*", re.IGNORECASE)


def _category_from_title(title: str) -> Optional[str]:
    # Requested: "MS Learn Module Update Request: [REPLACE_WITH_MODULE_TITLE]"
    # should bucket to "MS Learn Module Update Request".
    if _TITLE_PREFIX_RE.search(title or ""):
        return MODULE_UPDATE_REQUEST_CATEGORY
    return None


def _text_contains_any(haystack: str, needles: List[str]) -> bool:
    h = (haystack or "").casefold()
    return any(n.casefold() in h for n in needles)


def categorize_issue_best_guess(
    *,
    title: str,
    labels: List[str],
    body: str,
) -> str:
    # Priority order:
    # 1) Special title prefix bucket
    # 2) Grammar/spelling
    # 3) Placeholders/template
    # 4) Deprecated/outdated
    # 5) Suggested updates
    # 6) Other

    title_norm = _normalize_ws(title)
    special = _category_from_title(title_norm)
    if special:
        return special

    label_blob = " | ".join(_normalize_ws(l) for l in (labels or []))
    blob = "\n".join([title_norm, label_blob, body or ""]).strip()

    if _text_contains_any(blob, ["grammar", "spelling", "typo", "typos", "misspelling", "punctuation"]):
        return "Grammar/Spelling"

    if _text_contains_any(blob, ["placeholder", "template", "tbd", "replace_with", "replace with", "lorem ipsum", "todo:"]):
        return "Placeholders (Template-Incomplete)"

    if _text_contains_any(blob, ["deprecated", "outdated", "obsolete", "no longer", "old version", "deprecation"]):
        return "Deprecated/Potentially Outdated Content"

    if _text_contains_any(
        blob,
        [
            "suggest",
            "suggestion",
            "content update",
            "update content",
            "needs update",
            "fix content",
            "improve",
            "clarify",
            "add section",
        ],
    ):
        return "Suggested Content Updates"

    return "Other (Support/Ambiguous/Process)"


def build_dashboard(*, repo: str, state: str, token: Optional[str]) -> Dict[str, Any]:
    per_page = 100
    page = 1

    now_utc = _utc_now()
    hide_closed_older_than_days = 30

    issues: List[Dict[str, Any]] = []

    while True:
        items, headers = list_issues_page(repo=repo, state=state, page=page, per_page=per_page, token=token)
        if not items:
            break

        # Filter out PRs.
        for it in items:
            if isinstance(it, dict) and "pull_request" not in it:
                issues.append(it)

        remaining = headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                if int(remaining) <= 0:
                    print(
                        "Warning: rate limit exhausted while paging issues; output will be partial. "
                        "Set GITHUB_TOKEN to avoid this.",
                        file=sys.stderr,
                    )
                    break
            except ValueError:
                pass

        page += 1

    features: List[Feature] = []
    for it in issues:
        if not should_include_issue(
            issue=it,
            now_utc=now_utc,
            hide_closed_older_than_days=hide_closed_older_than_days,
        ):
            continue

        n = int(it.get("number"))
        title = it.get("title") or f"Issue #{n}"
        html_url = it.get("html_url") or f"https://github.com/{repo}/issues/{n}"
        created_at = it.get("created_at") or _iso_now_utc()
        issue_state = it.get("state") or "unknown"
        body = it.get("body") or ""
        labels_raw = it.get("labels") or []

        labels: List[str] = []
        if isinstance(labels_raw, list):
            for lr in labels_raw:
                if isinstance(lr, dict) and lr.get("name"):
                    labels.append(str(lr.get("name")))
                elif isinstance(lr, str):
                    labels.append(lr)

        product_area = categorize_issue_best_guess(title=title, labels=labels, body=body)

        features.append(
            Feature(
                issue_number=n,
                id=f"microsoft_learn_issue_{n}",
                title=title,
                state=issue_state,
                source_type="issue",
                product_area=product_area,
                source_url=html_url,
                date_discovered=created_at,
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

    source_breakdown = {"sources": [{"name": "issue", "count": total}]}

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
                "state": f.state,
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
    parser = argparse.ArgumentParser(description="Build issues dashboard JSON from all issues in a GitHub repo")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo in OWNER/REPO format")
    parser.add_argument(
        "--state",
        default="all",
        choices=["open", "closed", "all"],
        help="Issue state filter",
    )
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output JSON path")

    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN")

    try:
        dashboard = build_dashboard(repo=args.repo, state=args.state, token=token)
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
