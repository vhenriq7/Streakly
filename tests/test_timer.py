"""Verifica o cronômetro sem abrir a interface nem usar o banco pessoal."""

import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import storage
from timer import SessionEvent, elapsed_seconds, format_duration


START = datetime(2026, 10, 28, 12, tzinfo=UTC)


def at(seconds: int) -> datetime:
    return START + timedelta(seconds=seconds)


class TimerCalculationTests(unittest.TestCase):
    def test_sums_study_intervals_and_excludes_pauses(self):
        events = (
            SessionEvent("start", at(0)),
            SessionEvent("pause", at(60)),
            SessionEvent("resume", at(600)),
            SessionEvent("pause", at(630)),
            SessionEvent("resume", at(900)),
        )
        self.assertEqual(elapsed_seconds(events, at(920)), 110)
        finished = events + (SessionEvent("finish", at(920)),)
        self.assertEqual(elapsed_seconds(finished, at(2000)), 110)

    def test_offset_and_midnight_do_not_change_effective_duration(self):
        local_zone = timezone(timedelta(hours=-3))
        before_midnight = datetime(2026, 10, 28, 23, 59, tzinfo=local_zone)
        events = (
            SessionEvent("start", before_midnight),
            SessionEvent("finish", before_midnight.astimezone(UTC) + timedelta(minutes=2)),
        )
        self.assertEqual(elapsed_seconds(events, at(0)), 120)

    def test_display_handles_long_sessions_and_clock_moving_backwards(self):
        self.assertEqual(format_duration(25 * 3600 + 61.9), "25:01:01")
        self.assertEqual(elapsed_seconds((SessionEvent("start", at(10)),), at(0)), 0)


class TemporaryDatabaseTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="streakly-timer-test-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "test.sqlite3"
        path_patch = patch.object(storage, "database_path", return_value=self.path)
        path_patch.start()
        self.addCleanup(path_patch.stop)

    def execute(self, sql: str, parameters=()):
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                return connection.execute(sql, parameters).fetchall()

    def row_counts(self):
        return tuple(
            self.execute(f"SELECT count(*) FROM {table}")[0][0]
            for table in ("sessions", "session_events")
        )


class SessionStorageTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        storage.add_subject("Python")
        self.subject_id = storage.list_subject_options()[0][0]

    def start(self, **kwargs):
        return storage.start_session(self.subject_id, "Leitura", at=at(0), **kwargs)

    def in_new_process(self, action: str, seconds: int):
        # O processo novo não compartilha variáveis nem conexões SQLite com o teste.
        script = """
import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import storage
from timer import elapsed_seconds

with patch.object(storage, "database_path", return_value=Path(sys.argv[1])):
    storage.initialize_database()
    session = storage.get_active_session()
    moment = datetime.fromisoformat(sys.argv[3])
    if sys.argv[2] != "read":
        operation = getattr(storage, sys.argv[2] + "_session")
        session = operation(session.id, at=moment)
    print(json.dumps({
        "id": session.id,
        "status": session.status,
        "elapsed": elapsed_seconds(session.events, moment),
        "stored": session.duration_seconds,
    }))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path), action, at(seconds).isoformat()],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        return json.loads(result.stdout)

    def test_reopen_running_and_paused_then_finish_in_new_process(self):
        session = self.start(topic="Laços")
        reopened = self.in_new_process("read", 90)
        self.assertEqual((reopened["id"], reopened["status"], reopened["elapsed"]),
                         (session.id, "running", 90))

        storage.pause_session(session.id, at=at(90))
        reopened = self.in_new_process("read", 600)
        self.assertEqual((reopened["status"], reopened["elapsed"]), ("paused", 90))
        resumed = self.in_new_process("resume", 600)
        self.assertEqual((resumed["status"], resumed["stored"]), ("running", 90))
        finished = self.in_new_process("finish", 630)
        self.assertEqual((finished["status"], finished["stored"]), ("finished", 120))

        saved = storage.get_session(session.id)
        self.assertEqual(saved.duration_seconds, 120)
        self.assertEqual((saved.topic, saved.ended_at), ("Laços", at(630)))
        self.assertIsNone(storage.get_active_session())
        another = storage.start_session(self.subject_id, "Exercícios", at=at(700))
        self.assertNotEqual(another.id, saved.id)
        self.assertEqual(storage.get_session(saved.id), saved)

    def test_two_concurrent_starts_create_only_one_session(self):
        barrier = threading.Barrier(2)

        def try_start():
            barrier.wait(timeout=5)
            try:
                return self.start().id
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            ids = list(executor.map(lambda _: try_start(), range(2)))
        successful_ids = [session_id for session_id in ids if session_id is not None]
        self.assertEqual(len(successful_ids), 1)
        self.assertEqual(storage.get_active_session().id, successful_ids[0])
        self.assertEqual(self.row_counts(), (1, 1))

    def test_invalid_transitions_and_older_timestamps_preserve_state(self):
        session = self.start()
        attempts = (
            (storage.resume_session, at(1)),
            (storage.pause_session, at(-1)),
            (storage.finish_session, datetime(2026, 10, 28, 12)),
        )
        for operation, moment in attempts:
            with self.subTest(operation=operation.__name__, moment=moment):
                with self.assertRaises(ValueError):
                    operation(session.id, at=moment)
                self.assertEqual(storage.get_session(session.id), session)

        paused = storage.pause_session(session.id, at=at(10))
        for operation, moment in (
            (storage.pause_session, at(11)),
            (storage.resume_session, at(9)),
            (storage.finish_session, at(9)),
        ):
            with self.subTest(operation=operation.__name__, status="paused"):
                with self.assertRaises(ValueError):
                    operation(session.id, at=moment)
                self.assertEqual(storage.get_session(session.id), paused)
        with self.assertRaises(ValueError):
            storage.start_session(self.subject_id, "Aula", at=at(20))
        self.assertEqual(storage.get_active_session(), paused)

        finished = storage.finish_session(session.id, at=at(20))
        self.assertEqual(finished.duration_seconds, 10)
        for operation in (storage.pause_session, storage.resume_session, storage.finish_session):
            with self.subTest(operation=operation.__name__, status="finished"):
                with self.assertRaises(ValueError):
                    operation(session.id, at=at(30))
                self.assertEqual(storage.get_session(session.id), finished)

    def test_event_failure_rolls_back_new_session_and_existing_transition(self):
        trigger = """
            CREATE TRIGGER reject_event BEFORE INSERT ON session_events
            BEGIN SELECT RAISE(ABORT, 'falha de gravação simulada'); END
        """
        self.execute(trigger)
        with self.assertRaises(sqlite3.DatabaseError):
            self.start()
        self.assertEqual(self.row_counts(), (0, 0))
        self.assertIsNone(storage.get_active_session())

        self.execute("DROP TRIGGER reject_event")
        session = self.start()
        self.execute(trigger)
        with self.assertRaises(sqlite3.DatabaseError):
            storage.pause_session(session.id, at=at(60))
        self.assertEqual(storage.get_session(session.id), session)
        self.assertEqual(self.row_counts(), (1, 1))
        self.execute("DROP TRIGGER reject_event")
        self.assertEqual(storage.pause_session(session.id, at=at(60)).duration_seconds, 60)

    def test_equal_timestamps_preserve_event_order_after_reload(self):
        session = self.start()
        storage.pause_session(session.id, at=at(0))
        storage.resume_session(session.id, at=at(0))
        storage.finish_session(session.id, at=at(0))
        saved = storage.get_session(session.id)
        self.assertEqual([event.kind for event in saved.events],
                         ["start", "pause", "resume", "finish"])
        self.assertEqual(saved.duration_seconds, 0)

    def test_input_validation_and_duplicate_subject_names_use_ids(self):
        for subject_id, study_type, moment in (
            ("inexistente", "Leitura", at(0)),
            (self.subject_id, "   ", at(0)),
            (self.subject_id, "Leitura", datetime(2026, 10, 28, 12)),
        ):
            with self.subTest(subject_id=subject_id, study_type=study_type):
                with self.assertRaises(ValueError):
                    storage.start_session(subject_id, study_type, at=moment)
                self.assertEqual(self.row_counts(), (0, 0))

        storage.add_subject("Python")
        other_id = next(subject_id for subject_id, _ in storage.list_subject_options()
                        if subject_id != self.subject_id)
        local_zone = timezone(timedelta(hours=-3))
        session = storage.start_session(other_id, "  Aula  ", "  Laços  ",
                                        at=START.astimezone(local_zone))
        self.assertEqual((session.subject_id, session.study_type, session.topic),
                         (other_id, "Aula", "Laços"))
        self.assertEqual(session.started_at.tzinfo, UTC)
        self.assertEqual(session.started_at, START)


class MigrationTests(TemporaryDatabaseTest):
    def test_v1_migration_preserves_subject_identity_and_is_repeatable(self):
        original_id = "e6c56f09-60c4-459c-b9d4-a033cb135b4d"
        self.execute("CREATE TABLE subjects (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        self.execute("INSERT INTO subjects VALUES (?, ?)", (original_id, "Python"))
        self.execute("PRAGMA user_version = 1")
        storage.initialize_database()
        storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(2,)])
        self.assertEqual(storage.list_subject_options(), [(original_id, "Python")])
        session = storage.start_session(original_id, "Leitura", at=at(0))
        self.assertEqual(session.subject_id, original_id)

    def test_newer_database_version_is_rejected_without_changing_data(self):
        storage.initialize_database()
        storage.add_subject("SQL")
        subjects = self.execute("SELECT id, name FROM subjects")
        self.execute("PRAGMA user_version = 99")
        with self.assertRaises(ValueError):
            storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(99,)])
        self.assertEqual(self.execute("SELECT id, name FROM subjects"), subjects)


if __name__ == "__main__":
    unittest.main()
