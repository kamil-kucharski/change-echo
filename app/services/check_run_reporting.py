from typing import Protocol
from urllib.parse import quote

from pydantic import SecretStr

from app.github.check_rendering import CHECK_RUN_NAME, RenderedCheckRun
from app.github.client import GitHubClient


class CheckRunReporter(Protocol):
    async def publish(
        self,
        repository_full_name: str,
        head_sha: str,
        result: RenderedCheckRun,
        installation_token: SecretStr,
    ) -> None: ...


class GitHubCheckRunReporter:
    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    async def publish(
        self,
        repository_full_name: str,
        head_sha: str,
        result: RenderedCheckRun,
        installation_token: SecretStr,
    ) -> None:
        owner, repository = self._repository_parts(repository_full_name)
        if not head_sha:
            raise ValueError("head_sha must not be empty")

        path = f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/check-runs"
        await self._client.request(
            "POST",
            path,
            installation_token.get_secret_value(),
            json={
                "name": CHECK_RUN_NAME,
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": result.conclusion.value,
                "output": {
                    "title": result.title,
                    "summary": result.summary,
                    "text": result.text,
                },
            },
        )

    @staticmethod
    def _repository_parts(repository_full_name: str) -> tuple[str, str]:
        parts = repository_full_name.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("repository_full_name must contain owner and repository")
        return parts[0], parts[1]
