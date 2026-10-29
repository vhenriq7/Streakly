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
        settings = {
            "focus_seconds": 10, "break_seconds": 5,
            "long_break_seconds": 900, "cycles_until_long_break": 4,
        }
        settings.update(kwargs)
        return storage.start_session(
            self.subjects[subject], "Leitura", mode="pomodoro", at=at(0),
            **settings,
        )

    def test_deadline_and_manual_cycles_exclude_waiting_and_break(self):
        session = self.start()
        self.assertEqual(session_elapsed_seconds(session, at(1000)), 10)
        ready = storage.refresh_active_session(at=at(1000))
        self.assertEqual((ready.pomodoro.phase, ready.pomodoro.state, ready.status),
                         ("break", "ready", "paused"))
        self.assertEqual(ready.events[-1].occurred_at, at(10))
        self.assertEqual(ready.pomodoro.completed_focuses, 1)
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
        self.assertEqual(paused.pomodoro.completed_focuses, 0)
        self.assertEqual(session_elapsed_seconds(paused, at(1000)), 4)
        self.assertEqual(storage.refresh_active_session(at=at(1000)), paused)
        storage.resume_session(session.id, at=at(1000))
        ready = storage.refresh_active_session(at=at(1006))
        self.assertEqual(ready.duration_seconds, 10)
        self.assertEqual(ready.pomodoro.completed_focuses, 1)

        storage.start_next_phase(session.id, at=at(1020))
        paused_break = storage.pause_session(session.id, at=at(1022))
        self.assertEqual(paused_break.events, ready.events)
        self.assertEqual(pomodoro_remaining_seconds(paused_break.pomodoro, at(1500)), 3)
        self.assertEqual(paused_break.pomodoro.completed_focuses, 1)
        self.assertEqual(storage.refresh_active_session(at=at(1500)), paused_break)
        storage.resume_session(session.id, at=at(1500))
        ready_focus = storage.refresh_active_session(at=at(1503))
        self.assertEqual(ready_focus.duration_seconds, 10)
        self.assertEqual(ready_focus.pomodoro.completed_focuses, 1)
        self.assertEqual((ready_focus.pomodoro.phase, ready_focus.pomodoro.state),
                         ("focus", "ready"))

    def test_late_finish_without_polling_stores_exact_focus_deadline(self):
        session = self.start()
        finished = storage.finish_session(session.id, at=at(3600))
        self.assertEqual(finished.duration_seconds, 10)
        self.assertEqual(finished.ended_at, at(3600))
        self.assertEqual(finished.pomodoro.completed_focuses, 1)
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
        session = self.start(long_break_seconds=7, cycles_until_long_break=2, preset="custom")
        storage.finish_session(session.id, at=at(1))
        previous = storage.get_subject_timer_settings(self.subjects["Python"])
        self.execute("""
            CREATE TRIGGER reject_phase BEFORE INSERT ON pomodoro_state
            BEGIN SELECT RAISE(ABORT, 'falha de fase simulada'); END
        """)
        with self.assertRaises(sqlite3.DatabaseError):
            self.start(focus_seconds=3000, break_seconds=600)
        self.assertEqual(self.row_counts(), (1, 2))
        self.assertEqual(self.execute("SELECT count(*) FROM subject_timer_settings"), [(1,)])
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["Python"]), previous)

    def test_preferences_are_per_subject_and_only_successful_start_changes_them(self):
        session = self.start(focus_seconds=3000, break_seconds=600)
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["Python"]),
                         TimerSettings("pomodoro", 3000, 600, preset="50/10"))
        with self.assertRaises(ValueError):
            self.start("SQL")
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["SQL"]), TimerSettings())
        storage.finish_session(session.id, at=at(1))
        progressive = storage.start_session(self.subjects["Python"], "Aula", at=at(2))
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["Python"]),
                         TimerSettings("progressive", 3000, 600, preset="50/10"))
        storage.finish_session(progressive.id, at=at(3))
        self.start("SQL", focus_seconds=1500, break_seconds=300)
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["Python"]),
                         TimerSettings("progressive", 3000, 600, preset="50/10"))
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["SQL"]),
                         TimerSettings("pomodoro", 1500, 300))

    def test_long_breaks_repeat_at_every_nth_focus_and_new_session_resets_count(self):
        session = self.start(long_break_seconds=11, cycles_until_long_break=2)
        clock = 0
        for completed in range(1, 5):
            with self.subTest(completed=completed):
                if completed > 1:
                    storage.start_next_phase(session.id, at=at(clock))
                ready = storage.refresh_active_session(at=at(clock + 1000))
                self.assertEqual((ready.pomodoro.phase, ready.pomodoro.state), ("break", "ready"))
                self.assertEqual(ready.pomodoro.completed_focuses, completed)
                self.assertEqual(ready.pomodoro.is_long_break, completed % 2 == 0)
                self.assertEqual(ready.pomodoro.remaining_seconds, 11 if completed % 2 == 0 else 5)
                self.assertEqual(ready.events[-1].occurred_at, at(clock + 10))
                self.assertEqual(storage.refresh_active_session(at=at(clock + 1999)), ready)
                running = storage.start_next_phase(session.id, at=at(clock + 2000))
                self.assertEqual(running.pomodoro.completed_focuses, completed)
                focus = storage.refresh_active_session(at=at(clock + 3000))
                self.assertEqual((focus.pomodoro.phase, focus.pomodoro.state), ("focus", "ready"))
                self.assertEqual(focus.pomodoro.completed_focuses, completed)
                self.assertEqual(focus.duration_seconds, completed * 10)
                self.assertEqual(storage.refresh_active_session(at=at(clock + 3999)), focus)
                clock += 4000
        self.assertEqual(storage.finish_session(session.id, at=at(clock)).duration_seconds, 40)
        another = storage.start_session(self.subjects["Python"], "Aula", mode="pomodoro",
                                        at=at(clock + 1), focus_seconds=10, break_seconds=5,
                                        long_break_seconds=11, cycles_until_long_break=2)
        self.assertEqual(another.pomodoro.completed_focuses, 0)
        self.assertFalse(another.pomodoro.is_long_break)

    def test_n_one_uses_long_breaks_and_manual_pauses_never_complete_a_focus(self):
        session = self.start(long_break_seconds=7, cycles_until_long_break=1)
        paused = storage.pause_session(session.id, at=at(4))
        self.assertEqual(paused.pomodoro.completed_focuses, 0)
        storage.resume_session(session.id, at=at(100))
        ready = storage.refresh_active_session(at=at(106))
        self.assertEqual((ready.pomodoro.completed_focuses, ready.pomodoro.is_long_break,
                          ready.pomodoro.remaining_seconds), (1, True, 7))
        storage.start_next_phase(session.id, at=at(200))
        paused = storage.pause_session(session.id, at=at(202))
        self.assertEqual((paused.pomodoro.completed_focuses, paused.pomodoro.remaining_seconds), (1, 5))
        self.assertEqual(session_elapsed_seconds(paused, at(1000)), 10)
        storage.resume_session(session.id, at=at(1000))
        focus = storage.refresh_active_session(at=at(1005))
        self.assertEqual((focus.pomodoro.phase, focus.pomodoro.state,
                          focus.pomodoro.completed_focuses), ("focus", "ready", 1))
        storage.start_next_phase(session.id, at=at(2000))
        finished = storage.finish_session(session.id, at=at(10000))
        self.assertEqual(finished.duration_seconds, 20)
        self.assertEqual((finished.pomodoro.completed_focuses, finished.pomodoro.is_long_break,
                          finished.pomodoro.remaining_seconds), (2, True, 7))
        self.assertEqual(finished.events[-2].occurred_at, at(2010))

    def test_preset_inference_uses_all_settings_and_progressive_preserves_custom(self):
        cases = (
            ({"focus_seconds": 1500, "break_seconds": 300}, "25/5"),
            ({"focus_seconds": 3000, "break_seconds": 600}, "50/10"),
            ({"focus_seconds": 1500, "break_seconds": 300, "long_break_seconds": 901}, "custom"),
            ({"focus_seconds": 3000, "break_seconds": 600, "cycles_until_long_break": 2}, "custom"),
            ({"focus_seconds": 1500, "break_seconds": 300, "preset": "custom"}, "custom"),
            ({"focus_seconds": 10, "break_seconds": 5, "long_break_seconds": 7,
              "cycles_until_long_break": 2, "preset": "custom"}, "custom"),
        )
        for arguments, preset in cases:
            with self.subTest(arguments=arguments):
                session = self.start(**arguments)
                settings = storage.get_subject_timer_settings(self.subjects["Python"])
                self.assertEqual(settings, TimerSettings(
                    "pomodoro", arguments["focus_seconds"], arguments["break_seconds"],
                    arguments.get("long_break_seconds", 900),
                    arguments.get("cycles_until_long_break", 4), preset,
                ))
                self.assertEqual(session.pomodoro.preset, preset)
                storage.finish_session(session.id, at=at(1))
        progressive = storage.start_session(self.subjects["Python"], "Aula", at=at(2))
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["Python"]),
                         TimerSettings("progressive", 10, 5, 7, 2, "custom"))
        self.assertIsNone(progressive.pomodoro)

    def test_duplicate_names_use_uuid_and_active_session_keeps_its_settings_snapshot(self):
        storage.add_subject("Python")
        other_id = next(key for key, name in storage.list_subject_options()
                        if name == "Python" and key != self.subjects["Python"])
        session = self.start(long_break_seconds=7, cycles_until_long_break=2, preset="custom")
        expected = TimerSettings("pomodoro", 10, 5, 7, 2, "custom")
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["Python"]), expected)
        self.assertEqual(storage.get_subject_timer_settings(other_id), TimerSettings())
        self.execute("""
            UPDATE subject_timer_settings SET focus_seconds = 1500, break_seconds = 300,
                long_break_seconds = 900, cycles_until_long_break = 4, preset = '25/5'
            WHERE subject_id = ?
        """, (self.subjects["Python"],))
        reloaded = storage.get_session(session.id)
        self.assertEqual(reloaded, session)
        self.assertEqual((reloaded.pomodoro.focus_seconds, reloaded.pomodoro.break_seconds,
                          reloaded.pomodoro.long_break_seconds, reloaded.pomodoro.cycles_until_long_break,
                          reloaded.pomodoro.preset), (10, 5, 7, 2, "custom"))
        self.assertEqual(storage.refresh_active_session(at=at(1000)).duration_seconds, 10)

    def test_invalid_custom_values_and_named_presets_leave_preferences_unchanged(self):
        session = self.start(long_break_seconds=7, cycles_until_long_break=2, preset="custom")
        storage.finish_session(session.id, at=at(1))
        expected = storage.get_subject_timer_settings(self.subjects["Python"])
        counts = self.row_counts()
        attempts = [{field: value}
                    for field in ("focus_seconds", "break_seconds", "long_break_seconds", "cycles_until_long_break")
                    for value in (0, -1, True, 1.5, "2", None, 10 ** 40)]
        attempts += [{"preset": value} for value in ("unknown", True, "25/5", "50/10")]
        attempts += [{"focus_seconds": 1500, "break_seconds": 300, "preset": "25/5", field: value}
                     for field, value in (("long_break_seconds", 901), ("cycles_until_long_break", 2))]
        for arguments in attempts:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.start(**arguments)
                self.assertEqual(self.row_counts(), counts)
                self.assertIsNone(storage.get_active_session())
                self.assertEqual(storage.get_subject_timer_settings(self.subjects["Python"]), expected)

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
        session = self.start(long_break_seconds=7, cycles_until_long_break=1)
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
                      saved.duration_seconds, saved.events[-1].occurred_at.isoformat(),
                      saved.pomodoro.completed_focuses, saved.pomodoro.is_long_break,
                      saved.pomodoro.remaining_seconds]))
