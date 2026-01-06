# automatic-guacamole
Issue Dashboard

## Issues dashboard (meta issue #223)

This repo hosts a separate “Issues dashboard” built from https://github.com/githubpartners/microsoft-learn/issues/223.

### Generate the JSON

Writes to `docs/reports/dashboard_issue_223.json`.

- Without a token (works, but may hit rate limits):
	- `python3 scripts/build_dashboard_from_meta_issue.py`
- With a token (recommended to avoid rate limits):
	- `GITHUB_TOKEN=... python3 scripts/build_dashboard_from_meta_issue.py`

Optional flags:
- `--repo` (default `githubpartners/microsoft-learn`)
- `--issue-number` (default `223`)
- `--out` (default `docs/reports/dashboard_issue_223.json`)

### Preview locally

Serve the `docs/` folder and open either page:

- `cd docs && python3 -m http.server 8000`
- Open:
	- `http://localhost:8000/` (redirects to the dashboard)
	- `http://localhost:8000/issue-223.html` (preview)
	- `http://localhost:8000/issues.html` (live)

Alternatively, you can serve the repo root and open `/`:

- `python3 -m http.server 8000`
- Open `http://localhost:8000/` (redirects to `docs/`)

Important:
- Don’t open the HTML files directly via `file://.../docs/issues.html` — the page fetches JSON and may appear blank.
- Always use the HTTP URL from the server above.
- If you are using a dev container / Codespaces:
	- Make sure port `8000` is forwarded from VS Code’s **Ports** panel.
	- If you see “Error forwarding port”, ensure your server is listening on `0.0.0.0` (not `127.0.0.1`).

Note: the dashboard pages are static and only re-render on reload. Static servers
like `python3 -m http.server` cannot regenerate JSON from the page.

### (Dev) Refresh endpoints

The dashboard pages are safe to host statically; the in-page button opens the
GitHub Actions workflow (static pages cannot securely run the generator scripts).

For local development, you can still use the dev server which serves `docs/` and
exposes POST endpoints that run the existing generator scripts:

- `GITHUB_TOKEN=... python3 scripts/dev_dashboard_server.py --port 8000`
- Open:
	- `http://127.0.0.1:8000/issue-223.html`
	- `http://127.0.0.1:8000/issues.html`

Notes for dev containers / Codespaces:
- Prefer: `GITHUB_TOKEN=... python3 scripts/dev_dashboard_server.py --bind 0.0.0.0 --port 8000`
- The dev server serves `docs/` as the web root, so use `/issues.html` (not `/docs/issues.html`).

## Issues dashboard (all issues)

This repo can also generate a dashboard from *all* issues in a repo (defaults to
`githubpartners/microsoft-learn`).

### Generate the JSON

Writes to `docs/reports/dashboard_all_issues.json`.

- Without a token (may hit rate limits):
	- `python3 scripts/build_dashboard_from_repo_issues.py`
- With a token (recommended):
	- `GITHUB_TOKEN=... python3 scripts/build_dashboard_from_repo_issues.py`

Optional flags:
- `--repo` (default `githubpartners/microsoft-learn`)
- `--state` (`open`, `closed`, `all`; default `all`)
- `--out` (default `docs/reports/dashboard_all_issues.json`)

## Automated refresh (GitHub Actions)

This repo includes a workflow that regenerates `docs/reports/*.json` and commits
updates back to `main` on a weekly schedule.

No token is required from the person triggering the workflow: GitHub Actions
provides a built-in workflow token automatically.

- Workflow: `.github/workflows/refresh-dashboard.yml`
- Schedule: weekly (Mon 08:00 UTC)
- Manual run: GitHub → Actions → “Refresh dashboard data” → Run workflow
