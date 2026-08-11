import httpx
import pytest
from pydantic import SecretStr

from app.github.client import GitHubClient, GitHubResponseError
from app.services.pull_request_inspection import (
    CompletePullRequestInspection,
    GitHubPullRequestInspector,
    PullRequestTooLarge,
)


def file_payload(index: int, *, include_patch: bool = True) -> dict[str, object]:
    payload: dict[str, object] = {
        "filename": f"src/file_{index}.py",
        "status": "renamed" if index == 3 else "modified",
        "additions": index,
        "deletions": 1,
        "changes": index + 1,
    }
    if include_patch:
        payload["patch"] = f"@@ -1 +1 @@\n-old {index}\n+new {index}"
    return payload


@pytest.mark.asyncio
async def test_inspection_collects_complete_paginated_file_set() -> None:
    requested_urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(request.url)
        if request.url.params.get("page") == "2":
            return httpx.Response(
                200,
                json=[file_payload(3, include_patch=False)],
                request=request,
            )
        return httpx.Response(
            200,
            headers={
                "Link": (
                    "<https://api.github.test/repos/octo-org/repository/"
                    'pulls/42/files?page=2>; rel="next"'
                )
            },
            json=[file_payload(1), file_payload(2)],
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )
        inspector = GitHubPullRequestInspector(client)

        result = await inspector.inspect(
            repository_full_name="octo-org/repository",
            pull_request_number=42,
            installation_token=SecretStr("installation-token"),
            max_files=100,
        )

    assert isinstance(result, CompletePullRequestInspection)
    assert [file.filename for file in result.files] == [
        "src/file_1.py",
        "src/file_2.py",
        "src/file_3.py",
    ]
    assert result.files[2].status == "renamed"
    assert result.files[2].patch is None
    assert [str(url) for url in requested_urls] == [
        "https://api.github.test/repos/octo-org/repository/pulls/42/files?per_page=100",
        "https://api.github.test/repos/octo-org/repository/pulls/42/files?page=2",
    ]


@pytest.mark.asyncio
async def test_inspection_returns_no_partial_files_when_limit_is_exceeded() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(
                200,
                headers={
                    "Link": (
                        "<https://api.github.test/repos/octo-org/repository/"
                        'pulls/42/files?page=2>; rel="next"'
                    )
                },
                json=[file_payload(1), file_payload(2)],
                request=request,
            )
        return httpx.Response(200, json=[file_payload(3)], request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )
        inspector = GitHubPullRequestInspector(client)

        result = await inspector.inspect(
            repository_full_name="octo-org/repository",
            pull_request_number=42,
            installation_token=SecretStr("installation-token"),
            max_files=2,
        )

    assert result == PullRequestTooLarge(max_files=2)
    assert request_count == 2


@pytest.mark.asyncio
async def test_inspection_handles_pull_request_with_no_files() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )
        inspector = GitHubPullRequestInspector(client)

        result = await inspector.inspect(
            repository_full_name="octo-org/repository",
            pull_request_number=42,
            installation_token=SecretStr("installation-token"),
            max_files=100,
        )

    assert result == CompletePullRequestInspection(files=())


@pytest.mark.asyncio
async def test_inspection_rejects_malformed_github_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"filename": "not-a-list"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )
        inspector = GitHubPullRequestInspector(client)

        with pytest.raises(GitHubResponseError):
            await inspector.inspect(
                repository_full_name="octo-org/repository",
                pull_request_number=42,
                installation_token=SecretStr("installation-token"),
                max_files=100,
            )