"""
        def reopen(seconds):
            result = subprocess.run(
                [sys.executable, "-B", "-c", script, str(self.path), at(seconds).isoformat()],
                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                text=True, check=True, timeout=20,
            )
            return json.loads(result.stdout)

        self.assertEqual(reopen(1000),
                         [session.id, "break", "ready", 10, at(10).isoformat(), 1, True, 7])
        storage.start_next_phase(session.id, at=at(1001))
        storage.refresh_active_session(at=at(1008))
        storage.start_next_phase(session.id, at=at(1010))
        self.assertEqual(reopen(2000),
                         [session.id, "break", "ready", 20, at(1020).isoformat(), 2, True, 7])

    def test_v2_migration_preserves_active_and_finished_progressive_sessions(self):
        # Retira as adições posteriores para reconstruir um banco v2 real.
        finished = storage.start_session(self.subjects["SQL"], "Aula", at=at(0))
        finished = storage.finish_session(finished.id, at=at(2))
        active = storage.start_session(self.subjects["Python"], "Leitura", at=at(3))
        before = {table: self.execute(f"SELECT * FROM {table} ORDER BY id")
                  for table in ("subjects", "sessions", "session_events")}
        self.execute("DROP TABLE pomodoro_state")
        self.execute("DROP TABLE subject_timer_settings")
        self.execute("ALTER TABLE sessions DROP COLUMN source")
        self.execute("PRAGMA user_version = 2")
        storage.initialize_database()
        storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(5,)])
        for table, rows in before.items():
            self.assertEqual(self.execute(f"SELECT * FROM {table} ORDER BY id"), rows)
        self.assertEqual(storage.get_session(finished.id), finished)
        self.assertEqual(storage.get_active_session(), active)
        self.assertEqual(storage.get_subject_timer_settings(active.subject_id), TimerSettings())
        self.assertEqual(self.execute("SELECT count(*) FROM subject_timer_settings"), [(0,)])

    def test_v3_migration_recovers_completed_focuses_without_counting_manual_pauses(self):
        partial = self.start(focus_seconds=1500, break_seconds=300)
        storage.pause_session(partial.id, at=at(600))
        storage.resume_session(partial.id, at=at(620))
        storage.refresh_active_session(at=at(1520))
        storage.start_next_phase(partial.id, at=at(1530))
        storage.refresh_active_session(at=at(1830))
        storage.start_next_phase(partial.id, at=at(1840))
        storage.refresh_active_session(at=at(3340))
        storage.start_next_phase(partial.id, at=at(3350))
        storage.refresh_active_session(at=at(3650))
        storage.start_next_phase(partial.id, at=at(3660))
        storage.pause_session(partial.id, at=at(4260))
        storage.finish_session(partial.id, at=at(4261))

        ready = self.start("SQL", focus_seconds=3000, break_seconds=600, cycles_until_long_break=5)
        for clock in (0, 4000, 8000, 12000):
            if clock:
                storage.start_next_phase(ready.id, at=at(clock))
            storage.refresh_active_session(at=at(clock + 3000))
            if clock < 12000:
                storage.start_next_phase(ready.id, at=at(clock + 3001))
                storage.refresh_active_session(at=at(clock + 3601))

        before = {table: self.execute(f"SELECT * FROM {table} ORDER BY id")
                  for table in ("subjects", "sessions", "session_events")}
        legacy_tables = {
            "subject_timer_settings": (
                "subject_id, mode, focus_seconds, break_seconds",
                """subject_id TEXT PRIMARY KEY NOT NULL REFERENCES subjects(id),
                   mode TEXT NOT NULL CHECK (mode IN ('progressive', 'pomodoro')),
                   focus_seconds INTEGER NOT NULL CHECK (focus_seconds > 0),
                   break_seconds INTEGER NOT NULL CHECK (break_seconds > 0)""",
            ),
            "pomodoro_state": (
                "session_id, phase, state, focus_seconds, break_seconds, remaining_seconds, started_at, updated_at",
                """session_id TEXT PRIMARY KEY NOT NULL REFERENCES sessions(id),
                   phase TEXT NOT NULL CHECK (phase IN ('focus', 'break')),
                   state TEXT NOT NULL CHECK (state IN ('running', 'paused', 'ready')),
                   focus_seconds INTEGER NOT NULL CHECK (focus_seconds > 0),
                   break_seconds INTEGER NOT NULL CHECK (break_seconds > 0),
                   remaining_seconds REAL NOT NULL CHECK (remaining_seconds >= 0),
                   started_at TEXT, updated_at TEXT NOT NULL,
                   CHECK ((state = 'running' AND started_at IS NOT NULL)
                       OR (state != 'running' AND started_at IS NULL))""",
            ),
        }
        legacy_rows = {}
        for table, (columns, definition) in legacy_tables.items():
            legacy_rows[table] = self.execute(f"SELECT {columns} FROM {table} ORDER BY 1")
            self.execute(f"ALTER TABLE {table} RENAME TO {table}_fixture_v4")
            self.execute(f"CREATE TABLE {table} ({definition})")
            self.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {table}_fixture_v4")
            self.execute(f"DROP TABLE {table}_fixture_v4")
        self.execute("ALTER TABLE sessions DROP COLUMN source")
        self.execute("PRAGMA user_version = 3")
        storage.initialize_database()
        storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(5,)])
        for table, rows in before.items():
            self.assertEqual(self.execute(f"SELECT * FROM {table} ORDER BY id"), rows)
        for table, (columns, _) in legacy_tables.items():
            self.assertEqual(self.execute(f"SELECT {columns} FROM {table} ORDER BY 1"), legacy_rows[table])
        migrated = storage.get_session(partial.id).pomodoro
        self.assertEqual((migrated.phase, migrated.state, migrated.remaining_seconds,
                          migrated.completed_focuses, migrated.preset), ("focus", "paused", 900, 2, "25/5"))
        migrated = storage.get_active_session().pomodoro
        self.assertEqual((migrated.phase, migrated.state, migrated.remaining_seconds,
                          migrated.completed_focuses, migrated.is_long_break), ("break", "ready", 600, 4, False))
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["Python"]),
                         TimerSettings("pomodoro", 1500, 300))
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["SQL"]),
                         TimerSettings("pomodoro", 3000, 600, preset="50/10"))


if __name__ == "__main__":
    unittest.main()
