"""Storage tests.

The bug these guard against: the shopping list was written to an ephemeral
container path, so items added in one conversation were gone in the next.
"""

import asyncio
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tools import storage
from tools.storage import (
    BlobJsonStore,
    Document,
    LocalJsonStore,
    StaleWriteError,
    read_json,
    update_json,
)

_BLOB_ENV = [
    "MM_BLOB_ACCOUNT_URL",
    "MM_BLOB_CONNECTION_STRING",
    "MM_BLOB_CONTAINER",
    "MM_BLOB_PREFIX",
    "MM_DATA_DIR",
]


class FakeRemoteStore:
    """In-memory stand-in for blob storage with the same ETag semantics.

    Two instances sharing one ``backing`` dict model two container replicas
    talking to the same durable store.
    """

    def __init__(self, backing: dict):
        self.backing = backing

    def read(self, name: str) -> Document:
        record = self.backing.get(name)
        if record is None:
            return Document(data=None, etag=None)
        payload, etag = record
        return Document(data=json.loads(payload), etag=etag)

    def write(self, name, data, *, etag):
        record = self.backing.get(name)
        current_etag = record[1] if record else None
        if current_etag != etag:
            raise StaleWriteError(f"{name} changed since it was read")
        new_etag = uuid4().hex
        self.backing[name] = (json.dumps(data), new_etag)
        return new_etag


class StorageTestCase(unittest.TestCase):
    """Isolate each test from the developer's real environment and caches."""

    def setUp(self):
        cleared = {key: "" for key in _BLOB_ENV}
        patcher = patch.dict(os.environ, cleared)
        patcher.start()
        for key in _BLOB_ENV:
            os.environ.pop(key, None)
        self.addCleanup(patcher.stop)
        storage.reset_store()
        self.addCleanup(storage.reset_store)


class LocalStoreTests(StorageTestCase):
    def test_writes_land_in_the_configured_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["MM_DATA_DIR"] = temp_dir
            update_json("shopping_list.json", lambda items: items.append("milk"), default=[])

            written = Path(temp_dir) / "shopping_list.json"
            self.assertTrue(written.is_file())
            self.assertEqual(json.loads(written.read_text(encoding="utf-8")), ["milk"])

    def test_document_survives_a_process_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            LocalJsonStore(temp_dir).write("list.json", ["nappies"], etag=None)

            # A brand-new store object, as a restarted process would build.
            reloaded = LocalJsonStore(temp_dir).read("list.json")

            self.assertEqual(reloaded.data, ["nappies"])

    def test_write_with_a_stale_etag_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalJsonStore(temp_dir)
            stale = store.write("list.json", ["milk"], etag=None)
            store.write("list.json", ["milk", "bread"], etag=stale)

            with self.assertRaises(StaleWriteError):
                store.write("list.json", ["something else"], etag=stale)

    def test_unreadable_document_does_not_crash_the_tool(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "list.json").write_text("{ truncated", encoding="utf-8")

            self.assertIsNone(LocalJsonStore(temp_dir).read("list.json").data)


class OptimisticConcurrencyTests(StorageTestCase):
    def test_concurrent_write_is_retried_and_neither_update_is_lost(self):
        backing: dict = {}
        this_replica = FakeRemoteStore(backing)
        other_replica = FakeRemoteStore(backing)
        storage.set_store(this_replica)
        interfered = []

        def mutate(items: list):
            if not interfered:
                # Another replica commits between our read and our write.
                interfered.append(True)
                document = other_replica.read("list.json")
                other_replica.write("list.json", ["bread"], etag=document.etag)
            items.append("milk")

        items, _ = update_json("list.json", mutate, default=[])

        self.assertEqual(items, ["bread", "milk"])
        self.assertTrue(interfered)

    def test_gives_up_after_repeated_conflicts(self):
        backing: dict = {}
        storage.set_store(FakeRemoteStore(backing))
        other_replica = FakeRemoteStore(backing)

        def mutate(items: list):
            document = other_replica.read("list.json")
            other_replica.write("list.json", ["conflict"], etag=document.etag)
            items.append("milk")

        with self.assertRaises(StaleWriteError):
            update_json("list.json", mutate, default=[], attempts=3)


class BackendSelectionTests(StorageTestCase):
    def test_blob_backend_is_used_when_an_account_url_is_configured(self):
        os.environ["MM_BLOB_ACCOUNT_URL"] = "https://example.blob.core.windows.net"

        self.assertIsInstance(storage.get_store(), BlobJsonStore)

    def test_local_backend_is_used_when_no_blob_storage_is_configured(self):
        self.assertIsInstance(storage.get_store(), LocalJsonStore)

    def test_backend_is_resolved_lazily_not_at_import_time(self):
        self.assertIsInstance(storage.get_store(), LocalJsonStore)

        os.environ["MM_BLOB_ACCOUNT_URL"] = "https://example.blob.core.windows.net"

        self.assertIsInstance(storage.get_store(), BlobJsonStore)


class PackagedSeedTests(StorageTestCase):
    def test_packaged_default_seeds_a_document_that_has_never_been_written(self):
        storage.set_store(FakeRemoteStore({}))

        seeded = read_json("family_profile.json", seed_from_package=True)

        self.assertIsInstance(seeded.data, dict)
        # No ETag: the first write still has to create the document.
        self.assertIsNone(seeded.etag)

    def test_missing_document_without_a_packaged_copy_falls_back_to_default(self):
        storage.set_store(FakeRemoteStore({}))

        self.assertEqual(read_json("no_such_file.json", default=[]).data, [])


