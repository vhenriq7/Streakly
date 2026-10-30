"""Planejamento por data e descanso com SQLite temporário e relógio simulado."""

import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import storage
from test_timer import TemporaryDatabaseTest, at


LOCAL = timezone(timedelta(hours=-3))
DAY = date(2026, 10, 30)


def local(day, hour=12, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=LOCAL)


class DateGoalTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        storage.add_subject("Python")
        self.subject_id = storage.list_subject_options()[0][0]

    def snapshot(self, *, overrides=True):
        tables = ["subjects", "sessions", "session_events", "subject_timer_settings",
                  "pomodoro_state", "daily_goal_defaults"]
        if overrides:
            tables.append("daily_goal_overrides")
        return {table: self.execute(f"SELECT * FROM {table} ORDER BY 1") for table in tables}

    def default(self, seconds=3600, expected=None, *, day=20):
        return storage.set_default_daily_goal(seconds, expected, date(2026, 10, day),
                                              at=local(day), tz=LOCAL)

    def specific(self, seconds=600, expected=None, *, day=DAY):
        return storage.set_daily_goal_override(day, seconds, False, expected)

    def rest(self, expected=None, *, day=DAY):
        return storage.set_daily_goal_override(day, None, True, expected)

    def test_empty_dates_and_default_plans_are_read_only(self):
        before = self.snapshot()
        for day in (date(2020, 1, 1), DAY, date(2030, 1, 1)):
            with self.subTest(day=day):
                self.assertIsNone(storage.read_daily_goal_override(day))
                plan = storage.read_daily_plan(day)
                self.assertEqual((plan.day, plan.default_goal, plan.override, plan.effective_seconds,
                                  plan.is_rest_day), (day, None, None, None, False))
        self.assertEqual(self.snapshot(), before)
        default = self.default()
        before = self.snapshot()
        plan = storage.read_daily_plan(DAY)
        self.assertEqual((plan.default_goal, plan.override, plan.effective_seconds, plan.is_rest_day),
                         (default, None, 3600, False))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.execute("PRAGMA user_version"), [(7,)])

    def test_specific_past_and_future_dates_override_only_the_selected_day(self):
        first = self.default()
        second = self.default(7200, first, day=29)
        before = self.snapshot(overrides=False)
        old_day, future_day = date(2026, 10, 10), date(2030, 1, 1)
        old = self.specific(600, day=old_day)
        today = self.specific(1200)
        future = self.specific(1800, day=future_day)
        self.assertEqual(len({old.id, today.id, future.id}), 3)
        for override in (old, today, future):
            with self.subTest(day=override.day):
                self.assertEqual(str(UUID(override.id)), override.id)
                plan = storage.read_daily_plan(override.day)
                self.assertEqual((plan.override, plan.effective_seconds, plan.is_rest_day),
                                 (override, override.duration_seconds, False))
        self.assertIsNone(storage.read_daily_plan(old_day).default_goal)
        self.assertEqual(storage.read_daily_plan(date(2026, 10, 21)).effective_seconds, 3600)
        self.assertEqual(storage.read_daily_plan(date(2026, 10, 31)).effective_seconds, 7200)
        self.assertEqual(storage.read_default_daily_goal(date(2026, 10, 28)), first)
        self.assertEqual(storage.read_default_daily_goal(DAY), second)
        self.assertEqual(self.snapshot(overrides=False), before)

    def test_rest_preserves_registered_study_and_applies_at_local_midnight(self):
        default = self.default()
        saved = storage.create_manual_session(
            self.subject_id, "Leitura", local(29, 23, 50), 20 * 60, at=local(30),
        )
        before = self.snapshot(overrides=False)
        rest = self.rest()
        # 02:30 UTC ainda pertence a 29/10 no fuso escolhido.
        previous = storage.read_study_overview(at=datetime(2026, 10, 30, 2, 59, tzinfo=UTC), tz=LOCAL)
        current = storage.read_study_overview(at=local(30), tz=LOCAL)
        self.assertEqual((previous.day, previous.goal, previous.goal_override, previous.total_seconds),
                         (date(2026, 10, 29), default, None, 540))
        self.assertEqual((current.day, current.goal, current.goal_override, current.total_seconds),
                         (DAY, default, rest, 600))
        plan = storage.read_daily_plan(DAY)
        self.assertTrue(plan.is_rest_day)
        self.assertIsNone(plan.effective_seconds)
        self.assertEqual(storage.get_session(saved.id), saved)
        self.assertEqual(self.snapshot(overrides=False), before)

    def test_edits_preserve_the_uuid_and_identical_values_do_not_write(self):
        initial = self.specific(600)
        resting = self.rest(initial)
        edited = self.specific(900, resting)
        self.assertEqual((initial.id, initial.day), (resting.id, resting.day))
        self.assertEqual((initial.id, initial.day), (edited.id, edited.day))
        self.execute("""
            CREATE TRIGGER reject_override_update BEFORE UPDATE ON daily_goal_overrides
            BEGIN SELECT RAISE(ABORT, 'gravação desnecessária'); END
        """)
        before = self.snapshot()
        self.assertEqual(self.specific(900, edited), edited)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.execute("SELECT count(*) FROM daily_goal_overrides"), [(1,)])

    def test_clear_returns_to_the_current_default_or_an_unconfigured_day(self):
        first = self.default()
        override = self.specific()
        second = self.default(7200, first, day=29)
        before = self.snapshot(overrides=False)
        self.assertIsNone(storage.clear_daily_goal_override(DAY, override))
        plan = storage.read_daily_plan(DAY)
        self.assertEqual((plan.default_goal, plan.override, plan.effective_seconds), (second, None, 7200))
        self.assertEqual(self.snapshot(overrides=False), before)
        old_day = date(2020, 1, 1)
        rest = self.rest(day=old_day)
        storage.clear_daily_goal_override(old_day, rest)
        self.assertIsNone(storage.read_daily_plan(old_day).effective_seconds)
        before = self.snapshot()
        self.assertIsNone(storage.clear_daily_goal_override(old_day, None))
        self.assertEqual(self.snapshot(), before)

    def test_invalid_values_and_mismatched_expected_dates_are_rejected_before_connection(self):
        known = self.specific()
        parameters = dict(day=DAY, duration_seconds=600, is_rest_day=False, expected=known)
        attempts = [{"day": value} for value in (None, True, "2026-10-30", local(30))]
        attempts += [{"duration_seconds": value} for value in (None, True, False, 0, -1, 1.5, "600", 2**63)]
        attempts += [{"is_rest_day": value} for value in (None, 0, 1, "rest")]
        attempts += [{"expected": value} for value in (False, "meta", 1, object())]
        attempts += [{"expected": replace(known, day=date(2026, 10, 29))}]
        attempts += [{"is_rest_day": True, "duration_seconds": value} for value in (0, 1, False, "")]
        before = self.snapshot()
        with patch.object(storage, "_connection", side_effect=AssertionError("Conexão inesperada")) as connection:
            for day in (None, True, "2026-10-30", local(30)):
                with self.subTest(read_day=day):
                    for read in (storage.read_daily_goal_override, storage.read_daily_plan):
                        with self.assertRaises(ValueError):
                            read(day)
                    with self.assertRaises(ValueError):
                        storage.clear_daily_goal_override(day, None)
            for values in attempts:
                with self.subTest(values=values):
                    with self.assertRaises(ValueError):
                        storage.set_daily_goal_override(**(parameters | values))
            for invalid in (False, "meta", 1, replace(known, day=date(2026, 10, 29))):
                with self.subTest(clear_expected=invalid):
                    with self.assertRaises(ValueError):
                        storage.clear_daily_goal_override(DAY, invalid)
            connection.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        maximum = self.specific(2**63 - 1, known)
        self.assertEqual(maximum.duration_seconds, 2**63 - 1)

    def test_sql_constraints_refuse_ambiguous_or_invalid_rest_and_goal_rows(self):
        before = self.snapshot()
        for duration, rest in ((None, 0), (0, 0), (-1, 0), (600, 1), (None, 2)):
            with self.subTest(duration=duration, rest=rest):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.execute("""
                        INSERT INTO daily_goal_overrides (id, day, duration_seconds, is_rest_day)
                        VALUES (?, ?, ?, ?)
                    """, ("invalid", DAY.isoformat(), duration, rest))
                self.assertEqual(self.snapshot(), before)

    def test_stale_update_and_clear_cannot_overwrite_a_changed_or_created_override(self):
        initial = self.specific()
        current = self.rest(initial)
        before = self.snapshot()
        for expected in (None, initial):
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError):
                    self.specific(1200, expected)
                with self.assertRaises(ValueError):
                    storage.clear_daily_goal_override(DAY, expected)
                self.assertEqual(self.snapshot(), before)
        storage.clear_daily_goal_override(DAY, current)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.rest(current)
        with self.assertRaises(ValueError):
            storage.clear_daily_goal_override(DAY, current)
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_updates_from_one_snapshot_have_only_one_winner(self):
        initial = self.specific()
        barrier = threading.Barrier(2)

        def attempt(seconds):
            barrier.wait(timeout=5)
            try:
                return self.specific(seconds, initial)
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, (900, 1200)))
        winners = [item for item in results if item is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(storage.read_daily_goal_override(DAY), winners[0])
        self.assertEqual(winners[0].id, initial.id)

    def test_concurrent_clear_and_update_cannot_apply_both_old_intentions(self):
        initial = self.specific()
        barrier = threading.Barrier(2)

        def attempt(action):
            barrier.wait(timeout=5)
            try:
                if action == "clear":
                    storage.clear_daily_goal_override(DAY, initial)
                else:
                    self.rest(initial)
                return action
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ("clear", "rest")))
        winners = [item for item in results if item is not None]
        self.assertEqual(len(winners), 1)
        result = storage.read_daily_goal_override(DAY)
        if winners == ["clear"]:
            self.assertIsNone(result)
        else:
            self.assertTrue(result.is_rest_day)
            self.assertEqual(result.id, initial.id)

    def test_failed_insert_update_and_clear_roll_back_the_whole_operation(self):
        self.default()
        storage.create_manual_session(self.subject_id, "Leitura", at(0), 10, at=at(1000))
        for operation in ("INSERT", "UPDATE", "DELETE"):
            with self.subTest(operation=operation):
                current = storage.read_daily_goal_override(DAY)
                if operation != "INSERT" and current is None:
                    current = self.specific()
                before = self.snapshot()
                self.execute(f"""
                    CREATE TRIGGER reject_date_goal BEFORE {operation} ON daily_goal_overrides
                    BEGIN SELECT RAISE(ABORT, 'falha simulada'); END
                """)
                try:
                    with self.assertRaises(sqlite3.DatabaseError):
                        if operation == "DELETE":
                            storage.clear_daily_goal_override(DAY, current)
                        else:
                            self.specific(1200, current)
                    self.assertEqual(self.snapshot(), before)
                finally:
                    self.execute("DROP TRIGGER reject_date_goal")
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_date_goal_apis_do_not_consolidate_an_overdue_pomodoro(self):
        self.default()
        active = storage.start_session(self.subject_id, "Exercícios", mode="pomodoro",
                                       focus_seconds=10, break_seconds=5, at=at(0))
        before = self.snapshot(overrides=False)
        with patch.object(storage, "refresh_active_session", side_effect=AssertionError("Consolidação inesperada")):
            specific = self.specific()
            self.assertEqual(storage.read_daily_goal_override(DAY), specific)
            self.assertEqual(storage.read_daily_plan(DAY).override, specific)
            rest = self.rest(specific)
            storage.clear_daily_goal_override(DAY, rest)
        self.assertEqual(self.snapshot(overrides=False), before)
        self.assertEqual(storage.get_active_session(), active)
        self.assertEqual(storage.get_active_session().pomodoro.state, "running")

    def test_daily_plan_uses_one_consistent_snapshot_for_default_and_override(self):
        default = self.default()
        override = self.specific()
        self.execute("PRAGMA journal_mode = WAL")
        original_read = storage._read_default_daily_goal
        changed = False

        def read_then_change(connection, day):
            nonlocal changed
            result = original_read(connection, day)
            if not changed:
                changed = True
                with closing(sqlite3.connect(self.path)) as writer:
                    with writer:
                        writer.execute("UPDATE daily_goal_defaults SET duration_seconds = 7200 WHERE id = ?",
                                       (default.id,))
                        writer.execute("UPDATE daily_goal_overrides SET duration_seconds = 1200 WHERE id = ?",
                                       (override.id,))
            return result

        with patch.object(storage, "_read_default_daily_goal", side_effect=read_then_change):
            plan = storage.read_daily_plan(DAY)
        self.assertTrue(changed)
        self.assertEqual((plan.default_goal, plan.override, plan.effective_seconds), (default, override, 600))
        current = storage.read_daily_plan(DAY)
        self.assertEqual((current.default_goal.duration_seconds, current.effective_seconds), (7200, 1200))

    def test_overview_keeps_study_and_date_override_in_the_same_snapshot(self):
        default = self.default()
        override = self.specific(120)
        saved = storage.create_manual_session(self.subject_id, "Leitura", local(30, 8), 120, at=local(30))
        self.execute("PRAGMA journal_mode = WAL")
        original_read = storage._read_default_daily_goal
        changed = False

        def read_then_change(connection, day):
            nonlocal changed
            result = original_read(connection, day)
            if not changed:
                changed = True
                with closing(sqlite3.connect(self.path)) as writer:
                    with writer:
                        writer.execute("UPDATE daily_goal_overrides SET duration_seconds = NULL, is_rest_day = 1 WHERE id = ?",
                                       (override.id,))
                        writer.execute("DELETE FROM session_events WHERE session_id = ?", (saved.id,))
                        writer.execute("DELETE FROM sessions WHERE id = ?", (saved.id,))
            return result

        with patch.object(storage, "_read_default_daily_goal", side_effect=read_then_change):
            overview = storage.read_study_overview(at=local(30), tz=LOCAL)
        self.assertTrue(changed)
        self.assertEqual((overview.goal, overview.goal_override, overview.total_seconds), (default, override, 120))
        current = storage.read_study_overview(at=local(30), tz=LOCAL)
        self.assertTrue(current.goal_override.is_rest_day)
        self.assertEqual(current.total_seconds, 0)

    def test_v6_migration_preserves_studies_default_history_and_is_repeatable(self):
        default = self.default()
        saved = storage.create_manual_session(self.subject_id, "Leitura", at(0), 10, at=at(1000))
        active = storage.start_session(self.subject_id, "Aula", mode="pomodoro",
                                       focus_seconds=10, break_seconds=5, at=at(100))
        active = storage.pause_session(active.id, at=at(105))
        self.execute("DROP TABLE daily_goal_overrides")
        self.execute("PRAGMA user_version = 6")
        before = self.snapshot(overrides=False)
        storage.initialize_database()
        storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(7,)])
        self.assertEqual(self.execute("SELECT * FROM daily_goal_overrides"), [])
        self.assertEqual(self.snapshot(overrides=False), before)
        self.assertEqual(storage.read_daily_plan(DAY).default_goal, default)
        self.assertEqual(storage.get_session(saved.id), saved)
        self.assertEqual(storage.get_active_session(), active)

    def test_failed_v6_migration_rolls_back_the_table_and_version_before_retry(self):
        self.default()
        self.execute("DROP TABLE daily_goal_overrides")
        self.execute("PRAGMA user_version = 6")
        before = self.snapshot(overrides=False)
        original_connection = storage._connection

        @contextmanager
        def reject_version(*, write=False):
            with original_connection(write=write) as connection:
                def authorize(action, first, second, database, source):
                    if action == sqlite3.SQLITE_PRAGMA and first == "user_version" and second == "7":
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                connection.set_authorizer(authorize)
                yield connection

        with patch.object(storage, "_connection", side_effect=reject_version):
            with self.assertRaises(sqlite3.DatabaseError):
                storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(6,)])
        self.assertEqual(self.execute("SELECT name FROM sqlite_master WHERE name = 'daily_goal_overrides'"), [])
        self.assertEqual(self.snapshot(overrides=False), before)
        storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(7,)])

    def test_another_process_recovers_overrides_and_preserves_default_and_study_ids(self):
        default = self.default()
        old_day, future_day = date(2026, 10, 10), date(2030, 1, 1)
        old = self.specific(600, day=old_day)
        rest = self.rest()
        future = self.specific(7200, day=future_day)
        saved = storage.create_manual_session(self.subject_id, "Leitura", local(30, 8), 120, at=local(30))
        active = storage.start_session(self.subject_id, "Aula", at=local(30, 9))
        active = storage.pause_session(active.id, at=local(30, 9, 1))
        before = self.snapshot()
        script = """
import json, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    storage.initialize_database()
    plans = [storage.read_daily_plan(date.fromisoformat(day)) for day in sys.argv[3:]]
    view = storage.read_study_overview(at=datetime.fromisoformat(sys.argv[2]),
                                     tz=timezone(timedelta(hours=-3)))
    print(json.dumps([[[plan.override.id, plan.effective_seconds, plan.is_rest_day] for plan in plans],
                      view.goal.id, view.goal_override.id, view.total_seconds,
                      view.active_session.id, view.active_session.status,
                      [item.id for item in storage.read_session_history().sessions]]))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path), local(30).isoformat(),
             old_day.isoformat(), DAY.isoformat(), future_day.isoformat()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(result.stdout), [
            [[old.id, 600, False], [rest.id, None, True], [future.id, 7200, False]],
            default.id, rest.id, 180, active.id, "paused", [saved.id],
        ])
        self.assertEqual(self.snapshot(), before)
