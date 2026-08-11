from collections.abc import Mapping

import httpx
import pytest

from app.github.client import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClient,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubServerError,
    GitHubTimeoutError,
    GitHubValidationError,
)


@pytest.mark.asyncio
async def test_client_sends_required_headers_and_parameters() -> None:
    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )
        await client.request(
            "GET",
            "/items",
            "installation-token",
            params={"per_page": 100},
        )

    assert captured_request is not None
    assert captured_request.url == "https://api.github.test/items?per_page=100"
    assert captured_request.headers["Accept"] == "application/vnd.github+json"
    assert captured_request.headers["Authorization"] == "Bearer installation-token"
    assert captured_request.headers["User-Agent"] == "Change-Echo"
    assert captured_request.headers["X-GitHub-Api-Version"] == "2026-03-10"


@pytest.mark.parametrize(
    ("status_code", "headers", "error_type"),
    [
        (401, {}, GitHubAuthenticationError),
        (403, {}, GitHubPermissionError),
        (403, {"X-RateLimit-Remaining": "0"}, GitHubRateLimitError),
        (403, {"Retry-After": "60"}, GitHubRateLimitError),
        (404, {}, GitHubNotFoundError),
        (422, {}, GitHubValidationError),
        (429, {}, GitHubRateLimitError),
        (500, {}, GitHubServerError),
    ],
)
@pytest.mark.asyncio
async def test_client_maps_github_error_responses(
    status_code: int,
    headers: Mapping[str, str],
    error_type: type[GitHubAPIError],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, headers=headers, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )

        with pytest.raises(error_type) as captured_error:
            await client.request("GET", "/resource", "sensitive-token")

    assert captured_error.value.status_code == status_code
    assert "sensitive-token" not in str(captured_error.value)


@pytest.mark.asyncio
async def test_client_maps_timeout_without_exposing_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("network details", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )

        with pytest.raises(GitHubTimeoutError) as captured_error:
            await client.request("GET", "/resource", "sensitive-token")

    assert "sensitive-token" not in str(captured_error.value)


@pytest.mark.asyncio
async def test_client_maps_network_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network details", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )

        with pytest.raises(GitHubNetworkError):
            await client.request("GET", "/resource", "sensitive-token")


@pytest.mark.asyncio
async def test_pagination_follows_link_header_and_honors_page_cap() -> None:
    requested_urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(request.url)
        page = request.url.params.get("page", "1")
        next_page = int(page) + 1
        return httpx.Response(
            200,
            headers={
                "Link": (
                    f'<https://api.github.test/items?page={next_page}>; rel="next"'
                )
            },
            json=[{"page": int(page)}],
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )
        responses = [
            response
            async for response in client.paginate(
                "/items",
                "installation-token",
                max_pages=2,
            )
        ]

    assert len(responses) == 2
    assert [str(url) for url in requested_urls] == [
        "https://api.github.test/items",
        "https://api.github.test/items?page=2",
    ]


@pytest.mark.asyncio
async def test_pagination_rejects_next_link_for_another_origin() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Link": '<https://example.com/items?page=2>; rel="next"'},
            json=[],
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(
            http_client,
            base_url="https://api.github.test",
            api_version="2026-03-10",
        )

        with pytest.raises(ValueError, match="configured API origin"):
            async for _response in client.paginate(
                "/items",
                "installation-token",
            ):
                pass
