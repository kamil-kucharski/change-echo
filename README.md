# Change Echo

Change Echo is an advisory GitHub App that helps repositories surface earlier
pull requests related to a current change. The application is designed to
preserve repository memory without blocking merges or judging code quality.

## Current capabilities

The application currently:

- exposes `GET /health`;
- validates GitHub webhook signatures against the raw request body;
- accepts supported `pull_request` webhook actions;
- authenticates as a GitHub App with a short-lived RS256 JWT;
- obtains an installation access token using the webhook installation ID;
- retrieves the complete changed-file list for the current pull request;
- stops inspection explicitly when the configured file limit is exceeded;
- discovers historical pull-request candidates from recent commits touching the
  current file paths;
- deduplicates candidates, excludes the current pull request, and applies
  deterministic discovery limits;
- provides deterministic Echo scoring, classification, outcome classification,
  and ranking for enriched pull-request data;
- uses bounded pagination, request timeouts, and typed GitHub error handling.

Candidate enrichment and Check Run reporting are not implemented yet. The
scoring module remains disconnected from live webhook processing until the
required candidate metadata and changed-file sets are available.

## Requirements

- Python 3.12 or newer
- A GitHub App ID, private key file, and webhook secret for live webhook handling

## Local setup

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell, activate it with:

```powershell
.venv\Scripts\Activate.ps1
```

Install the application and development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Copy the example configuration:

```bash
cp .env.example .env
```

On Windows PowerShell, use `Copy-Item .env.example .env`.

Set these values in the local `.env` file:

| Variable | Purpose | Default |
| --- | --- | --- |
| `APP_ENV` | Application environment | `development` |
| `LOG_LEVEL` | Application log level | `INFO` |
| `GITHUB_APP_ID` | Numeric GitHub App ID | required for webhook processing |
| `GITHUB_PRIVATE_KEY_PATH` | Path to the GitHub App private key file | required for webhook processing |
| `GITHUB_WEBHOOK_SECRET` | Secret used to verify webhook signatures | required for webhook processing |
| `GITHUB_API_BASE_URL` | GitHub REST API base URL | `https://api.github.com` |
| `GITHUB_API_VERSION` | Explicit GitHub REST API version | `2026-03-10` |
| `ECHO_MAX_CURRENT_FILES` | Maximum changed files inspected in a PR | `100` |
| `ECHO_MAX_COMMITS_PER_PATH` | Recent commits inspected for each changed path | `20` |
| `ECHO_MAX_UNIQUE_CANDIDATES` | Maximum deduplicated historical PR candidates | `40` |
| `ECHO_MAX_RESULTS` | Maximum relevant echoes returned by ranking | `3` |
| `ECHO_POSSIBLE_THRESHOLD` | Minimum score classified as a possible echo | `0.55` |
| `ECHO_STRONG_THRESHOLD` | Minimum score classified as a strong echo | `0.72` |

Never commit `.env`, private keys, installation tokens, or generated
credentials. The private key is read from the configured file path and its
contents must not be placed directly in `.env`.

## Run the application

Start the development server:

```bash
uvicorn app.main:app --reload
```

The health endpoint is available at <http://127.0.0.1:8000/health>.

The GitHub webhook endpoint is:

```text
POST /webhooks/github
```

Supported pull-request actions are `opened`, `reopened`, `synchronize`, and
`edited`. Unsupported events and actions are acknowledged and ignored. A pull
request exceeding `ECHO_MAX_CURRENT_FILES` receives an explicit bounded-analysis
response instead of being partially inspected.

For an accepted pull request, the application also checks recent commits for
each changed path and maps those commits to historical pull requests. Candidate
discovery is deterministic and bounded, but candidates are not displayed on the
pull request until scoring and Check Run reporting are implemented.

## Development checks

```bash
pytest
ruff check .
ruff format --check .
mypy app
```

Tests use mock HTTP transports and do not call live GitHub endpoints.
