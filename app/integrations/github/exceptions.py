from __future__ import annotations


class GitHubAPIError(Exception):
    """Base exception for all GitHub API errors."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.request_id = request_id

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"status_code={self.status_code!r}, "
            f"request_id={self.request_id!r}, "
            f"message={str(self)!r})"
        )


class GitHubAuthError(GitHubAPIError):
    """Raised on 401 or 403 responses that are not rate-limit related."""


class GitHubRateLimitError(GitHubAPIError):
    """Raised when the API rate limit is exhausted (403 + remaining == 0)."""

    def __init__(
        self,
        message: str,
        reset_at: int | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(message, **kwargs)  # type: ignore[arg-type]
        self.reset_at = reset_at


class GitHubNotFoundError(GitHubAPIError):
    """Raised on 404 responses."""


class GitHubServerError(GitHubAPIError):
    """Raised on 5xx responses."""


class GitHubValidationError(GitHubAPIError):
    """Raised on 422 Unprocessable Entity responses."""
