# Change Echo

Change Echo is an advisory GitHub App that finds historically similar pull
requests in the same repository. It helps reviewers understand whether a
related change was attempted before and what happened to it.

Change Echo uses deterministic scoring based on changed files, directories,
pull-request titles, and descriptions. It does not use AI, block merges, or
judge whether a change should be accepted.

## How it works

When a pull request is opened, reopened, updated, or edited, Change Echo:

1. verifies the GitHub webhook signature;
2. retrieves the pull request's changed files;
3. finds historical pull requests connected to recent commits touching those
   files;
4. scores and ranks the most relevant matches;
5. publishes the result as a `Change Echo` Check Run on the pull request.

A check reports:

- `success` when no meaningful historical echo is found;
- `neutral` when a possible or strong echo is found;
- `neutral` when analysis is safely limited, skipped, or cannot be completed.

Historical matches include the Echo Score, historical outcome, factual match
reasons, and a link to the earlier pull request. All results are advisory.

## GitHub App configuration

Configure the GitHub App with these repository permissions:

| Permission | Access |
| --- | --- |
| Metadata | Read |
| Pull requests | Read |
| Contents | Read |
| Checks | Read and write |

Subscribe to the `pull_request` webhook. The application handles the `opened`,
`reopened`, `synchronize`, and `edited` actions.

Set the webhook URL to:

```text
https://<public-host>/webhooks/github
```

Use the same webhook secret in the GitHub App settings and the local `.env`
file.

## Local setup

Requirements:

- Python 3.12 or newer;
- a GitHub App ID;
- a downloaded GitHub App private key;
- a webhook secret.

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Install the application and development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Create the local configuration file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Set at least these values in `.env`:

```dotenv
GITHUB_APP_ID=123456
GITHUB_PRIVATE_KEY_PATH=path/to/private-key.pem
GITHUB_WEBHOOK_SECRET=replace-with-your-webhook-secret
```

The remaining settings in `.env.example` provide safe development defaults for
API versioning, analysis limits, result limits, and scoring thresholds.

Never commit `.env`, private keys, installation tokens, or other credentials.

## Run the application

Start the FastAPI development server:

```bash
uvicorn app.main:app --reload
```

Available endpoints:

```text
GET  /health
POST /webhooks/github
```

The local health endpoint is available at
<http://127.0.0.1:8000/health>.

To receive GitHub webhooks locally, expose port `8000` through a secure HTTPS
tunnel and use its public URL in the GitHub App configuration.

## Development checks

Run the automated tests:

```bash
pytest
```

Run linting, formatting checks, and type checking:

```bash
ruff check .
ruff format --check .
mypy app
```

Tests use mocked GitHub HTTP responses and do not call live GitHub endpoints.
