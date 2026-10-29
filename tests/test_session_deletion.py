"""Exclusões confirmadas usando somente bancos temporários e relógio simulado."""

import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import storage
from test_timer import TemporaryDatabaseTest, at


class FinishedSessionDeletionTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        for name in ("Python", "SQL"):
            storage.add_subject(name)
        self.subjects = {name: key for key, name in storage.list_subject_options()}

    def manual(self, start=0, duration=10, *, subject="Python", topic=None):
        return storage.create_manual_session(
            self.subjects[subject], "Leitura", at(start), duration, topic, at=at(1000),
        )

    def finished_pomodoro(self, start=200):
        active = storage.start_session(
            self.subjects["Python"], "Exercícios", "Pomodoro salvo", mode="pomodoro",
            at=at(start), focus_seconds=10, break_seconds=5,
        )
        storage.refresh_active_session(at=at(start + 10))
        return storage.finish_session(active.id, at=at(start + 20))

    def snapshot(self):
        return {
            **{table: self.execute(f"SELECT * FROM {table} ORDER BY 1")
               for table in ("subjects", "sessions", "session_events",
                             "subject_timer_settings", "pomodoro_state")},
            "schema": self.execute("PRAGMA user_version"),
        }

    def without_session(self, snapshot, session_id):
        return snapshot | {
            "sessions": [row for row in snapshot["sessions"] if row[0] != session_id],
            "session_events": [row for row in snapshot["session_events"] if row[1] != session_id],
            "pomodoro_state": [row for row in snapshot["pomodoro_state"] if row[0] != session_id],
        }

    def test_each_finished_source_removes_only_its_session_and_dependents(self):
        manual = self.manual()
        progressive = storage.start_session(self.subjects["Python"], "Aula", at=at(100))
        storage.pause_session(progressive.id, at=at(110))
        storage.resume_session(progressive.id, at=at(120))
        progressive = storage.finish_session(progressive.id, at=at(125))
        pomodoro = self.finished_pomodoro()
        active = storage.start_session(self.subjects["SQL"], "Aula", at=at(500))
        paused = storage.pause_session(active.id, at=at(510))

        self.assertEqual((manual.source, progressive.mode, pomodoro.mode),
                         ("manual", "progressive", "pomodoro"))
        for saved in (manual, progressive, pomodoro):
            with self.subTest(source=saved.source, mode=saved.mode):
                before = self.snapshot()
                self.assertIsNone(storage.delete_finished_session(saved))
                self.assertEqual(self.snapshot(), self.without_session(before, saved.id))
                self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])
                self.assertEqual(self.execute("PRAGMA user_version"), [(5,)])
                self.assertEqual(storage.get_active_session(), paused)
                self.assertNotIn(saved.id, [item.id for item in storage.read_session_history().sessions])
                with self.assertRaises(ValueError):
                    storage.get_session(saved.id)

    def test_deleting_a_midnight_session_recalculates_daily_totals_without_erasing_other_study(self):
        zone = timezone(timedelta(hours=-3))
        start = datetime(2026, 10, 28, 23, 50, tzinfo=zone)
        now = datetime(2026, 10, 29, 1, tzinfo=zone)
        saved = storage.create_manual_session(
            self.subjects["Python"], "Leitura", start, 20 * 60, at=now,
        )
        retained = storage.create_manual_session(
            self.subjects["SQL"], "Revisão", datetime(2026, 10, 29, 0, 30, tzinfo=zone),
            5 * 60, at=now,
        )
        previous_moment = datetime(2026, 10, 28, 23, 59, tzinfo=zone)
        self.assertEqual(storage.read_study_overview(at=previous_moment, tz=zone).total_seconds, 540)
        self.assertEqual(storage.read_study_overview(at=now, tz=zone).total_seconds, 900)
        storage.delete_finished_session(saved)
        self.assertEqual(storage.read_study_overview(at=previous_moment, tz=zone).total_seconds, 0)
        self.assertEqual(storage.read_study_overview(at=now, tz=zone).total_seconds, 300)
        self.assertEqual(storage.read_session_history().sessions, (retained,))
        self.assertEqual(self.row_counts(), (1, 2))

    def test_running_paused_and_ready_sessions_cannot_be_deleted_or_settled(self):
        active = storage.start_session(self.subjects["Python"], "Aula", at=at(0))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            storage.delete_finished_session(active)
        self.assertEqual(self.snapshot(), before)
        active = storage.pause_session(active.id, at=at(10))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            storage.delete_finished_session(active)
        self.assertEqual(self.snapshot(), before)
        storage.finish_session(active.id, at=at(20))

        active = storage.start_session(
            self.subjects["SQL"], "Aula", mode="pomodoro", at=at(100),
            focus_seconds=10, break_seconds=5,
        )
        for state in ("running", "paused"):
            if state == "paused":
                active = storage.pause_session(active.id, at=at(105))
            before = self.snapshot()
            with self.assertRaises(ValueError):
                storage.delete_finished_session(active)
            self.assertEqual(self.snapshot(), before)
        storage.resume_session(active.id, at=at(110))
        ready = storage.refresh_active_session(at=at(115))
        before = self.snapshot()
        with patch.object(storage, "refresh_active_session", side_effect=AssertionError("Consolidação inesperada")):
            with self.assertRaises(ValueError):
                storage.delete_finished_session(ready)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_active_session(), ready)

    def test_a_stale_snapshot_cannot_delete_a_session_changed_after_confirmation_was_prepared(self):
        saved = self.manual()
        updated = storage.update_finished_session(
            saved, self.subjects["SQL"], "Revisão", "Correção recente", at=at(1000),
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "(?i)atualize"):
            storage.delete_finished_session(saved)
        self.assertEqual(self.snapshot(), before)
        moved = storage.update_finished_session(
            updated, updated.subject_id, updated.study_type, updated.topic,
            started_at=at(20), duration_seconds=15, at=at(1000),
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "(?i)atualize"):
            storage.delete_finished_session(updated)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_session(saved.id), moved)
        storage.delete_finished_session(moved)
        self.assertEqual(self.row_counts(), (0, 0))

    def test_invalid_missing_and_repeated_deletions_leave_remaining_data_unchanged(self):
        saved = self.manual()
        retained = self.manual(100, 10, subject="SQL")
        before = self.snapshot()
        with patch.object(storage, "_connection", side_effect=AssertionError("Conexão inesperada")) as connection:
            for invalid in (None, True, "id", 2):
                with self.subTest(expected=invalid):
                    with self.assertRaises(ValueError):
                        storage.delete_finished_session(invalid)
            connection.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(ValueError):
            storage.delete_finished_session(replace(saved, id="sessão-inexistente"))
        self.assertEqual(self.snapshot(), before)
        storage.delete_finished_session(saved)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            storage.delete_finished_session(saved)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_session(retained.id), retained)

    def test_parent_delete_failure_rolls_back_events_pomodoro_state_and_all_other_data(self):
        saved = self.finished_pomodoro(0)
        active = storage.start_session(self.subjects["SQL"], "Aula", at=at(100))
        paused = storage.pause_session(active.id, at=at(110))
        before = self.snapshot()
        self.execute("""
            CREATE TRIGGER reject_session_delete BEFORE DELETE ON sessions
            BEGIN SELECT RAISE(ABORT, 'falha simulada na exclusão da sessão'); END
        """)
        with self.assertRaises(sqlite3.DatabaseError):
            storage.delete_finished_session(saved)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_active_session(), paused)
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])
        self.execute("DROP TRIGGER reject_session_delete")
        storage.delete_finished_session(saved)
        self.assertEqual(self.snapshot(), self.without_session(before, saved.id))

    def test_second_event_delete_failure_restores_the_pomodoro_state_and_preceding_event(self):
        saved = self.finished_pomodoro(0)
        before = self.snapshot()
        self.execute("""
            CREATE TRIGGER reject_second_event_delete BEFORE DELETE ON session_events
            WHEN OLD.sequence = 1
            BEGIN SELECT RAISE(ABORT, 'falha simulada no segundo evento'); END
        """)
        with self.assertRaises(sqlite3.DatabaseError):
            storage.delete_finished_session(saved)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_session(saved.id), saved)
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])
        self.execute("DROP TRIGGER reject_second_event_delete")
        storage.delete_finished_session(saved)
        self.assertEqual(self.row_counts(), (0, 0))

    def test_two_concurrent_confirmations_delete_the_session_only_once(self):
        saved = self.manual()
        retained = self.manual(100, 10, subject="SQL")
        before = self.snapshot()
        barrier = threading.Barrier(2)

        def attempt(_):
            barrier.wait(timeout=5)
            try:
                storage.delete_finished_session(saved)
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, range(2)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.snapshot(), self.without_session(before, saved.id))
        self.assertEqual(storage.get_session(retained.id), retained)
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_another_process_keeps_the_deletion_and_preserves_remaining_ids_and_preferences(self):
        saved = self.manual()
        retained = self.manual(100, 20, subject="SQL", topic="Funções 🚀")
        pomodoro = self.finished_pomodoro(200)
        active = storage.start_session(self.subjects["SQL"], "Aula", at=at(300))
        paused = storage.pause_session(active.id, at=at(310))
        storage.delete_finished_session(saved)
        before = self.snapshot()
        script = """
import json, sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    storage.initialize_database()
    page = storage.read_session_history()
    overview = storage.read_study_overview(at=datetime.fromisoformat(sys.argv[2]), tz=UTC)
    print(json.dumps([[[item.id, item.source, item.topic, item.duration_seconds]
                       for item in page.sessions], overview.total_seconds,
                      overview.active_session.id, overview.active_session.status]))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path), at(1000).isoformat()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(result.stdout), [
            [[pomodoro.id, "timer", "Pomodoro salvo", 10],
             [retained.id, "manual", "Funções 🚀", 20]],
            40, paused.id, "paused",
        ])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_session(retained.id), retained)
        self.assertEqual(storage.get_session(pomodoro.id), pomodoro)
        self.assertEqual(storage.get_active_session(), paused)
