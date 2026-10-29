"""Lançamentos manuais com bancos temporários, sem alterar os dados pessoais."""

import json
import sqlite3
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import storage
from main import parse_manual_period
from test_timer import TemporaryDatabaseTest, at
from timer import SessionEvent, TimerSettings, local_day_window, session_study_seconds_between


class ManualPeriodParsingTests(unittest.TestCase):
    def test_local_start_becomes_utc_and_duration_accepts_more_than_24_hours(self):
        for duration, seconds in (("01:30", 5400), ("26:15", 94500)):
            with self.subTest(duration=duration):
                start, actual_seconds = parse_manual_period("29/10/2026", "09:15", duration)
                self.assertEqual(start.tzinfo, UTC)
                self.assertEqual(start.astimezone().strftime("%d/%m/%Y %H:%M"),
                                 "29/10/2026 09:15")
                self.assertEqual(actual_seconds, seconds)

    def test_invalid_calendar_time_and_duration_are_reported_as_value_errors(self):
        attempts = [("29/02/2025", "09:15", "01:30"),
                    ("29/10/2026", "24:00", "01:30")]
        attempts += [("29/10/2026", "09:15", duration)
                     for duration in ("00:00", "01:60", "-1:30", "01:3", "", "9" * 100 + ":00")]
        for date_text, time_text, duration_text in attempts:
            with self.subTest(date=date_text, time=time_text, duration=duration_text):
                with self.assertRaises(ValueError):
                    parse_manual_period(date_text, time_text, duration_text)


class ManualSessionTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        for name in ("Python", "SQL"):
            storage.add_subject(name)
        self.subjects = {name: key for key, name in storage.list_subject_options()}

    def create(self, start=0, duration=60, *, subject="Python", now=1000, **kwargs):
        return storage.create_manual_session(
            self.subjects[subject], "Leitura", at(start), duration, at=at(now), **kwargs,
        )

    def snapshot(self):
        return {
            **{table: self.execute(f"SELECT * FROM {table} ORDER BY 1")
               for table in ("subjects", "sessions", "session_events",
                             "subject_timer_settings", "pomodoro_state")},
            "schema": self.execute("PRAGMA user_version"),
        }

    def test_create_finishes_a_continuous_period_with_utc_events_and_stable_ids(self):
        storage.add_subject("Python")
        other_id = next(key for key, name in storage.list_subject_options()
                        if name == "Python" and key != self.subjects["Python"])
        zone = timezone(timedelta(hours=-3))
        saved = storage.create_manual_session(
            other_id, "  Revisão  ", at(0).astimezone(zone), 60,
            "  Laços e funções 🚀  ", at=at(60),
        )
        self.assertEqual((saved.subject_id, saved.subject_name, saved.study_type, saved.topic),
                         (other_id, "Python", "Revisão", "Laços e funções 🚀"))
        self.assertEqual((saved.status, saved.mode, saved.source, saved.duration_seconds),
                         ("finished", "progressive", "manual", 60))
        self.assertEqual((saved.started_at, saved.ended_at), (at(0), at(60)))
        self.assertEqual(saved.started_at.tzinfo, UTC)
        self.assertEqual(saved.events, (SessionEvent("start", at(0)), SessionEvent("finish", at(60))))
        rows = self.execute("SELECT id, sequence FROM session_events ORDER BY sequence")
        self.assertEqual([sequence for _, sequence in rows], [0, 1])
        ids = [saved.id, *(event_id for event_id, _ in rows)]
        self.assertEqual(len(set(ids)), 3)
        for key in ids:
            self.assertEqual(str(UUID(key)), key)
        self.assertEqual(storage.get_session(saved.id), saved)
        self.assertEqual(storage.read_session_history().sessions, (saved,))
        self.assertIsNone(storage.get_active_session())
        self.assertIsNone(saved.pomodoro)
        self.assertEqual(self.execute("SELECT * FROM subject_timer_settings"), [])

    def test_daily_total_splits_a_manual_period_at_local_midnight(self):
        zone = timezone(timedelta(hours=-3))
        start = datetime(2026, 10, 28, 23, 50, tzinfo=zone)
        now = datetime(2026, 10, 29, 1, tzinfo=zone)
        saved = storage.create_manual_session(
            self.subjects["Python"], "Aula", start, 20 * 60, "   ", at=now,
        )
        before = self.snapshot()
        overview = storage.read_study_overview(at=now, tz=zone)
        self.assertEqual((saved.topic, saved.duration_seconds, overview.total_seconds),
                         (None, 1200, 600))
        previous = local_day_window(start, zone)
        self.assertEqual(session_study_seconds_between(saved, *previous[1:], now), 600)
        self.assertEqual(self.snapshot(), before)

    def test_manual_period_in_a_pause_preserves_active_session_and_all_preferences(self):
        preset = storage.start_session(
            self.subjects["Python"], "Aula", mode="pomodoro", at=at(0),
            focus_seconds=3000, break_seconds=600, preset="50/10",
        )
        storage.finish_session(preset.id, at=at(10))
        active = storage.start_session(self.subjects["Python"], "Aula", at=at(100))
        paused = storage.pause_session(active.id, at=at(110))
        preferences = self.execute("SELECT * FROM subject_timer_settings ORDER BY subject_id")
        active_events = self.execute("SELECT * FROM session_events WHERE session_id = ? ORDER BY sequence",
                                     (active.id,))
        saved = self.create(120, 60, subject="SQL", now=200)
        self.assertEqual(saved.source, "manual")
        self.assertEqual(storage.get_active_session(), paused)
        self.assertEqual(self.execute("SELECT * FROM subject_timer_settings ORDER BY subject_id"), preferences)
        self.assertEqual(self.execute("SELECT * FROM session_events WHERE session_id = ? ORDER BY sequence",
                                      (active.id,)), active_events)
        self.assertEqual(storage.get_subject_timer_settings(self.subjects["SQL"]), TimerSettings())
        self.assertEqual(storage.read_study_overview(at=at(200), tz=UTC).total_seconds, 80)

    def test_overlaps_across_subjects_and_duplicate_periods_are_rejected_but_boundaries_are_allowed(self):
        self.create(100, 100)
        before = self.snapshot()
        for start, duration, subject in ((100, 100, "Python"), (199, 2, "SQL"), (0, 101, "SQL")):
            with self.subTest(start=start, duration=duration, subject=subject):
                with self.assertRaises(ValueError):
                    self.create(start, duration, subject=subject)
                self.assertEqual(self.snapshot(), before)
        self.create(0, 100, subject="SQL")
        self.create(200, 10, subject="SQL")
        self.assertEqual(storage.read_study_overview(at=at(1000), tz=UTC).total_seconds, 210)
        self.assertEqual(self.row_counts(), (3, 6))

    def test_running_progressive_study_blocks_overlaps_without_changing_its_state(self):
        active = storage.start_session(self.subjects["Python"], "Aula", at=at(100))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.create(120, 30, subject="SQL", now=200)
        self.assertEqual(self.snapshot(), before)
        saved = self.create(80, 20, subject="SQL", now=200)
        self.assertEqual(saved.ended_at, active.started_at)
        self.assertEqual(storage.get_active_session(), active)

    def test_expired_pomodoro_waits_and_breaks_do_not_overlap_manual_study_or_get_consolidated(self):
        active = storage.start_session(
            self.subjects["Python"], "Aula", mode="pomodoro", at=at(0),
            focus_seconds=10, break_seconds=5, long_break_seconds=7,
            cycles_until_long_break=2, preset="custom",
        )
        self.create(10, 10, subject="SQL", now=100)
        self.assertEqual(storage.get_active_session(), active)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.create(9, 2, subject="SQL", now=100)
        self.assertEqual(self.snapshot(), before)

        storage.refresh_active_session(at=at(100))
        self.create(20, 5, subject="SQL", now=100)
        short_break = storage.start_next_phase(active.id, at=at(200))
        self.create(200, 5, subject="SQL", now=205)
        self.assertEqual(storage.get_active_session(), short_break)
        storage.refresh_active_session(at=at(205))
        storage.start_next_phase(active.id, at=at(300))
        storage.refresh_active_session(at=at(310))
        long_break = storage.start_next_phase(active.id, at=at(400))
        self.assertTrue(long_break.pomodoro.is_long_break)
        self.create(400, 7, subject="SQL", now=1000)
        self.assertEqual(storage.get_active_session(), long_break)

    def test_invalid_values_dates_and_unknown_subject_leave_no_partial_rows(self):
        self.create(-200, 60)
        before = self.snapshot()
        parameters = dict(subject_id=self.subjects["SQL"], study_type="Leitura",
                          started_at=at(0), duration_seconds=60, at=at(1000))
        attempts = [{"duration_seconds": value}
                    for value in (0, -1, True, False, "60", 1.5, None, 10**40, 2**63)]
        attempts += [{"study_type": value} for value in ("   ", None, 2)]
        attempts += [{"topic": value} for value in (2, True)]
        attempts += [{"started_at": value} for value in (at(0).replace(tzinfo=None), None, "2026-10-28")]
        attempts += [{"subject_id": value} for value in ("inexistente", "", None, 2)]
        attempts += [{"at": at(1000).replace(tzinfo=None)},
                     {"started_at": at(1001)}, {"started_at": at(999), "duration_seconds": 2},
                     {"started_at": datetime(9999, 12, 31, 23, 59, 50, tzinfo=UTC),
                      "duration_seconds": 60,
                      "at": datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)}]
        for arguments in attempts:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    storage.create_manual_session(**(parameters | arguments))
                self.assertEqual(self.snapshot(), before)

    def test_second_event_failure_rolls_back_the_entire_manual_insert(self):
        active = storage.start_session(self.subjects["Python"], "Aula", at=at(0))
        paused = storage.pause_session(active.id, at=at(10))
        before = self.snapshot()
        self.execute("""
            CREATE TRIGGER reject_manual_finish BEFORE INSERT ON session_events
            WHEN NEW.kind = 'finish'
            BEGIN SELECT RAISE(ABORT, 'falha simulada no segundo evento'); END
        """)
        with self.assertRaises(sqlite3.DatabaseError):
            self.create(20, 10, subject="SQL", now=100)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_active_session(), paused)
        self.execute("DROP TRIGGER reject_manual_finish")
        self.assertEqual(self.create(20, 10, subject="SQL", now=100).duration_seconds, 10)
        self.assertEqual(storage.get_active_session(), paused)

    def test_concurrent_manual_inserts_cannot_record_the_same_period_twice(self):
        barrier = threading.Barrier(2)

        def attempt(subject):
            barrier.wait(timeout=5)
            try:
                return self.create(0, 10, subject=subject, now=100).id
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ("Python", "SQL")))
        successful = [key for key in results if key is not None]
        self.assertEqual(len(successful), 1)
        self.assertEqual(self.row_counts(), (1, 2))
        self.assertEqual(storage.read_session_history().sessions[0].id, successful[0])
        self.assertIsNone(storage.get_active_session())

    def test_another_process_recovers_manual_source_and_totals_without_changing_rows(self):
        saved = self.create(0, 60, topic="Funções 🚀")
        active = storage.start_session(self.subjects["SQL"], "Aula", at=at(200))
        storage.pause_session(active.id, at=at(210))
        before = self.snapshot()
        script = """
import json, sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    storage.initialize_database()
    saved = storage.read_session_history().sessions[0]
    overview = storage.read_study_overview(at=datetime.fromisoformat(sys.argv[2]), tz=UTC)
    print(json.dumps([saved.id, saved.subject_id, saved.source, saved.topic,
                      saved.duration_seconds, [event.kind for event in saved.events],
                      overview.total_seconds, overview.active_session.id]))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path), at(300).isoformat()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(result.stdout),
                         [saved.id, saved.subject_id, "manual", "Funções 🚀", 60,
                          ["start", "finish"], 70, active.id])
        self.assertEqual(self.snapshot(), before)

    def test_v4_migration_preserves_existing_sessions_events_and_preferences(self):
        finished = storage.start_session(self.subjects["Python"], "Leitura", at=at(0))
        finished = storage.finish_session(finished.id, at=at(10))
        active = storage.start_session(
            self.subjects["SQL"], "Aula", mode="pomodoro", at=at(100),
            focus_seconds=10, break_seconds=5, long_break_seconds=7,
            cycles_until_long_break=2, preset="custom",
        )
        self.execute("ALTER TABLE sessions DROP COLUMN source")
        self.execute("PRAGMA user_version = 4")
        old_columns = ", ".join(row[1] for row in self.execute("PRAGMA table_info(sessions)"))
        old_sessions = self.execute(f"SELECT {old_columns} FROM sessions ORDER BY id")
        other_tables = {
            table: self.execute(f"SELECT * FROM {table} ORDER BY 1")
            for table in ("subjects", "session_events", "subject_timer_settings", "pomodoro_state")
        }
        storage.initialize_database()
        storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(5,)])
        self.assertEqual(self.execute(f"SELECT {old_columns} FROM sessions ORDER BY id"), old_sessions)
        for table, rows in other_tables.items():
            self.assertEqual(self.execute(f"SELECT * FROM {table} ORDER BY 1"), rows)
        self.assertEqual(self.execute("SELECT DISTINCT source FROM sessions"), [("timer",)])
        self.assertEqual(storage.get_session(finished.id), finished)
        self.assertEqual(storage.get_active_session(), active)
        self.assertEqual(self.create(20, 10, now=1000).source, "manual")
        self.assertEqual(storage.get_active_session(), active)
