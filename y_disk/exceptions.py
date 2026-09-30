"""Exceptions raised by the y_disk package."""
from __future__ import annotations

from typing import Optional


class YandexDiskError(Exception):
    """Base class for all errors raised by this package."""


class YandexDiskAPIError(YandexDiskError):
    """Raised when the Yandex.Disk API returns an error response."""

    def __init__(self, status_code: int, message: str, payload: Optional[dict] = None):
        self.status_code = status_code
        self.payload = payload or {}
        super().__init__(f"[{status_code}] {message}")


class ResourceNotFoundError(YandexDiskAPIError):
    """Raised when a requested path does not exist on the disk."""


class AuthenticationError(YandexDiskAPIError):
    """Raised when the OAuth token is missing/invalid/expired."""
