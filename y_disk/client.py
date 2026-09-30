"""A convenient client for the Yandex.Disk REST API.

By design this client never deletes or overwrites anything on the disk:
uploads always land at a unique path (an existing file is never overwritten),
and there is no delete/remove/move-to-trash method at all.
"""
from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

import requests

from .exceptions import AuthenticationError, ResourceNotFoundError, YandexDiskAPIError

API_BASE_URL = "https://cloud-api.yandex.net/v1/disk"
LIST_PAGE_SIZE = 200
ITEM_FIELDS = (
    "_embedded.items.name,_embedded.items.path,_embedded.items.type,"
    "_embedded.items.size,_embedded.items.mime_type,_embedded.items.md5,"
    "_embedded.items.created,_embedded.items.modified,_embedded.total"
)


@dataclass
class DiskItem:
    """Metadata for a file or folder on the disk."""

    name: str
    path: str
    type: str
    size: Optional[int] = None
    mime_type: Optional[str] = None
    md5: Optional[str] = None
    created: Optional[str] = None
    modified: Optional[str] = None

    @property
    def is_dir(self) -> bool:
        return self.type == "dir"

    @classmethod
    def _from_api(cls, data: dict) -> "DiskItem":
        return cls(
            name=data["name"],
            path=data["path"],
            type=data["type"],
            size=data.get("size"),
            mime_type=data.get("mime_type"),
            md5=data.get("md5"),
            created=data.get("created"),
            modified=data.get("modified"),
        )


