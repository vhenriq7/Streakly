"""Ciclos com horários simulados, sem espera real nem uso do banco pessoal."""

import json
import sqlite3
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import storage
from test_timer import TemporaryDatabaseTest, at
from timer import TimerSettings, pomodoro_remaining_seconds, session_elapsed_seconds


class PomodoroTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        for name in ("Python", "SQL"):
            storage.add_subject(name)
        self.subjects = {name: key for key, name in storage.list_subject_options()}

    def start(self, subject="Python", **kwargs):
        return storage.start_session(
            self.subjects[subject], "Leitura", mode="pomodoro", at=at(0),
            focus_seconds=kwargs.get("focus_seconds", 10),
            break_seconds=kwargs.get("break_seconds", 5),
        )

    def test_deadline_and_manual_cycles_exclude_waiting_and_break(self):
        session = self.start()
        self.assertEqual(session_elapsed_seconds(session, at(1000)), 10)
        ready = storage.refresh_active_session(at=at(1000))
        self.assertEqual((ready.pomodoro.phase, ready.pomodoro.state, ready.status),
                         ("break", "ready", "paused"))
        self.assertEqual(ready.events[-1].occurred_at, at(10))
        self.assertEqual(storage.refresh_active_session(at=at(2000)), ready)
        with self.assertRaises(ValueError):
            self.start("SQL")

        running_break = storage.start_next_phase(session.id, at=at(2001))
        self.assertEqual(running_break.pomodoro.state, "running")
        ready_focus = storage.refresh_active_session(at=at(2010))
        self.assertEqual((ready_focus.pomodoro.phase, ready_focus.pomodoro.state),
                         ("focus", "ready"))
        self.assertEqual(ready_focus.events, ready.events)
        self.assertEqual(storage.refresh_active_session(at=at(3000)), ready_focus)
        storage.start_next_phase(session.id, at=at(3000))
        saved = storage.finish_session(session.id, at=at(3004))
        self.assertEqual(saved.duration_seconds, 14)
        self.assertEqual([event.kind for event in saved.events],
                         ["start", "pause", "resume", "finish"])
        self.assertIsNone(storage.get_active_session())

    def test_pause_and_resume_freeze_both_phase_countdowns(self):
        session = self.start()
        paused = storage.pause_session(session.id, at=at(4))
        self.assertEqual(pomodoro_remaining_seconds(paused.pomodoro, at(1000)), 6)
        self.assertEqual(session_elapsed_seconds(paused, at(1000)), 4)
        self.assertEqual(storage.refresh_active_session(at=at(1000)), paused)
        storage.resume_session(session.id, at=at(1000))
        ready = storage.refresh_active_session(at=at(1006))
        self.assertEqual(ready.duration_seconds, 10)

        storage.start_next_phase(session.id, at=at(1020))
        paused_break = storage.pause_session(session.id, at=at(1022))
        self.assertEqual(paused_break.events, ready.events)
        self.assertEqual(pomodoro_remaining_seconds(paused_break.pomodoro, at(1500)), 3)
        self.assertEqual(storage.refresh_active_session(at=at(1500)), paused_break)
        storage.resume_session(session.id, at=at(1500))
        ready_focus = storage.refresh_active_session(at=at(1503))
        self.assertEqual(ready_focus.duration_seconds, 10)
        self.assertEqual((ready_focus.pomodoro.phase, ready_focus.pomodoro.state),
                         ("focus", "ready"))

    def test_late_finish_without_polling_stores_exact_focus_deadline(self):
        session = self.start()
        finished = storage.finish_session(session.id, at=at(3600))
        self.assertEqual(finished.duration_seconds, 10)
        self.assertEqual(finished.ended_at, at(3600))
        self.assertEqual([(event.kind, event.occurred_at) for event in finished.events],
                         [("start", at(0)), ("pause", at(10)), ("finish", at(3600))])

    def test_finish_during_break_or_while_waiting_never_adds_study(self):
        for phase in ("break_ready", "break_running", "focus_ready"):
            with self.subTest(phase=phase):
                session = self.start()
                storage.refresh_active_session(at=at(10))
                if phase != "break_ready":
                    storage.start_next_phase(session.id, at=at(20))
                if phase == "focus_ready":
                    storage.refresh_active_session(at=at(25))
                finish_at = 22 if phase == "break_running" else 200
                saved = storage.finish_session(session.id, at=at(finish_at))
                self.assertEqual(saved.duration_seconds, 10)

    def test_invalid_action_after_expiry_rolls_back_then_refresh_reapplies(self):
        original = self.start()
        with self.assertRaises(ValueError):
            storage.pause_session(original.id, at=at(20))
        self.assertEqual(storage.get_session(original.id), original)
        ready = storage.refresh_active_session(at=at(20))
        self.assertEqual(ready.duration_seconds, 10)
        with self.assertRaises(ValueError):
            storage.resume_session(original.id, at=at(21))
        self.assertEqual(storage.get_session(original.id), ready)

    def test_phase_update_failure_rolls_back_focus_event_and_session(self):
        original = self.start()
        self.execute("""
            CREATE TRIGGER reject_phase BEFORE UPDATE ON pomodoro_state
            BEGIN SELECT RAISE(ABORT, 'falha de fase simulada'); END
        """)
        with self.assertRaises(sqlite3.DatabaseError):
            storage.refresh_active_session(at=at(20))
        self.assertEqual(storage.get_session(original.id), original)
        self.assertEqual(self.row_counts(), (1, 1))
        self.execute("DROP TRIGGER reject_phase")
        self.assertEqual(storage.refresh_active_session(at=at(20)).duration_seconds, 10)

    def test_failed_phase_insert_also_rolls_back_preferences(self):
        self.execute("""
            CREATE TRIGGER reject_phase BEFORE INSERT ON pomodoro_state
            BEGIN SELECT RAISE(ABORT, 'falha de fase simulada'); END
        """)
        with self.assertRaises(sqlite3.DatabaseError):
            self.start(focus_seconds=3000, break_seconds=600)
        self.assertEqual(self.row_counts(), (0, 0))
        self.assertEqual(self.execute("SELECT count(*) FROM subject_timer_settings"), [(0,)])
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["Python"]), TimerSettings())

    def test_preferences_are_per_subject_and_only_successful_start_changes_them(self):
        session = self.start(focus_seconds=3000, break_seconds=600)
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["Python"]),
                         TimerSettings("pomodoro", 3000, 600))
        with self.assertRaises(ValueError):
            self.start("SQL")
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["SQL"]), TimerSettings())
        storage.finish_session(session.id, at=at(1))
        progressive = storage.start_session(self.subjects["Python"], "Aula", at=at(2))
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["Python"]),
                         TimerSettings("progressive", 3000, 600))
        storage.finish_session(progressive.id, at=at(3))
        self.start("SQL", focus_seconds=1500, break_seconds=300)
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["Python"]),
                         TimerSettings("progressive", 3000, 600))
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["SQL"]),
                         TimerSettings("pomodoro", 1500, 300))

    def test_concurrent_refresh_and_next_phase_are_applied_once(self):
        session = self.start()
        barrier = threading.Barrier(2)

        def refresh():
            barrier.wait(timeout=5)
            return storage.refresh_active_session(at=at(10))

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: refresh(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual([event.kind for event in results[0].events], ["start", "pause"])
        barrier = threading.Barrier(2)

        def advance():
            barrier.wait(timeout=5)
            try:
                return storage.start_next_phase(session.id, at=at(20))
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: advance(), range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(storage.get_active_session().pomodoro.state, "running")
        self.assertEqual(len(storage.get_active_session().events), 2)

    def test_clock_before_last_break_update_is_rejected(self):
        session = self.start()
        storage.refresh_active_session(at=at(10))
        storage.start_next_phase(session.id, at=at(20))
        paused = storage.pause_session(session.id, at=at(23))
        for operation in (storage.resume_session, storage.finish_session, storage.start_next_phase):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(ValueError):
                    operation(session.id, at=at(22))
                self.assertEqual(storage.get_session(session.id), paused)

    def test_new_process_recovers_expired_focus_without_starting_break(self):
        session = self.start()
        script = """
import json, sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    storage.initialize_database()
    saved = storage.refresh_active_session(at=datetime.fromisoformat(sys.argv[2]))
    print(json.dumps([saved.id, saved.pomodoro.phase, saved.pomodoro.state,
                      saved.duration_seconds, saved.events[-1].occurred_at.isoformat()]))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path), at(1000).isoformat()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(result.stdout),
                         [session.id, "break", "ready", 10, at(10).isoformat()])

    def test_v2_migration_preserves_active_and_finished_progressive_sessions(self):
        # As tabelas legadas têm o mesmo esquema; removemos apenas as adições v3.
        finished = storage.start_session(self.subjects["SQL"], "Aula", at=at(0))
        finished = storage.finish_session(finished.id, at=at(2))
        active = storage.start_session(self.subjects["Python"], "Leitura", at=at(3))
        before = {table: self.execute(f"SELECT * FROM {table} ORDER BY id")
                  for table in ("subjects", "sessions", "session_events")}
        self.execute("DROP TABLE pomodoro_state")
        self.execute("DROP TABLE subject_timer_settings")
        self.execute("PRAGMA user_version = 2")
        storage.initialize_database()
        storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(3,)])
        for table, rows in before.items():
            self.assertEqual(self.execute(f"SELECT * FROM {table} ORDER BY id"), rows)
        self.assertEqual(storage.get_session(finished.id), finished)
        self.assertEqual(storage.get_active_session(), active)
        self.assertEqual(storage.get_subject_timer_settings(active.subject_id), TimerSettings())
        self.assertEqual(self.execute("SELECT count(*) FROM subject_timer_settings"), [(0,)])


if __name__ == "__main__":
    unittest.main()
