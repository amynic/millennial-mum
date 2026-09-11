"""Durable JSON storage for the small JSON-backed tools.

The hosted agent's code is mounted read-only and its container filesystem is
ephemeral, so writing to ``MM_DATA_DIR`` (``/tmp/...``) loses data on every
restart and is not shared between replicas. Tools therefore go through a
:class:`JsonStore`, which has two backends:

* :class:`BlobJsonStore` — Azure Blob Storage, durable and shared across
  replicas. Writes use the blob's ETag as an ``If-Match`` precondition so two
  concurrent turns can't silently clobber each other. Selected when
  ``MM_BLOB_ACCOUNT_URL`` or ``MM_BLOB_CONNECTION_STRING`` is set.
* :class:`LocalJsonStore` — a local file written atomically, used for dev and
  tests when no blob storage is configured.

Backends are resolved lazily (on first use, keyed by the current environment)
rather than at import time, so the process doesn't bake in a path or endpoint
that was only correct when the module happened to be imported.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONTAINER = "millennial-mum"
MAX_WRITE_ATTEMPTS = 5


class StaleWriteError(RuntimeError):
    """The document changed since it was read; the write was rejected."""


@dataclass(frozen=True)
class Document:
    """A stored JSON document plus the version token needed to update it.

    ``data`` is ``None`` when the document does not exist yet.
    """

    data: Any
    etag: Optional[str]

    @property
    def exists(self) -> bool:
        return self.data is not None


def _packaged_default(filename: str) -> Any:
    """Read the copy of ``filename`` shipped alongside the code, if any."""
    packaged = _PROJECT_ROOT / filename
    if not packaged.exists():
        return None
    try:
        return json.loads(packaged.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _serialise(data: Any) -> bytes:
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


class JsonStore(Protocol):
    """Read/write a named JSON document with optimistic concurrency."""

    def read(self, name: str) -> Document: ...

    def write(self, name: str, data: Any, *, etag: Optional[str]) -> Optional[str]:
        """Persist ``data``.

        ``etag`` is the version from the :class:`Document` that was read:
        ``None`` means "this document did not exist". Raises
        :class:`StaleWriteError` if the stored document no longer matches.
        """


class LocalJsonStore:
    """File-backed store for dev and tests. Writes are atomic; ETag is a hash."""

    def __init__(self, directory: Optional[str] = None):
        self._directory = Path(directory) if directory else _PROJECT_ROOT

    def _path(self, name: str) -> Path:
        return self._directory / name

    def read(self, name: str) -> Document:
        path = self._path(name)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return Document(data=None, etag=None)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # A truncated or hand-edited file shouldn't take the tool down.
            return Document(data=None, etag=None)
        return Document(data=data, etag=hashlib.sha256(raw).hexdigest())

    def write(self, name: str, data: Any, *, etag: Optional[str]) -> Optional[str]:
        path = self._path(name)
        if self.read(name).etag != etag:
            raise StaleWriteError(f"{name} changed since it was read")

        payload = _serialise(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as f:
                f.write(payload)
            os.replace(tmp_path, path)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        return hashlib.sha256(payload).hexdigest()


class BlobJsonStore:
    """Azure Blob Storage store using ETag optimistic concurrency."""

    def __init__(
        self,
        *,
        account_url: Optional[str] = None,
        connection_string: Optional[str] = None,
        container: str = DEFAULT_CONTAINER,
        prefix: str = "",
    ):
        if not account_url and not connection_string:
            raise ValueError("BlobJsonStore needs an account URL or connection string")
        self._account_url = account_url
        self._connection_string = connection_string
        self._container_name = container
        self._prefix = prefix
        self._container = None

    def _blob_name(self, name: str) -> str:
        return f"{self._prefix}{name}"

    def _get_container(self):
        if self._container is not None:
            return self._container

        from azure.core.exceptions import HttpResponseError, ResourceExistsError
        from azure.storage.blob import BlobServiceClient

        if self._connection_string:
            service = BlobServiceClient.from_connection_string(self._connection_string)
        else:
            from azure.identity import DefaultAzureCredential

            service = BlobServiceClient(
                account_url=self._account_url, credential=DefaultAzureCredential()
            )

        container = service.get_container_client(self._container_name)
        try:
            container.create_container()
        except ResourceExistsError:
            pass
        except HttpResponseError:
            # e.g. the identity is scoped to an existing container and may not
            # create one. Let the actual read/write surface any real problem.
            pass
        self._container = container
        return container

    def read(self, name: str) -> Document:
        from azure.core.exceptions import ResourceNotFoundError

        blob = self._get_container().get_blob_client(self._blob_name(name))
        try:
            download = blob.download_blob()
            raw = download.readall()
        except ResourceNotFoundError:
            return Document(data=None, etag=None)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return Document(data=None, etag=None)
        return Document(data=data, etag=download.properties.etag)

    def write(self, name: str, data: Any, *, etag: Optional[str]) -> Optional[str]:
        from azure.core import MatchConditions
        from azure.core.exceptions import (
            ResourceExistsError,
            ResourceModifiedError,
            ResourceNotFoundError,
        )

        blob = self._get_container().get_blob_client(self._blob_name(name))
        payload = _serialise(data)
        try:
            if etag is None:
                # Create-only: fails if another replica got there first.
                result = blob.upload_blob(payload, overwrite=False)
            else:
                result = blob.upload_blob(
                    payload,
                    overwrite=True,
                    etag=etag,
                    match_condition=MatchConditions.IfNotModified,
                )
        except (ResourceExistsError, ResourceModifiedError, ResourceNotFoundError) as exc:
            raise StaleWriteError(f"{name} changed since it was read") from exc
        return (result or {}).get("etag")


_STORE_CACHE: dict[tuple, JsonStore] = {}


def _store_key() -> tuple:
    return (
        os.getenv("MM_BLOB_CONNECTION_STRING") or "",
        os.getenv("MM_BLOB_ACCOUNT_URL") or "",
        os.getenv("MM_BLOB_CONTAINER") or "",
        os.getenv("MM_BLOB_PREFIX") or "",
        os.getenv("MM_DATA_DIR") or "",
    )


def get_store() -> JsonStore:
    """Return the configured store, building it on first use per environment."""
    key = _store_key()
    cached = _STORE_CACHE.get(key)
    if cached is not None:
        return cached

    connection_string = os.getenv("MM_BLOB_CONNECTION_STRING")
    account_url = os.getenv("MM_BLOB_ACCOUNT_URL")
    if connection_string or account_url:
        store: JsonStore = BlobJsonStore(
            account_url=account_url,
            connection_string=connection_string,
            container=os.getenv("MM_BLOB_CONTAINER") or DEFAULT_CONTAINER,
            prefix=os.getenv("MM_BLOB_PREFIX") or "",
        )
    else:
        store = LocalJsonStore(os.getenv("MM_DATA_DIR"))

    _STORE_CACHE[key] = store
    return store


def set_store(store: Optional[JsonStore]) -> None:
    """Override the store for the current environment (tests)."""
    if store is None:
        _STORE_CACHE.pop(_store_key(), None)
    else:
        _STORE_CACHE[_store_key()] = store


def reset_store() -> None:
    """Drop every cached backend so the next call re-reads the environment."""
    _STORE_CACHE.clear()


def read_json(name: str, *, default: Any = None, seed_from_package: bool = False) -> Document:
    """Read a document, optionally seeding from the packaged copy of the file.

    Reads degrade gracefully: if the store is unreachable or the identity isn't
    authorised, we log and fall back to the packaged/default value rather than
    raising. Reads happen during agent construction (profile context), so a
    storage blip must not take the whole agent down. Writes still raise — the
    caller needs to know an update didn't land.
    """
    try:
        document = get_store().read(name)
    except Exception:  # noqa: BLE001 - never let a read break the agent
        logger.warning("could not read %s from storage; using default", name, exc_info=True)
        document = Document(data=None, etag=None)

    if document.exists:
        return document

    if seed_from_package:
        packaged = _packaged_default(name)
        if packaged is not None:
            # etag stays None: the first write still has to create the blob.
            return Document(data=packaged, etag=None)

    return Document(data=default, etag=None)


def update_json(
    name: str,
    mutator: Callable[[Any], Any],
    *,
    default: Any,
    seed_from_package: bool = False,
    attempts: int = MAX_WRITE_ATTEMPTS,
) -> Tuple[Any, Any]:
    """Read-modify-write ``name`` with optimistic concurrency.

    ``mutator`` receives the current document (or a fresh ``default``), mutates
    it in place and may return a summary of what it did. Returns
    ``(document, mutator_result)``. On a concurrent write the whole cycle is
    retried against the newer document, so no update is lost.
    """
    last_error: Optional[StaleWriteError] = None

    for _ in range(max(1, attempts)):
        document = read_json(name, seed_from_package=seed_from_package)
        data = document.data
        if data is None:
            data = json.loads(json.dumps(default))  # fresh copy per attempt

        result = mutator(data)
        try:
            get_store().write(name, data, etag=document.etag)
        except StaleWriteError as exc:
            last_error = exc
            continue
        return data, result

    raise last_error or StaleWriteError(f"could not write {name}")