class YandexDiskClient:
    """A thin, convenient wrapper around the Yandex.Disk REST API."""

    def __init__(self, token: str, base_url: str = API_BASE_URL, timeout: float = 60.0):
        if not token:
            raise ValueError("A Yandex.Disk OAuth token is required")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"OAuth {token}",
            "Accept": "application/json",
        })

    # ------------------------------------------------------------------
    # low-level request helpers
    # ------------------------------------------------------------------
    def _api(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self._base_url}{path}"
        resp = self._session.request(method, url, timeout=self._timeout, **kwargs)
        if resp.status_code >= 400:
            self._raise_for_error(resp)
        return resp

    @staticmethod
    def _raise_for_error(resp: requests.Response) -> None:
        try:
            payload = resp.json()
            message = payload.get("message", resp.text)
        except ValueError:
            payload, message = {}, resp.text
        if resp.status_code == 401:
            raise AuthenticationError(resp.status_code, message, payload)
        if resp.status_code == 404:
            raise ResourceNotFoundError(resp.status_code, message, payload)
        raise YandexDiskAPIError(resp.status_code, message, payload)

    @staticmethod
    def _normalize(path: str) -> str:
        """Normalize a path, leaving scheme-prefixed paths (``disk:/...``,
        ``app:/...``, ``trash:/...``) untouched.

        ``app:/...`` addresses the app's dedicated folder and is what tokens
        with restricted ("app folder only") access must use, since they
        cannot see ``disk:/`` (the full disk root) at all.
        """
        path = path.strip()
        if not path or path == "/":
            return "/"
        for scheme in ("disk:", "app:", "trash:"):
            if path.startswith(scheme):
                return path
        if not path.startswith("/"):
            path = "/" + path
        return path.rstrip("/") or "/"

    # ------------------------------------------------------------------
    # disk info
    # ------------------------------------------------------------------
    def get_disk_info(self) -> dict:
        """Return raw disk info (total/used space, etc.)."""
        return self._api("GET", "").json()

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------
    def get_meta(self, path: str) -> DiskItem:
        """Return metadata for a file or folder."""
        path = self._normalize(path)
        data = self._api("GET", "/resources", params={"path": path, "limit": 0}).json()
        return DiskItem._from_api(data)

    def exists(self, path: str) -> bool:
        try:
            self.get_meta(path)
            return True
        except ResourceNotFoundError:
            return False

    def is_dir(self, path: str) -> bool:
        return self.get_meta(path).is_dir

    def list_dir(self, path: str = "/") -> List[DiskItem]:
        """Return the immediate children of a folder (non-recursive)."""
        path = self._normalize(path)
        items: List[DiskItem] = []
        offset = 0
        while True:
            data = self._api(
                "GET",
                "/resources",
                params={
                    "path": path,
                    "limit": LIST_PAGE_SIZE,
                    "offset": offset,
                    "fields": ITEM_FIELDS,
                },
            ).json()
            embedded = data.get("_embedded", {})
            page = embedded.get("items", [])
            items.extend(DiskItem._from_api(item) for item in page)
            offset += len(page)
            if not page or offset >= embedded.get("total", offset):
                break
        return items

    def _walk(self, path: str) -> Iterator[Tuple[str, List[DiskItem], List[DiskItem]]]:
        children = self.list_dir(path)
        dirs = [c for c in children if c.is_dir]
        files = [c for c in children if not c.is_dir]
        yield path, dirs, files
        for d in dirs:
            yield from self._walk(d.path)

    def walk(self, path: str = "/") -> Iterator[Tuple[str, List[DiskItem], List[DiskItem]]]:
        """Like ``os.walk``: yields ``(dir_path, subdirs, files)`` top-down."""
        root = self.get_meta(path).path
        yield from self._walk(root)

    def print_tree(self, path: str = "/", _indent: str = "") -> None:
        """Pretty-print the folder tree rooted at ``path``."""
        children = self.list_dir(path)
        for i, item in enumerate(children):
            connector = "└── " if i == len(children) - 1 else "├── "
            print(f"{_indent}{connector}{item.name}{'/' if item.is_dir else ''}")
            if item.is_dir:
                extension = "    " if i == len(children) - 1 else "│   "
                self.print_tree(item.path, _indent + extension)

    # ------------------------------------------------------------------
    # folders
    # ------------------------------------------------------------------
    def make_dir(self, path: str) -> str:
        """Create a folder, creating parent folders as needed.

        Reusing an existing folder is not an overwrite, so this is a no-op
        if the folder (or any of its parents) already exists.
        """
        path = self._normalize(path)
        scheme, remainder = "", path
        for s in ("disk:", "app:", "trash:"):
            if path.startswith(s):
                scheme, remainder = s, path[len(s):]
                break
        current = scheme
        for part in (p for p in remainder.split("/") if p):
            current += "/" + part
            try:
                self._api("PUT", "/resources", params={"path": current})
            except YandexDiskAPIError as e:
                if e.status_code != 409:  # 409 = already exists
                    raise
        return path

    # ------------------------------------------------------------------
    # unique naming -- never overwrite anything
    # ------------------------------------------------------------------
    def unique_remote_path(self, path: str) -> str:
        """Return ``path`` if free, otherwise the first free ``name (n).ext``."""
        path = self._normalize(path)
        if not self.exists(path):
            return path
        directory, name = posixpath.split(path)
        stem, ext = posixpath.splitext(name)
        n = 1
        while True:
            candidate = posixpath.join(directory or "/", f"{stem} ({n}){ext}")
            if not self.exists(candidate):
                return candidate
            n += 1

    # ------------------------------------------------------------------
    # upload / download
    # ------------------------------------------------------------------
    def upload_file(self, local_path: str, remote_path: str, make_unique: bool = True) -> str:
        """Upload a single local file.

        An existing remote file is never overwritten: by default a unique
        name is chosen automatically (``make_unique=True``); with
        ``make_unique=False`` a ``FileExistsError`` is raised instead.
        Returns the remote path the file was actually uploaded to.
        """
        remote_path = self._normalize(remote_path)
        remote_dir = posixpath.dirname(remote_path) or "/"
        self.make_dir(remote_dir)

        if make_unique:
            remote_path = self.unique_remote_path(remote_path)
        elif self.exists(remote_path):
            raise FileExistsError(f"{remote_path} already exists and overwriting is not supported")

        upload_info = self._api(
            "GET", "/resources/upload", params={"path": remote_path, "overwrite": False}
        ).json()
        with open(local_path, "rb") as f:
            resp = self._session.put(upload_info["href"], data=f, timeout=self._timeout)
        if resp.status_code >= 400:
            self._raise_for_error(resp)
        return remote_path

    def download_file(self, remote_path: str, local_path: str) -> str:
        """Download a single remote file to ``local_path``."""
        remote_path = self._normalize(remote_path)
        download_info = self._api("GET", "/resources/download", params={"path": remote_path}).json()
        local_dir = os.path.dirname(local_path)
        if local_dir:
            os.makedirs(local_dir, exist_ok=True)
        with self._session.get(download_info["href"], stream=True, timeout=self._timeout) as resp:
            if resp.status_code >= 400:
                self._raise_for_error(resp)
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
        return local_path

    def upload_folder(self, local_folder: str, remote_folder: str, make_unique: bool = True) -> str:
        """Recursively upload a local directory tree.

        Existing remote folders are reused; existing remote files are never
        overwritten (see :meth:`upload_file`).
        """
        remote_folder = self._normalize(remote_folder)
        self.make_dir(remote_folder)
        for root, _dirs, files in os.walk(local_folder):
            rel = os.path.relpath(root, local_folder)
            remote_root = remote_folder if rel == "." else posixpath.join(remote_folder, *rel.split(os.sep))
            self.make_dir(remote_root)
            for filename in files:
                local_file = os.path.join(root, filename)
                remote_file = posixpath.join(remote_root, filename)
                self.upload_file(local_file, remote_file, make_unique=make_unique)
        return remote_folder

    def download_folder(self, remote_folder: str, local_folder: str) -> str:
        """Recursively download a remote directory tree to ``local_folder``."""
        root = self.get_meta(remote_folder).path
        for dir_path, _dirs, files in self._walk(root):
            rel = posixpath.relpath(dir_path, root)
            local_dir = local_folder if rel == "." else os.path.join(local_folder, *rel.split("/"))
            os.makedirs(local_dir, exist_ok=True)
            for item in files:
                self.download_file(item.path, os.path.join(local_dir, item.name))
        return local_folder
