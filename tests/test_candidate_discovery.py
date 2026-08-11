import httpx
import pytest
from pydantic import SecretStr

from app.github.client import GitHubClient, GitHubResponseError
from app.services.candidate_discovery import (
    GitHubCandidateDiscoverer,
    HistoricalPullRequestCandidate,
)


def create_client(http_client: httpx.AsyncClient) -> GitHubClient:
    return GitHubClient(
        http_client,
        base_url="https://api.github.test",
        api_version="2026-03-10",
    )


@pytest.mark.asyncio
async def test_discovery_deduplicates_orders_and_excludes_current_pull_request() -> (
    None
):
    requests: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        file_path = request.url.params.get("path")
        requests.append((request.url.path, file_path))
        if file_path == "src/a.py":
            payload = [{"sha": "sha-a"}]
        elif file_path == "src/b.py":
            payload = [{"sha": "sha-b"}]
        elif request.url.path.endswith("/commits/sha-a/pulls"):
            payload = [{"number": 42}, {"number": 7}, {"number": 5}]
        elif request.url.path.endswith("/commits/sha-b/pulls"):
            payload = [{"number": 7}, {"number": 9}]
        else:
            raise AssertionError(f"Unexpected request: {request.url}")
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        discoverer = GitHubCandidateDiscoverer(create_client(http_client))
        candidates = await discoverer.discover(
            repository_full_name="octo-org/repository",
            current_pull_request_number=42,
            current_file_paths=("src/b.py", "src/a.py", "src/a.py"),
            installation_token=SecretStr("installation-token"),
            max_commits_per_path=20,
            max_unique_candidates=10,
        )

    assert candidates == (
        HistoricalPullRequestCandidate(
            number=7,
            matching_paths=("src/a.py", "src/b.py"),
        ),
        HistoricalPullRequestCandidate(number=9, matching_paths=("src/b.py",)),
        HistoricalPullRequestCandidate(number=5, matching_paths=("src/a.py",)),
    )
    assert requests == [
        ("/repos/octo-org/repository/commits", "src/a.py"),
        ("/repos/octo-org/repository/commits", "src/b.py"),
        ("/repos/octo-org/repository/commits/sha-a/pulls", None),
        ("/repos/octo-org/repository/commits/sha-b/pulls", None),
    ]


@pytest.mark.asyncio
async def test_discovery_prioritizes_multi_path_commits_and_stops_at_cap() -> None:
    associated_commit_requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        file_path = request.url.params.get("path")
        if file_path == "src/a.py":
            payload = [{"sha": "shared"}, {"sha": "a-only"}]
        elif file_path == "src/b.py":
            payload = [{"sha": "b-only"}, {"sha": "shared"}]
        elif "/pulls" in request.url.path:
            commit_sha = request.url.path.split("/")[-2]
            associated_commit_requests.append(commit_sha)
            payload = [{"number": 3}]
        else:
            raise AssertionError(f"Unexpected request: {request.url}")
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        discoverer = GitHubCandidateDiscoverer(create_client(http_client))
        candidates = await discoverer.discover(
            repository_full_name="octo-org/repository",
            current_pull_request_number=42,
            current_file_paths=("src/a.py", "src/b.py"),
            installation_token=SecretStr("installation-token"),
            max_commits_per_path=2,
            max_unique_candidates=1,
        )

    assert candidates == (
        HistoricalPullRequestCandidate(
            number=3,
            matching_paths=("src/a.py", "src/b.py"),
        ),
    )
    assert associated_commit_requests == ["shared"]


@pytest.mark.asyncio
async def test_discovery_skips_malformed_and_inaccessible_records() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("path") == "src/a.py":
            return httpx.Response(
                200,
                json=[{}, {"sha": "missing"}, {"sha": "good"}],
                request=request,
            )
        if request.url.path.endswith("/commits/missing/pulls"):
            return httpx.Response(404, request=request)
        if request.url.path.endswith("/commits/good/pulls"):
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json=[{"number": 8}], request=request)
            return httpx.Response(
                200,
                headers={
                    "Link": (
                        "<https://api.github.test/repos/octo-org/repository/"
                        'commits/good/pulls?page=2>; rel="next"'
                    )
                },
                json=[{}, {"number": 0}],
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        discoverer = GitHubCandidateDiscoverer(create_client(http_client))
        candidates = await discoverer.discover(
            repository_full_name="octo-org/repository",
            current_pull_request_number=42,
            current_file_paths=("src/a.py",),
            installation_token=SecretStr("installation-token"),
            max_commits_per_path=20,
            max_unique_candidates=2,
        )

    assert candidates == (
        HistoricalPullRequestCandidate(number=8, matching_paths=("src/a.py",)),
    )


@pytest.mark.asyncio
async def test_discovery_rejects_invalid_list_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sha": "not-a-list"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        discoverer = GitHubCandidateDiscoverer(create_client(http_client))
        with pytest.raises(GitHubResponseError):
            await discoverer.discover(
                repository_full_name="octo-org/repository",
                current_pull_request_number=42,
                current_file_paths=("src/a.py",),
                installation_token=SecretStr("installation-token"),
                max_commits_per_path=20,
                max_unique_candidates=40,
            )


@pytest.mark.asyncio
async def test_discovery_with_no_files_makes_no_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        discoverer = GitHubCandidateDiscoverer(create_client(http_client))
        candidates = await discoverer.discover(
            repository_full_name="octo-org/repository",
            current_pull_request_number=42,
            current_file_paths=(),
            installation_token=SecretStr("installation-token"),
            max_commits_per_path=20,
            max_unique_candidates=40,
        )

    assert candidates == ()
