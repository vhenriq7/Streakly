"""Persistência local do cursor remoto e da caixa de entrada de sincronização."""

import sqlite3
import unittest
from uuid import uuid4

import storage
from storage import RemoteSyncOperation
from test_timer import TemporaryDatabaseTest


def remote(sequence, *, operation_id=None, payload=None):
    return RemoteSyncOperation(
        sequence=sequence,
        id=operation_id or str(uuid4()),
        entity="subjects",
        entity_id=str(uuid4()),
        operation="upsert",
        payload=payload or {"name": "Python"},
    )


class SyncInboxTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        self.user_id = str(uuid4())

    def test_schema_9_migration_adds_internal_tables_without_changing_study_or_outbox(self):
        storage.add_subject("Python")
        subject_id = storage.list_subject_options()[0][0]
        outbox_before = storage.read_sync_outbox()
        self.execute("DROP TABLE sync_metadata")
        self.execute("DROP TABLE sync_inbox")
        self.execute("PRAGMA user_version = 9")

        storage.initialize_database()

        self.assertEqual(storage.list_subject_options()[0][0], subject_id)
        self.assertEqual(storage.read_sync_outbox(), outbox_before)
        self.assertEqual(storage.read_sync_cursor(self.user_id), 0)
        self.assertEqual(storage.read_remote_sync_inbox(), ())
        self.assertEqual(self.execute("PRAGMA user_version"), [(10,)])

    def test_binding_is_persistent_and_refuses_a_different_user(self):
        bound = storage.bind_sync_account(self.user_id)
        reopened = storage.bind_sync_account(self.user_id)
        self.assertEqual(bound.user_id, reopened.user_id)
        self.assertEqual((bound.remote_cursor, reopened.remote_cursor), (0, 0))
        self.assertEqual(storage.read_sync_cursor(self.user_id), 0)
        with self.assertRaisesRegex(ValueError, "outra conta"):
            storage.bind_sync_account(str(uuid4()))
        with self.assertRaisesRegex(ValueError, "não corresponde"):
            storage.read_sync_cursor(str(uuid4()))

    def test_remote_page_and_cursor_are_saved_atomically_and_without_outbox_echo(self):
        storage.bind_sync_account(self.user_id)
        operations = (remote(4), remote(9))
        cursor = storage.accept_remote_sync_page(self.user_id, operations)

        self.assertEqual(cursor, 9)
        self.assertEqual(storage.read_sync_cursor(self.user_id), 9)
        self.assertEqual(storage.read_remote_sync_inbox(), operations)
        self.assertEqual(storage.read_sync_outbox(), ())
        self.assertEqual(storage.list_subject_options(), [])

    def test_replaying_a_saved_page_is_idempotent_and_keeps_the_cursor(self):
        storage.bind_sync_account(self.user_id)
        page = (remote(2), remote(7))
        self.assertEqual(storage.accept_remote_sync_page(self.user_id, page), 7)
        self.assertEqual(storage.accept_remote_sync_page(self.user_id, page), 7)
        self.assertEqual(storage.read_remote_sync_inbox(), page)
        self.assertEqual(storage.read_sync_cursor(self.user_id), 7)

    def test_empty_page_updates_sync_time_without_moving_cursor(self):
        first = storage.bind_sync_account(self.user_id)
        self.assertEqual(storage.accept_remote_sync_page(self.user_id, ()), 0)
        refreshed = storage.bind_sync_account(self.user_id)
        self.assertEqual(refreshed.remote_cursor, 0)
        self.assertGreaterEqual(refreshed.updated_at, first.updated_at)

    def test_page_rejects_wrong_account_or_unordered_and_duplicate_operations(self):
        first, second = remote(3), remote(8)
        with self.assertRaisesRegex(ValueError, "Vincule a conta"):
            storage.accept_remote_sync_page(self.user_id, (first,))
        storage.bind_sync_account(self.user_id)
        with self.assertRaisesRegex(ValueError, "não corresponde"):
            storage.accept_remote_sync_page(str(uuid4()), (first,))
        with self.assertRaisesRegex(ValueError, "ordenadas"):
            storage.accept_remote_sync_page(self.user_id, (second, first))
        with self.assertRaisesRegex(ValueError, "repetida"):
            storage.accept_remote_sync_page(
                self.user_id, (first, RemoteSyncOperation(
                    first.sequence, str(uuid4()), first.entity, first.entity_id,
                    first.operation, first.payload,
                )),
            )
        self.assertEqual(storage.read_sync_cursor(self.user_id), 0)
        self.assertEqual(storage.read_remote_sync_inbox(), ())

    def test_same_cursor_with_different_content_is_rejected(self):
        storage.bind_sync_account(self.user_id)
        saved = remote(6)
        storage.accept_remote_sync_page(self.user_id, (saved,))
        changed = RemoteSyncOperation(
            saved.sequence, saved.id, saved.entity, saved.entity_id,
            saved.operation, {"name": "SQL"},
        )
        with self.assertRaisesRegex(ValueError, "outra operação"):
            storage.accept_remote_sync_page(self.user_id, (changed,))
        self.assertEqual(storage.read_remote_sync_inbox(), (saved,))

    def test_storage_failure_rolls_back_page_and_cursor_together(self):
        storage.bind_sync_account(self.user_id)
        self.execute(
            """CREATE TRIGGER fail_sync_inbox BEFORE INSERT ON sync_inbox
            BEGIN SELECT RAISE(ABORT, 'simulated failure'); END""",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            storage.accept_remote_sync_page(self.user_id, (remote(5),))
        self.assertEqual(storage.read_sync_cursor(self.user_id), 0)
        self.assertEqual(storage.read_remote_sync_inbox(), ())

    def test_inbox_is_not_part_of_portable_backup(self):
        storage.bind_sync_account(self.user_id)
        storage.accept_remote_sync_page(self.user_id, (remote(3),))
        snapshot = storage.read_backup_snapshot()
        self.assertNotIn("sync_metadata", snapshot["tables"])
        self.assertNotIn("sync_inbox", snapshot["tables"])

    def test_inbox_limit_is_validated(self):
        for invalid in (0, -1, 1001, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                storage.read_remote_sync_inbox(invalid)

    def test_remote_page_validation_rejects_invalid_ids_and_non_json_payloads(self):
        storage.bind_sync_account(self.user_id)
        item = remote(3, payload={"bad": float("nan")})
        with self.assertRaisesRegex(ValueError, "JSON"):
            storage.accept_remote_sync_page(self.user_id, (item,))
        invalid = RemoteSyncOperation(4, "not-an-id", "subjects", str(uuid4()),
                                      "upsert", {"name": "Python"})
        with self.assertRaisesRegex(ValueError, "inválida"):
            storage.accept_remote_sync_page(self.user_id, (invalid,))
        self.assertEqual(storage.read_sync_cursor(self.user_id), 0)


if __name__ == "__main__":
    unittest.main()
