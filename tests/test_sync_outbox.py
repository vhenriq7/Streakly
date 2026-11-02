"""Fila local de sincronização: transações, snapshots e confirmações."""

import json
import sqlite3
from uuid import UUID
from datetime import date

import storage
from test_timer import TemporaryDatabaseTest, at


class SyncOutboxTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()

    def test_study_changes_are_captured_atomically_and_read_without_removing(self):
        storage.add_subject("Python")
        subject_id = storage.list_subject_options()[0][0]
        session = storage.create_manual_session(
            subject_id, "Leitura", at(0), 60, "SQLite", at=at(100),
        )

        operations = storage.read_sync_outbox()
        self.assertEqual(
            [(operation.entity, operation.operation) for operation in operations],
            [("subjects", "upsert"), ("sessions", "upsert"),
             ("session_events", "upsert"), ("session_events", "upsert")],
        )
        self.assertTrue(all(UUID(operation.id).version == 4 for operation in operations))
        self.assertTrue(all(operation.created_at.tzinfo is not None for operation in operations))
        session_operation = next(item for item in operations if item.entity == "sessions")
        self.assertEqual(session_operation.entity_id, session.id)
        self.assertEqual(session_operation.payload["topic"], "SQLite")
        self.assertEqual(len(storage.read_sync_outbox()), len(operations))

    def test_timer_ticks_do_not_create_redundant_operations(self):
        storage.add_subject("Python")
        subject_id = storage.list_subject_options()[0][0]
        session = storage.start_session(subject_id, "Leitura", at=at(0))
        before = len(storage.read_sync_outbox())
        with storage._connection(write=True) as connection:
            connection.execute(
                "UPDATE sessions SET duration_seconds = 1 WHERE id = ?", (session.id,),
            )
        self.assertEqual(len(storage.read_sync_outbox()), before)

    def test_pomodoro_ticks_are_ignored_but_phase_changes_are_queued(self):
        storage.add_subject("Python")
        subject_id = storage.list_subject_options()[0][0]
        session = storage.start_session(
            subject_id, "Leitura", mode="pomodoro", focus_seconds=10,
            break_seconds=5, at=at(0),
        )
        before = len(storage.read_sync_outbox())
        storage.refresh_active_session(at=at(2))
        self.assertEqual(len(storage.read_sync_outbox()), before)
        storage.pause_session(session.id, at=at(3))
        queued = storage.read_sync_outbox()
        self.assertGreater(len(queued), before)
        self.assertIn("pomodoro_state", [item.entity for item in queued[before:]])

    def test_goal_updates_and_deletions_are_captured(self):
        storage.add_subject("Python")
        subject_id = storage.list_subject_options()[0][0]
        storage.set_default_daily_goal(3600, None, date(2026, 10, 28), at=at(0))
        storage.set_daily_goal_override(date(2026, 10, 29), None, True, None)
        storage.delete_subject(storage.read_subject_deletion_preview(subject_id))
        operations = storage.read_sync_outbox()
        self.assertIn(("daily_goal_defaults", "upsert"),
                      [(item.entity, item.operation) for item in operations])
        self.assertIn(("daily_goal_overrides", "upsert"),
                      [(item.entity, item.operation) for item in operations])
        self.assertEqual(operations[-1].operation, "delete")
        self.assertEqual(operations[-1].entity, "subjects")

    def test_schema_8_migration_preserves_rows_without_creating_historical_work(self):
        storage.add_subject("Python")
        subject_id = storage.list_subject_options()[0][0]
        saved = storage.create_manual_session(subject_id, "Leitura", at(0), 60, at=at(100))
        self.remove_sync_outbox()
        self.execute("PRAGMA user_version = 8")

        storage.initialize_database()

        self.assertEqual(storage.get_session(saved.id), saved)
        self.assertEqual(storage.read_sync_outbox(), ())
        self.assertEqual(self.execute("PRAGMA user_version"), [(storage.SCHEMA_VERSION,)])

    def test_queue_write_rolls_back_with_the_local_change(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with storage._connection(write=True) as connection:
                connection.execute(
                    "INSERT INTO subjects (id, name) VALUES ('rolled-back', 'Python')",
                )
                self.assertEqual(connection.execute(
                    "SELECT count(*) FROM sync_outbox",
                ).fetchone()[0], 1)
                raise sqlite3.IntegrityError("falha simulada da gravação local")
        self.assertEqual(storage.read_sync_outbox(), ())
        self.assertEqual(storage.list_subject_options(), [])

    def test_acknowledgement_removes_only_confirmed_operations_and_is_repeatable(self):
        storage.add_subject("Python")
        storage.add_subject("SQL")
        operations = storage.read_sync_outbox()
        self.assertEqual(storage.acknowledge_sync_operations((operations[0].id,)), 1)
        remaining = storage.read_sync_outbox()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].payload["name"], "SQL")
        self.assertEqual(storage.acknowledge_sync_operations((operations[0].id,)), 0)
        self.assertEqual(storage.acknowledge_sync_operations((remaining[0].id,)), 1)
        self.assertEqual(storage.read_sync_outbox(), ())

    def test_limit_is_validated_and_json_payloads_are_read_as_objects(self):
        storage.add_subject("Python")
        operation = storage.read_sync_outbox()[0]
        self.assertEqual(
            json.loads(json.dumps(operation.payload)),
            {"id": operation.entity_id, "name": "Python"},
        )
        for invalid in (0, -1, 1001, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                storage.read_sync_outbox(invalid)
        with self.assertRaises(ValueError):
            storage.acknowledge_sync_operations([operation.id])

