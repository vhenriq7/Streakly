"""Metas por vigência com relógio simulado e SQLite temporário."""

import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import storage
from test_timer import TemporaryDatabaseTest, at


LOCAL = timezone(timedelta(hours=-3))


def local(day, hour=12, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=LOCAL)


class DailyGoalTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        storage.add_subject("Python")
        self.subject_id = storage.list_subject_options()[0][0]

    def snapshot(self, *, include_goals=True):
        tables = ["subjects", "sessions", "session_events", "subject_timer_settings", "pomodoro_state"]
        if include_goals:
            tables.append("daily_goal_defaults")
        return {table: self.execute(f"SELECT * FROM {table} ORDER BY 1") for table in tables}

    def set_goal(self, seconds, expected=None, *, day=29):
        return storage.set_default_daily_goal(seconds, expected, date(2026, 10, day),
                                              at=local(day), tz=LOCAL)

    def test_unconfigured_days_are_read_only_and_have_no_implicit_goal(self):
        before = self.snapshot()
        for day in (date(2026, 10, 1), date(2026, 10, 29), date(2030, 1, 1)):
            with self.subTest(day=day):
                self.assertIsNone(storage.read_default_daily_goal(day))
        overview = storage.read_study_overview(at=local(29), tz=LOCAL)
        self.assertIsNone(overview.goal)
        self.assertEqual(overview.total_seconds, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.execute("PRAGMA user_version"), [(6,)])

    def test_effective_periods_preserve_past_and_unobserved_days_and_same_day_ids(self):
        first = self.set_goal(3600, day=20)
        self.assertEqual(str(UUID(first.id)), first.id)
        self.assertEqual((first.effective_from, first.duration_seconds), (date(2026, 10, 20), 3600))
        self.assertIsNone(storage.read_default_daily_goal(date(2026, 10, 19)))
        second = self.set_goal(7200, first, day=29)
        self.assertNotEqual(first.id, second.id)
        third = self.set_goal(5400, second, day=29)
        self.assertEqual(third.id, second.id)
        self.assertEqual(third.effective_from, second.effective_from)
        for day in range(20, 29):
            with self.subTest(day=day):
                self.assertEqual(storage.read_default_daily_goal(date(2026, 10, day)), first)
        for day in (date(2026, 10, 29), date(2026, 11, 15), date(2030, 1, 1)):
            with self.subTest(day=day):
                self.assertEqual(storage.read_default_daily_goal(day), third)
        self.assertEqual(self.execute("SELECT count(*) FROM daily_goal_defaults"), [(2,)])

    def test_an_unchanged_duration_does_not_write_or_create_an_extra_period(self):
        first = self.set_goal(3600, day=20)
        self.execute("""
            CREATE TRIGGER reject_goal_insert BEFORE INSERT ON daily_goal_defaults
            BEGIN SELECT RAISE(ABORT, 'gravação desnecessária'); END
        """)
        self.execute("""
            CREATE TRIGGER reject_goal_update BEFORE UPDATE ON daily_goal_defaults
            BEGIN SELECT RAISE(ABORT, 'gravação desnecessária'); END
        """)
        before = self.snapshot()
        self.assertEqual(self.set_goal(3600, first, day=20), first)
        self.assertEqual(self.set_goal(3600, first, day=29), first)
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(ValueError):
            self.set_goal(3600, None, day=29)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_inputs_are_rejected_before_opening_a_connection(self):
        parameters = dict(duration_seconds=3600, expected=None, expected_day=date(2026, 10, 29),
                          at=local(29), tz=LOCAL)
        attempts = [{"duration_seconds": value}
                    for value in (None, True, False, 0, -1, 1.5, "3600", 2**63)]
        attempts += [{"expected_day": value}
                     for value in (None, True, "2026-10-29", local(29))]
        attempts += [{"expected": value} for value in (True, 1, "meta", object())]
        attempts += [{"at": local(29).replace(tzinfo=None)}, {"expected_day": date(2026, 10, 28)}]
        before = self.snapshot()
        with patch.object(storage, "_connection", side_effect=AssertionError("Conexão inesperada")) as connection:
            for invalid in (None, True, "2026-10-29", local(29)):
                with self.subTest(read_day=invalid):
                    with self.assertRaises(ValueError):
                        storage.read_default_daily_goal(invalid)
            for values in attempts:
                with self.subTest(values=values):
                    with self.assertRaises(ValueError):
                        storage.set_default_daily_goal(**(parameters | values))
            connection.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        maximum = self.set_goal(2**63 - 1)
        self.assertEqual(maximum.duration_seconds, 2**63 - 1)

    def test_stale_configuration_cannot_overwrite_a_newer_goal(self):
        first = self.set_goal(3600)
        current = self.set_goal(5400, first)
        before = self.snapshot()
        for expected in (None, first):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, "(?i)(mudou|atualiz|cancele)"):
                    self.set_goal(7200, expected)
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.read_default_daily_goal(date(2026, 10, 29)), current)

    def test_midnight_and_a_clock_moving_backwards_do_not_rewrite_closed_days(self):
        yesterday = self.set_goal(3600, day=28)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "(?i)dia"):
            storage.set_default_daily_goal(7200, yesterday, date(2026, 10, 28),
                                          at=local(29, 0), tz=LOCAL)
        self.assertEqual(self.snapshot(), before)
        today = self.set_goal(7200, yesterday, day=29)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.set_goal(5400, yesterday, day=28)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.read_default_daily_goal(date(2026, 10, 28)), yesterday)
        self.assertEqual(storage.read_default_daily_goal(date(2026, 10, 29)), today)

    def test_goal_effective_date_uses_the_requested_local_zone_instead_of_utc(self):
        moment = datetime(2026, 10, 29, 2, 30, tzinfo=UTC)
        first = storage.set_default_daily_goal(3600, None, date(2026, 10, 28), at=moment, tz=LOCAL)
        self.assertEqual(first.effective_from, date(2026, 10, 28))
        east = timezone(timedelta(hours=5, minutes=30))
        second = storage.set_default_daily_goal(7200, first, date(2026, 10, 29), at=moment, tz=east)
        self.assertEqual(second.effective_from, date(2026, 10, 29))
        self.assertEqual(storage.read_study_overview(at=moment, tz=LOCAL).goal, first)
        self.assertEqual(storage.read_study_overview(at=moment, tz=east).goal, second)

    def test_two_concurrent_different_updates_have_only_one_winner(self):
        initial = self.set_goal(3600)
        barrier = threading.Barrier(2)

        def attempt(seconds):
            barrier.wait(timeout=5)
            try:
                return self.set_goal(seconds, initial)
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, (5400, 7200)))
        successful = [goal for goal in results if goal is not None]
        self.assertEqual(len(successful), 1)
        self.assertEqual(storage.read_default_daily_goal(date(2026, 10, 29)), successful[0])
        self.assertEqual(successful[0].id, initial.id)
        self.assertEqual(self.execute("SELECT count(*) FROM daily_goal_defaults"), [(1,)])

    def test_insert_and_update_failures_roll_back_without_changing_study_data(self):
        saved = storage.create_manual_session(self.subject_id, "Leitura", local(29, 8), 120,
                                              "Funções 🚀", at=local(29))
        for operation in ("INSERT", "UPDATE"):
            with self.subTest(operation=operation):
                expected = self.set_goal(3600) if operation == "UPDATE" else None
                before = self.snapshot()
                self.execute(f"""
                    CREATE TRIGGER reject_daily_goal BEFORE {operation} ON daily_goal_defaults
                    BEGIN SELECT RAISE(ABORT, 'falha simulada'); END
                """)
                try:
                    with self.assertRaises(sqlite3.DatabaseError):
                        self.set_goal(7200, expected)
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(storage.get_session(saved.id), saved)
                finally:
                    self.execute("DROP TRIGGER reject_daily_goal")

    def test_goal_apis_preserve_an_overdue_pomodoro_and_all_related_rows(self):
        active = storage.start_session(self.subject_id, "Exercícios", mode="pomodoro",
                                       focus_seconds=10, break_seconds=5, at=at(0))
        before = self.snapshot(include_goals=False)
        with patch.object(storage, "refresh_active_session", side_effect=AssertionError("Consolidação inesperada")):
            goal = self.set_goal(3600)
            self.assertEqual(storage.read_default_daily_goal(date(2026, 10, 29)), goal)
        self.assertEqual(self.snapshot(include_goals=False), before)
        self.assertEqual(storage.get_active_session(), active)
        self.assertEqual(storage.get_active_session().pomodoro.state, "running")
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_overview_reads_goal_and_study_total_from_one_consistent_snapshot(self):
        goal = self.set_goal(120)
        saved = storage.create_manual_session(self.subject_id, "Leitura", local(29, 8), 120,
                                              at=local(29))
        self.execute("PRAGMA journal_mode = WAL")
        original_read = storage._read_default_daily_goal
        changed = False

        def mutate_between_total_and_goal(connection, day):
            nonlocal changed
            if not changed:
                changed = True
                # WAL permite que outra conexão grave enquanto a visão de leitura permanece aberta.
                with closing(sqlite3.connect(self.path)) as writer:
                    with writer:
                        writer.execute("UPDATE daily_goal_defaults SET duration_seconds = 240 WHERE id = ?",
                                       (goal.id,))
                        writer.execute("DELETE FROM session_events WHERE session_id = ?", (saved.id,))
                        writer.execute("DELETE FROM sessions WHERE id = ?", (saved.id,))
            return original_read(connection, day)

        with patch.object(storage, "_read_default_daily_goal", side_effect=mutate_between_total_and_goal):
            overview = storage.read_study_overview(at=local(29), tz=LOCAL)
        self.assertTrue(changed)
        self.assertEqual((overview.goal, overview.total_seconds), (goal, 120))
        current = storage.read_study_overview(at=local(29), tz=LOCAL)
        self.assertEqual((current.goal.id, current.goal.duration_seconds, current.total_seconds),
                         (goal.id, 240, 0))
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_v5_migration_preserves_sessions_events_preferences_and_ids_and_is_repeatable(self):
        manual = storage.create_manual_session(self.subject_id, "Leitura", at(0), 10, at=at(1000))
        active = storage.start_session(self.subject_id, "Aula", mode="pomodoro",
                                       focus_seconds=10, break_seconds=5, at=at(100))
        active = storage.pause_session(active.id, at=at(105))
        self.execute("DROP TABLE daily_goal_defaults")
        self.execute("PRAGMA user_version = 5")
        before = self.snapshot(include_goals=False)
        storage.initialize_database()
        storage.initialize_database()
        self.assertEqual(self.snapshot(include_goals=False), before)
        self.assertEqual(self.execute("PRAGMA user_version"), [(6,)])
        self.assertEqual(self.execute("SELECT * FROM daily_goal_defaults"), [])
        self.assertEqual(storage.get_session(manual.id), manual)
        self.assertEqual(storage.get_active_session(), active)

    def test_failed_v5_migration_rolls_back_table_and_version_before_retry(self):
        self.execute("DROP TABLE daily_goal_defaults")
        self.execute("PRAGMA user_version = 5")
        before = self.snapshot(include_goals=False)
        original_connection = storage._connection

        @contextmanager
        def reject_version(*, write=False):
            with original_connection(write=write) as connection:
                def authorize(action, first, second, database, source):
                    if action == sqlite3.SQLITE_PRAGMA and first == "user_version" and second == "6":
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                connection.set_authorizer(authorize)
                yield connection

        with patch.object(storage, "_connection", side_effect=reject_version):
            with self.assertRaises(sqlite3.DatabaseError):
                storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(5,)])
        self.assertEqual(self.execute("SELECT name FROM sqlite_master WHERE name = 'daily_goal_defaults'"), [])
        self.assertEqual(self.snapshot(include_goals=False), before)
        storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(6,)])

    def test_another_process_recovers_goal_periods_and_preserves_study_data(self):
        first = self.set_goal(3600, day=20)
        current = self.set_goal(7200, first, day=29)
        manual = storage.create_manual_session(self.subject_id, "Leitura", local(29, 8), 120,
                                              "Funções 🚀", at=local(29))
        active = storage.start_session(self.subject_id, "Aula", at=local(29, 9))
        active = storage.pause_session(active.id, at=local(29, 9, 1))
        before = self.snapshot()
        script = """
import json, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    storage.initialize_database()
    old = storage.read_default_daily_goal(date(2026, 10, 25))
    view = storage.read_study_overview(at=datetime.fromisoformat(sys.argv[2]),
                                     tz=timezone(timedelta(hours=-3)))
    print(json.dumps([old.id, old.duration_seconds, view.goal.id, view.goal.duration_seconds,
                      view.total_seconds, view.active_session.id, view.active_session.status,
                      [session.id for session in storage.read_session_history().sessions]]))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path), local(29).isoformat()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(result.stdout),
                         [first.id, 3600, current.id, 7200, 180, active.id, "paused", [manual.id]])
        self.assertEqual(self.snapshot(), before)
