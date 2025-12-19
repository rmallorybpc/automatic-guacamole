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
	- `http://localhost:8000/issue-223.html` (preview)
	- `http://localhost:8000/issues.html` (live)
