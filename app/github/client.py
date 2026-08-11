from collections.abc import AsyncIterator, Mapping

import httpx

DEFAULT_TIMEOUT_SECONDS = 10.0


class GitHubAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubAuthenticationError(GitHubAPIError):
    pass


class GitHubPermissionError(GitHubAPIError):
    pass


class GitHubNotFoundError(GitHubAPIError):
    pass


class GitHubValidationError(GitHubAPIError):
    pass


class GitHubRateLimitError(GitHubAPIError):
    pass


class GitHubServerError(GitHubAPIError):
    pass


class GitHubNetworkError(GitHubAPIError):
    pass


class GitHubTimeoutError(GitHubAPIError):
    pass


class GitHubResponseError(GitHubAPIError):
    pass


class GitHubClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        base_url: str,
        api_version: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        normalized_base_url = f"{base_url.rstrip('/')}/"
        self._base_url = httpx.URL(normalized_base_url)
        if self._base_url.scheme != "https":
            raise ValueError("GitHub API base URL must use HTTPS")

        self._http_client = http_client
        self._api_version = api_version
        self._timeout = httpx.Timeout(timeout_seconds)

    async def request(
        self,
        method: str,
        path_or_url: str,
        token: str,
        params: Mapping[str, str | int] | None = None,
    ) -> httpx.Response:
        url = self._resolve_url(path_or_url)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Change-Echo",
            "X-GitHub-Api-Version": self._api_version,
        }

        try:
            response = await self._http_client.request(
                method,
                url,
                headers=headers,
                params=params,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as error:
            raise GitHubTimeoutError("GitHub API request timed out") from error
        except httpx.RequestError as error:
            raise GitHubNetworkError("GitHub API network request failed") from error

        self._raise_for_status(response)
        return response

    async def paginate(
        self,
        path: str,
        token: str,
        params: Mapping[str, str | int] | None = None,
        max_pages: int | None = None,
    ) -> AsyncIterator[httpx.Response]:
        if max_pages is not None and max_pages <= 0:
            raise ValueError("max_pages must be greater than zero")

        next_url: str | None = path
        next_params = params
        page_count = 0

        while next_url is not None:
            response = await self.request(
                "GET",
                next_url,
                token,
                params=next_params,
            )
            yield response
            page_count += 1

            if max_pages is not None and page_count >= max_pages:
                break

            next_link = response.links.get("next")
            next_url = next_link.get("url") if next_link is not None else None
            next_params = None

    def _resolve_url(self, path_or_url: str) -> httpx.URL:
        candidate = httpx.URL(path_or_url)
        if not candidate.is_absolute_url:
            return self._base_url.join(path_or_url.lstrip("/"))

        candidate_origin = (candidate.scheme, candidate.host, candidate.port)
        base_origin = (self._base_url.scheme, self._base_url.host, self._base_url.port)
        if candidate_origin != base_origin:
            raise ValueError("GitHub pagination URL must use the configured API origin")
        return candidate

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if 200 <= response.status_code < 300:
            return

        status_code = response.status_code
        error_type: type[GitHubAPIError]
        if status_code == 401:
            error_type = GitHubAuthenticationError
            message = "GitHub API authentication failed"
        elif status_code == 429 or (
            status_code == 403
            and (
                response.headers.get("X-RateLimit-Remaining") == "0"
                or "Retry-After" in response.headers
            )
        ):
            error_type = GitHubRateLimitError
            message = "GitHub API rate limit exceeded"
        elif status_code == 403:
            error_type = GitHubPermissionError
            message = "GitHub API permission denied"
        elif status_code == 404:
            error_type = GitHubNotFoundError
            message = "GitHub API resource not found"
        elif status_code == 422:
            error_type = GitHubValidationError
            message = "GitHub API rejected the request"
        elif 500 <= status_code < 600:
            error_type = GitHubServerError
            message = "GitHub API server error"
        else:
            error_type = GitHubAPIError
            message = "Unexpected GitHub API response"

        raise error_type(message, status_code=status_code)
