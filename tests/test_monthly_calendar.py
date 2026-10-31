"""Calendário mensal com relógio simulado e SQLite temporário."""

import calendar
import json
import sqlite3
import subprocess
import sys
from contextlib import closing, contextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import storage
from test_streaks import SeasonZone
from test_timer import TemporaryDatabaseTest


LOCAL = timezone(timedelta(hours=-3))
NOW = datetime(2026, 11, 3, 12, tzinfo=LOCAL)


def local(day, hour=12, minute=0, second=0, *, month=10, year=2026):
    return datetime(year, month, day, hour, minute, second, tzinfo=LOCAL)


class MonthlyCalendarTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        for name in ("Python", "SQL"):
            storage.add_subject(name)
        self.subjects = {name: key for key, name in storage.list_subject_options()}

    def snapshot(self):
        tables = ("subjects", "sessions", "session_events", "subject_timer_settings",
                  "pomodoro_state", "daily_goal_defaults", "daily_goal_overrides")
        return {table: self.execute(f"SELECT * FROM {table} ORDER BY 1") for table in tables}

    def read(self, year=2026, month=10, *, moment=None, zone=LOCAL):
        before = self.snapshot()
        with patch.object(storage, "refresh_active_session", side_effect=AssertionError("Consolidação inesperada")):
            result = storage.read_monthly_calendar(year, month, at=moment or NOW, tz=zone)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((result.year, result.month), (year, month))
        self.assertEqual(result.today, (moment or NOW).astimezone(zone).date())
        return result

    def default(self, day, seconds):
        return storage.set_default_daily_goal(
            seconds, storage.read_default_daily_goal(day.date()), day.date(), at=day, tz=LOCAL,
        )

    def manual(self, started, seconds=60, *, subject="Python", topic=None, now=NOW):
        return storage.create_manual_session(
            self.subjects[subject], "Leitura", started, seconds, topic, at=now,
        )

    def test_every_month_contains_exactly_its_gregorian_days_in_order(self):
        for year, month in ((2024, 2), (2025, 2), (2100, 2), (2026, 4), (2026, 12), (2027, 1)):
            with self.subTest(year=year, month=month):
                result = self.read(year, month)
                expected = tuple(date(year, month, number)
                                 for number in range(1, calendar.monthrange(year, month)[1] + 1))
                self.assertEqual(tuple(item.day for item in result.days), expected)
                self.assertEqual(tuple(item.plan.day for item in result.days), expected)
                self.assertTrue(all(item.study_seconds == 0 and item.plan.effective_seconds is None
                                    for item in result.days))
        self.assertEqual(self.execute("PRAGMA user_version"), [(7,)])
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_invalid_year_month_and_naive_moment_are_rejected_before_connection(self):
        before = self.snapshot()
        with patch.object(storage, "_connection", side_effect=AssertionError("Conexão inesperada")) as connection:
            for year, month in ((None, 10), (True, 10), (2026.0, 10), (0, 10), (10000, 10),
                                (2026, None), (2026, False), (2026, 1.0), (2026, 0), (2026, 13)):
                with self.subTest(year=year, month=month):
                    with self.assertRaises(ValueError):
                        storage.read_monthly_calendar(year, month, at=NOW, tz=LOCAL)
            with self.assertRaises(ValueError):
                storage.read_monthly_calendar(2026, 10, at=NOW.replace(tzinfo=None), tz=LOCAL)
            connection.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_default_periods_specific_goals_rest_and_unconfigured_dates_keep_their_priority(self):
        first = self.default(local(15), 600)
        second = self.default(local(29), 1200)
        specific = storage.set_daily_goal_override(date(2026, 10, 5), 300, False, None)
        rest = storage.set_daily_goal_override(date(2026, 10, 20), None, True, None)
        future = storage.set_daily_goal_override(date(2026, 10, 31), 1800, False, None)
        result = self.read(moment=local(30))
        by_day = {item.day.day: item for item in result.days}
        self.assertIsNone(by_day[1].plan.default_goal)
        self.assertIsNone(by_day[1].plan.effective_seconds)
        self.assertEqual(by_day[5].plan.override, specific)
        self.assertEqual(by_day[5].plan.effective_seconds, 300)
        self.assertEqual(by_day[15].plan.default_goal, first)
        self.assertEqual(by_day[28].plan.effective_seconds, 600)
        self.assertEqual(by_day[20].plan.override, rest)
        self.assertTrue(by_day[20].plan.is_rest_day)
        self.assertIsNone(by_day[20].plan.effective_seconds)
        self.assertEqual(by_day[29].plan.default_goal, second)
        self.assertEqual(by_day[29].plan.effective_seconds, 1200)
        self.assertEqual(by_day[31].plan.override, future)
        self.assertEqual(by_day[31].plan.effective_seconds, 1800)
        self.assertTrue(all(item.study_seconds == 0 for item in result.days))

    def test_an_overnight_session_is_split_across_december_and_january(self):
        self.manual(local(31, 23, 59, 30, month=12, year=2025), 60)
        december = self.read(2025, 12, moment=local(2, month=1))
        january = self.read(2026, 1, moment=local(2, month=1))
        self.assertEqual(december.days[-1].study_seconds, 30)
        self.assertEqual(january.days[0].study_seconds, 30)
        self.assertEqual(sum(item.study_seconds for item in december.days), 30)
        self.assertEqual(sum(item.study_seconds for item in january.days), 30)

    def test_current_study_is_clipped_at_the_query_and_future_days_and_months_have_zero_study(self):
        self.manual(local(30, 8), 600)
        self.manual(local(31, 8), 600)
        result = self.read(moment=local(30, 8, 5))
        self.assertEqual(result.days[29].study_seconds, 300)
        self.assertEqual(result.days[30].study_seconds, 0)
        self.assertEqual(sum(item.study_seconds for item in result.days), 300)
        self.assertTrue(all(item.study_seconds == 0
                            for item in self.read(2026, 11, moment=local(30, 8, 5)).days))

    def test_active_progressive_study_crosses_months_without_counting_pauses_or_counting_twice(self):
        active = storage.start_session(self.subjects["Python"], "Aula", at=local(30, 23, 59, 40, month=9))
        storage.pause_session(active.id, at=local(1, 0, 0, 10))
        storage.resume_session(active.id, at=local(2, 8))
        paused = storage.pause_session(active.id, at=local(2, 8, 0, 20))
        result = self.read(moment=local(3))
        self.assertEqual((result.days[0].study_seconds, result.days[1].study_seconds), (10, 20))
        self.assertEqual(sum(item.study_seconds for item in result.days), 30)
        self.assertEqual(storage.get_active_session(), paused)

    def test_an_overdue_pomodoro_is_capped_without_consolidating_its_phase(self):
        active = storage.start_session(
            self.subjects["Python"], "Exercícios", mode="pomodoro", focus_seconds=20,
            break_seconds=10, at=local(30, 23, 59, 50, month=9),
        )
        result = self.read(moment=local(3))
        self.assertEqual(result.days[0].study_seconds, 10)
        self.assertEqual(sum(item.study_seconds for item in result.days), 10)
        self.assertEqual(storage.get_active_session(), active)
        self.assertEqual(storage.get_active_session().pomodoro.state, "running")
        self.assertEqual(self.row_counts(), (1, 1))

    def test_synthetic_short_and_long_local_days_keep_23_and_25_hours_of_study(self):
        zone = SeasonZone()
        for selected, hours in ((date(2026, 3, 29), 23), (date(2026, 10, 25), 25)):
            with self.subTest(day=selected):
                start = datetime.combine(selected, datetime.min.time(), tzinfo=zone)
                self.manual(start, hours * 3600)
                result = self.read(selected.year, selected.month, zone=zone)
                self.assertEqual(result.days[selected.day - 1].study_seconds, hours * 3600)
                self.assertEqual(sum(item.study_seconds for item in result.days), hours * 3600)

    def test_multiple_subjects_and_tenths_of_a_second_sum_exactly_and_preserve_active_state(self):
        for index in range(10):
            subject = "Python" if index % 2 == 0 else "SQL"
            started = local(29, 8, index)
            session = storage.start_session(self.subjects[subject], "Aula", at=started)
            storage.finish_session(session.id, at=started + timedelta(milliseconds=100))
        active = storage.start_session(self.subjects["SQL"], "Revisão", at=local(30, 8))
        paused = storage.pause_session(active.id, at=local(30, 8, 0, 5))
        result = self.read(moment=local(30))
        self.assertEqual(result.days[28].study_seconds, 1)
        self.assertEqual(result.days[29].study_seconds, 5)
        self.assertEqual(storage.get_active_session(), paused)

    def test_the_requested_zone_changes_the_day_without_changing_the_records(self):
        self.manual(datetime(2026, 10, 30, 1, tzinfo=UTC), 600)
        utc = self.read(moment=local(31), zone=UTC)
        local_result = self.read(moment=local(31), zone=LOCAL)
        self.assertEqual((utc.days[29].study_seconds, utc.days[28].study_seconds), (600, 0))
        self.assertEqual((local_result.days[28].study_seconds, local_result.days[29].study_seconds), (600, 0))

    def test_new_reads_reflect_retroactive_edits_and_deletions_without_mutating_old_results(self):
        saved = self.manual(local(28, 8), 60)
        old = self.read(moment=local(30))
        edited = storage.update_finished_session(
            saved, saved.subject_id, saved.study_type, started_at=local(29, 8), duration_seconds=120, at=NOW,
        )
        current = self.read(moment=local(30))
        self.assertEqual((old.days[27].study_seconds, old.days[28].study_seconds), (60, 0))
        self.assertEqual((current.days[27].study_seconds, current.days[28].study_seconds), (0, 120))
        storage.delete_finished_session(edited)
        self.assertTrue(all(item.study_seconds == 0 for item in self.read(moment=local(30)).days))

    def test_goals_and_study_are_read_from_one_consistent_snapshot_during_a_concurrent_write(self):
        goal = self.default(local(1), 60)
        saved = self.manual(local(28, 8), 60)
        self.execute("PRAGMA journal_mode = WAL")
        original_connection = storage._connection
        changed = False

        @contextmanager
        def interleaved_connection(*, write=False):
            nonlocal changed
            self.assertFalse(write)
            with original_connection(write=write) as connection:
                select_count = 0

                def trace(sql):
                    nonlocal select_count, changed
                    if sql.lstrip().upper().startswith("SELECT"):
                        select_count += 1
                        if select_count == 2 and not changed:
                            changed = True
                            with closing(sqlite3.connect(self.path)) as writer:
                                with writer:
                                    writer.execute("UPDATE daily_goal_defaults SET duration_seconds = 120 WHERE id = ?",
                                                   (goal.id,))
                                    writer.execute("DELETE FROM session_events WHERE session_id = ?", (saved.id,))
                                    writer.execute("DELETE FROM sessions WHERE id = ?", (saved.id,))

                connection.set_trace_callback(trace)
                yield connection

        with patch.object(storage, "_connection", side_effect=interleaved_connection):
            result = storage.read_monthly_calendar(2026, 10, at=local(30), tz=LOCAL)
        self.assertTrue(changed)
        self.assertEqual((result.days[27].plan.default_goal, result.days[27].study_seconds), (goal, 60))
        current = self.read(moment=local(30))
        self.assertEqual((current.days[27].plan.effective_seconds, current.days[27].study_seconds), (120, 0))
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_monthly_reads_batch_queries_instead_of_querying_every_day(self):
        self.default(local(1), 60)
        for number in range(1, 32):
            storage.set_daily_goal_override(date(2026, 10, number), 60 + number, False, None)
        self.manual(local(28, 8), 60)
        statements = []
        original_connection = storage._connection

        @contextmanager
        def traced_connection(*, write=False):
            self.assertFalse(write)
            with original_connection(write=write) as connection:
                connection.set_trace_callback(statements.append)
                yield connection

        before = self.snapshot()
        with patch.object(storage, "_connection", side_effect=traced_connection):
            result = storage.read_monthly_calendar(2026, 10, at=local(30), tz=LOCAL)
        selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        self.assertEqual(len(result.days), 31)
        self.assertLess(len(selects), 15, "A leitura mensal não deve fazer uma consulta por dia.")
        self.assertEqual(self.snapshot(), before)

    def test_year_one_and_december_9999_do_not_require_an_out_of_range_next_month(self):
        ancient = self.read(1, 1, zone=timezone(timedelta(hours=14)))
        future = self.read(9999, 12)
        self.assertEqual((ancient.days[0].day, ancient.days[-1].day), (date(1, 1, 1), date(1, 1, 31)))
        self.assertEqual((future.days[0].day, future.days[-1].day), (date(9999, 12, 1), date.max))
        maximum = datetime.max.replace(tzinfo=UTC)
        self.manual(datetime(9999, 12, 31, 23, 59, 58, tzinfo=UTC), 1, now=maximum)
        self.assertTrue(all(item.study_seconds == 0 for item in self.read(9999, 12).days))
        last_month = self.read(9999, 12, moment=maximum, zone=UTC)
        self.assertEqual(last_month.today, date.max)
        self.assertEqual(last_month.days[-1].study_seconds, 1)

    def test_an_ancient_plan_keeps_local_calendar_and_streak_reads_usable_without_study_data(self):
        ancient = storage.set_daily_goal_override(date.min, 60, False, None)
        before = self.snapshot()
        month = self.read(1, 1, zone=None)
        self.assertEqual(month.days[0].plan.override, ancient)
        self.assertEqual(month.days[0].plan.effective_seconds, 60)
        self.assertTrue(all(item.study_seconds == 0 for item in month.days))
        overview = storage.read_study_overview(at=NOW, tz=None, include_streak_base=True)
        self.assertEqual(overview.streak_base,
                         storage.StreakBase(NOW.astimezone().date(), 0, 0))
        self.assertEqual(overview.total_seconds, 0)
        self.assertEqual(storage.read_daily_goal_override(date.min), ancient)
        self.assertEqual(self.snapshot(), before)

    def test_another_process_recovers_the_month_and_preserves_ids_preferences_and_active_state(self):
        goal = self.default(local(1), 60)
        saved = self.manual(local(28, 8), 60, topic="Funções 🚀")
        rest = storage.set_daily_goal_override(date(2026, 10, 29), None, True, None)
        active = storage.start_session(self.subjects["SQL"], "Aula", mode="pomodoro", focus_seconds=60,
                                       break_seconds=30, at=local(30, 8))
        paused = storage.pause_session(active.id, at=local(30, 8, 0, 5))
        before = self.snapshot()
        script = """
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    storage.initialize_database()
    result = storage.read_monthly_calendar(2026, 10, at=datetime.fromisoformat(sys.argv[2]),
                                          tz=timezone(timedelta(hours=-3)))
    active = storage.get_active_session()
    print(json.dumps([result.today.isoformat(), len(result.days),
                      result.days[27].study_seconds, result.days[27].plan.default_goal.id,
                      result.days[28].plan.override.id, result.days[28].plan.is_rest_day,
                      result.days[29].study_seconds, active.id, active.status,
                      storage.get_subject_timer_settings(active.subject_id).preset,
                      [item.id for item in storage.read_session_history().sessions]]))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path), local(30).isoformat()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(result.stdout),
                         ["2026-10-30", 31, 60, goal.id, rest.id, True, 5,
                          paused.id, "paused", "custom", [saved.id]])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_active_session(), paused)
