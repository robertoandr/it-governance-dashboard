from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import SecretStr


class AuthProvider(ABC):
    """Abstract base for GitHub authentication strategies.

    Implement this interface to add new auth methods without
    touching the HTTP client layer.
    """

    @abstractmethod
    async def get_headers(self) -> dict[str, str]:
        """Return HTTP headers required to authenticate requests."""


class PATAuth(AuthProvider):
    """Authenticates using a Personal Access Token (fine-grained or classic).

    Args:
        token: The GitHub PAT wrapped in ``SecretStr`` to prevent
            accidental exposure in logs or repr output.
    """

    _ACCEPT = "application/vnd.github+json"
    _API_VERSION = "2022-11-28"

    def __init__(self, token: SecretStr) -> None:
        self._token = token

    async def get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            "Accept": self._ACCEPT,
            "X-GitHub-Api-Version": self._API_VERSION,
        }


# TODO: Implement GitHubAppAuth using JWT + installation access tokens.
# Reference: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app