class ShoppingListPersistenceTests(StorageTestCase):
    """The regression test for the reported bug."""

    def _call(self, name, params):
        from tools._dual import TOOL_IMPLS

        return asyncio.run(TOOL_IMPLS[name](params))

    def test_items_added_in_one_conversation_are_readable_in_the_next(self):
        from tools.shopping_list import AddToListParams, GetShoppingListParams

        backing: dict = {}
        storage.set_store(FakeRemoteStore(backing))
        self._call(
            "add_to_shopping_list",
            AddToListParams(items=["nappies size 4", "calpol"], category="baby"),
        )

        # A later conversation, served by a different replica/process.
        storage.set_store(FakeRemoteStore(backing))
        listed = self._call("get_shopping_list", GetShoppingListParams())

        self.assertIn("nappies size 4", listed)
        self.assertIn("calpol", listed)

    def test_marking_bought_persists_too(self):
        from tools.shopping_list import (
            AddToListParams,
            GetShoppingListParams,
            MarkBoughtParams,
        )

        backing: dict = {}
        storage.set_store(FakeRemoteStore(backing))
        self._call("add_to_shopping_list", AddToListParams(items=["milk", "bread"]))
        self._call("mark_bought", MarkBoughtParams(items=["milk"]))

        storage.set_store(FakeRemoteStore(backing))
        listed = self._call("get_shopping_list", GetShoppingListParams())

        self.assertNotIn("milk", listed)
        self.assertIn("bread", listed)

    def test_legacy_entries_without_a_bought_flag_are_still_listed(self):
        from tools.shopping_list import GetShoppingListParams

        backing = {"shopping_list.json": (json.dumps([{"item": "olive oil"}]), "etag-1")}
        storage.set_store(FakeRemoteStore(backing))

        self.assertIn("olive oil", self._call("get_shopping_list", GetShoppingListParams()))


class FamilyProfilePersistenceTests(StorageTestCase):
    def _call(self, name, params):
        from tools._dual import TOOL_IMPLS

        return asyncio.run(TOOL_IMPLS[name](params))

    def test_saved_child_is_recoverable_in_a_later_conversation(self):
        from tools.memory import GetProfileParams, UpdateChildParams

        backing: dict = {}
        storage.set_store(FakeRemoteStore(backing))
        self._call("save_child", UpdateChildParams(name="Rosie", age="2"))

        storage.set_store(FakeRemoteStore(backing))
        profile = self._call("get_family_profile", GetProfileParams())

        self.assertIn("Rosie", profile)

    def test_two_saves_do_not_clobber_each_other(self):
        from tools.memory import GetProfileParams, UpdateChildParams

        storage.set_store(FakeRemoteStore({}))
        self._call("save_child", UpdateChildParams(name="Rosie", age="2"))
        self._call("save_child", UpdateChildParams(name="Alfie", age="4"))

        profile = self._call("get_family_profile", GetProfileParams())

        self.assertIn("Rosie", profile)
        self.assertIn("Alfie", profile)


class BlobBackendTests(StorageTestCase):
    """The production path: verify the ETag preconditions actually get sent."""

    def _store_with_fake_container(self, container):
        store = BlobJsonStore(account_url="https://example.blob.core.windows.net")
        store._container = container
        return store

    def test_update_sends_an_if_match_precondition(self):
        from azure.core import MatchConditions

        blob = unittest.mock.MagicMock()
        blob.upload_blob.return_value = {"etag": "etag-2"}
        container = unittest.mock.MagicMock()
        container.get_blob_client.return_value = blob

        new_etag = self._store_with_fake_container(container).write(
            "list.json", ["milk"], etag="etag-1"
        )

        _, kwargs = blob.upload_blob.call_args
        self.assertEqual(kwargs["etag"], "etag-1")
        self.assertEqual(kwargs["match_condition"], MatchConditions.IfNotModified)
        self.assertTrue(kwargs["overwrite"])
        self.assertEqual(new_etag, "etag-2")

    def test_first_write_is_create_only(self):
        blob = unittest.mock.MagicMock()
        blob.upload_blob.return_value = {"etag": "etag-1"}
        container = unittest.mock.MagicMock()
        container.get_blob_client.return_value = blob

        self._store_with_fake_container(container).write("list.json", ["milk"], etag=None)

        _, kwargs = blob.upload_blob.call_args
        self.assertFalse(kwargs["overwrite"])
        self.assertNotIn("etag", kwargs)

    def test_precondition_failure_becomes_a_stale_write(self):
        from azure.core.exceptions import ResourceModifiedError

        blob = unittest.mock.MagicMock()
        blob.upload_blob.side_effect = ResourceModifiedError("412")
        container = unittest.mock.MagicMock()
        container.get_blob_client.return_value = blob

        with self.assertRaises(StaleWriteError):
            self._store_with_fake_container(container).write(
                "list.json", ["milk"], etag="etag-1"
            )

    def test_missing_blob_reads_as_a_absent_document(self):
        from azure.core.exceptions import ResourceNotFoundError

        blob = unittest.mock.MagicMock()
        blob.download_blob.side_effect = ResourceNotFoundError("404")
        container = unittest.mock.MagicMock()
        container.get_blob_client.return_value = blob

        self.assertIsNone(self._store_with_fake_container(container).read("list.json").data)

    def test_blob_names_are_prefixed(self):
        blob = unittest.mock.MagicMock()
        blob.download_blob.return_value.readall.return_value = b"[]"
        container = unittest.mock.MagicMock()
        container.get_blob_client.return_value = blob
        store = BlobJsonStore(
            account_url="https://example.blob.core.windows.net",
            prefix="families/smith/",
        )
        store._container = container

        store.read("list.json")

        container.get_blob_client.assert_called_once_with("families/smith/list.json")


if __name__ == "__main__":
    unittest.main()
