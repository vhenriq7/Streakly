"""Estatísticas por período usando relógio simulado e SQLite temporário."""

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


class PeriodStatisticsTests(TemporaryDatabaseTest):
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

    def manual(self, started, seconds=60, *, subject="Python", now=NOW):
        return storage.create_manual_session(
            self.subjects[subject], "Leitura", started, seconds, at=now,
        )

    def read(self, start, end, *, moment=NOW, zone=LOCAL):
        before = self.snapshot()
        with patch.object(storage, "refresh_active_session", side_effect=AssertionError("Consolidação inesperada")):
            result = storage.read_period_statistics(start, end, at=moment, tz=zone)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((result.start_day, result.end_day, result.as_of),
                         (start, end, moment.astimezone(UTC)))
        self.assertEqual(result.as_of.tzinfo, UTC)
        self.assertEqual(result.day_count, (end - start).days + 1)
        self.assertEqual(result.average_seconds, result.total_seconds / result.day_count)
        return result

    def test_totals_sum_subjects_and_the_average_includes_empty_and_future_days(self):
        start, end = date(2026, 10, 28), date(2026, 11, 1)
        empty = self.read(start, end, moment=local(30))
        self.assertEqual((empty.day_count, empty.total_seconds, empty.average_seconds), (5, 0, 0))
        self.manual(local(28, 8), 60)
        self.manual(local(30, 8), 120, subject="SQL")
        result = self.read(start, end, moment=local(30))
        self.assertEqual((result.day_count, result.total_seconds, result.average_seconds), (5, 180, 36))
        single = self.read(date(2026, 10, 30), date(2026, 10, 30), moment=local(30))
        self.assertEqual((single.day_count, single.total_seconds, single.average_seconds), (1, 120, 120))
        self.assertEqual(empty.total_seconds, 0)
        self.assertEqual(self.execute("PRAGMA user_version"), [(7,)])
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_overnight_study_crosses_month_and_year_using_the_requested_local_days(self):
        self.manual(local(31, 23, 59, 30, month=12, year=2025), 120)
        december = date(2025, 12, 31)
        january = date(2026, 1, 1)
        self.assertEqual(self.read(december, december).total_seconds, 30)
        self.assertEqual(self.read(january, january).total_seconds, 90)
        both = self.read(december, january)
        self.assertEqual((both.total_seconds, both.average_seconds), (120, 60))
        self.assertEqual(self.read(december, december, zone=UTC).total_seconds, 0)
        self.assertEqual(self.read(january, january, zone=UTC).total_seconds, 120)

    def test_study_is_clipped_at_the_query_instant_even_when_later_data_is_saved(self):
        self.manual(local(30, 8), 600)
        self.manual(local(31, 8), 600, subject="SQL")
        result = self.read(date(2026, 10, 30), date(2026, 10, 31), moment=local(30, 8, 5))
        self.assertEqual((result.total_seconds, result.average_seconds), (300, 150))
        future = self.read(date(2026, 10, 31), date(2026, 11, 1), moment=local(30, 8, 5))
        self.assertEqual((future.day_count, future.total_seconds, future.average_seconds), (2, 0, 0))
        completed = self.read(date(2026, 10, 30), date(2026, 10, 31))
        self.assertEqual(completed.total_seconds, 1200)

    def test_an_active_progressive_session_excludes_pauses_and_is_not_counted_twice(self):
        active = storage.start_session(self.subjects["Python"], "Aula", at=local(28, 23, 59, 40))
        storage.pause_session(active.id, at=local(29, 0, 0, 10))
        running = storage.resume_session(active.id, at=local(30, 8))
        moment = local(30, 8, 0, 20)
        current = self.read(date(2026, 10, 29), date(2026, 10, 30), moment=moment)
        self.assertEqual((current.total_seconds, current.average_seconds), (30, 15))
        self.assertEqual(self.read(date(2026, 10, 28), date(2026, 10, 28), moment=moment).total_seconds, 20)
        self.assertEqual(storage.get_active_session(), running)
        paused = storage.pause_session(active.id, at=moment)
        self.assertEqual(self.read(date(2026, 10, 28), date(2026, 10, 30)).total_seconds, 50)
        self.assertEqual(storage.get_active_session(), paused)

    def test_overdue_pomodoro_focus_waiting_and_breaks_are_read_without_writing(self):
        active = storage.start_session(
            self.subjects["Python"], "Exercícios", mode="pomodoro", focus_seconds=20,
            break_seconds=10, at=local(28, 23, 59, 50),
        )
        start, end = date(2026, 10, 28), date(2026, 10, 30)
        self.assertEqual(self.read(start, end).total_seconds, 20)
        self.assertEqual(self.read(date(2026, 10, 29), end).total_seconds, 10)
        self.assertEqual(storage.get_active_session(), active)
        self.assertEqual(self.row_counts(), (1, 1))
        storage.refresh_active_session(at=local(29, 0, 0, 10))
        waiting = storage.get_active_session()
        self.assertEqual(self.read(start, end).total_seconds, 20)
        self.assertEqual(storage.get_active_session(), waiting)
        in_break = storage.start_next_phase(active.id, at=local(29, 8))
        self.assertEqual(self.read(start, end).total_seconds, 20)
        self.assertEqual(storage.get_active_session(), in_break)
        self.assertEqual(in_break.pomodoro.phase, "break")

    def test_local_days_with_23_or_25_hours_keep_their_actual_study_duration(self):
        zone = SeasonZone()
        for selected, hours in ((date(2026, 3, 29), 23), (date(2026, 10, 25), 25)):
            with self.subTest(day=selected):
                started = datetime.combine(selected, datetime.min.time(), tzinfo=zone)
                self.manual(started, hours * 3600)
                result = self.read(selected, selected, zone=zone)
                self.assertEqual((result.day_count, result.total_seconds, result.average_seconds),
                                 (1, hours * 3600, hours * 3600))

    def test_fractional_intervals_and_multiple_sessions_sum_without_rounding_down(self):
        for index in range(10):
            started = local(29, 8, index)
            subject = "Python" if index % 2 == 0 else "SQL"
            active = storage.start_session(self.subjects[subject], "Aula", at=started)
            storage.finish_session(active.id, at=started + timedelta(milliseconds=100))
        self.assertEqual(self.read(date(2026, 10, 29), date(2026, 10, 29)).total_seconds, 1)
        started = local(30, 8)
        active = storage.start_session(self.subjects["Python"], "Revisão", at=started)
        paused = storage.pause_session(active.id, at=started + timedelta(microseconds=1))
        result = self.read(date(2026, 10, 29), date(2026, 10, 30))
        self.assertEqual(result.total_seconds, 1.000001)
        self.assertEqual(result.average_seconds, 1.000001 / 2)
        self.assertEqual(storage.get_active_session(), paused)

    def test_edits_session_deletions_and_subject_deletions_recalculate_the_period(self):
        saved = self.manual(local(28, 8), 60)
        other = self.manual(local(29, 8), 120, subject="SQL")
        start, end = date(2026, 10, 28), date(2026, 10, 29)
        old = self.read(start, end)
        edited = storage.update_finished_session(
            saved, saved.subject_id, saved.study_type, started_at=local(30, 8),
            duration_seconds=180, at=NOW,
        )
        self.assertEqual(self.read(start, end).total_seconds, 120)
        self.assertEqual(self.read(start, date(2026, 10, 30)).total_seconds, 300)
        storage.delete_finished_session(edited)
        self.assertEqual(self.read(start, date(2026, 10, 30)).total_seconds, 120)
        storage.delete_subject(storage.read_subject_deletion_preview(other.subject_id))
        self.assertEqual(self.read(start, end).total_seconds, 0)
        self.assertEqual(old.total_seconds, 180)

    def test_invalid_date_types_reversed_ranges_and_naive_instants_fail_before_connection(self):
        class DateSubclass(date):
            pass

        selected = date(2026, 10, 30)
        invalid_dates = (None, True, 30, "2026-10-30", local(30), DateSubclass(2026, 10, 30))
        with patch.object(storage, "_connection", side_effect=AssertionError("Conexão inesperada")) as connection:
            for invalid in invalid_dates:
                for start, end in ((invalid, selected), (selected, invalid)):
                    with self.subTest(start=start, end=end):
                        with self.assertRaises(ValueError):
                            storage.read_period_statistics(start, end, at=NOW, tz=LOCAL)
            with self.assertRaises(ValueError):
                storage.read_period_statistics(selected, selected - timedelta(days=1), at=NOW, tz=LOCAL)
            for invalid in (NOW.replace(tzinfo=None), True, "agora"):
                with self.subTest(moment=invalid):
                    with self.assertRaises(ValueError):
                        storage.read_period_statistics(selected, selected, at=invalid, tz=LOCAL)
            connection.assert_not_called()

    def test_period_uses_one_snapshot_during_a_concurrent_write_and_batches_its_queries(self):
        saved = self.manual(local(28, 8), 60)
        self.execute("PRAGMA journal_mode = WAL")
        original_connection = storage._connection
        statements = []
        changed = False

        @contextmanager
        def interleaved_connection(*, write=False):
            nonlocal changed
            self.assertFalse(write)
            with original_connection(write=write) as connection:
                select_count = 0

                def trace(sql):
                    nonlocal select_count, changed
                    statements.append(sql)
                    if sql.lstrip().upper().startswith("SELECT"):
                        select_count += 1
                        if select_count == 2 and not changed:
                            changed = True
                            with closing(sqlite3.connect(self.path)) as writer:
                                with writer:
                                    writer.execute("DELETE FROM session_events WHERE session_id = ?", (saved.id,))
                                    writer.execute("DELETE FROM sessions WHERE id = ?", (saved.id,))

                connection.set_trace_callback(trace)
                yield connection

        start, end = date(2026, 1, 1), date(2026, 12, 31)
        with patch.object(storage, "_connection", side_effect=interleaved_connection):
            result = storage.read_period_statistics(start, end, at=NOW, tz=LOCAL)
        self.assertTrue(changed)
        self.assertEqual((result.day_count, result.total_seconds), (365, 60))
        selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        self.assertLess(len(selects), 15, "A consulta não deve buscar cada dia do período separadamente.")
        self.assertEqual(self.read(start, end).total_seconds, 0)
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_year_one_ancient_planning_and_the_last_supported_day_do_not_overflow(self):
        ancient = storage.set_daily_goal_override(date.min, 60, False, None)
        for zone in (None, UTC, timezone(timedelta(hours=14))):
            with self.subTest(zone=zone):
                result = self.read(date.min, date(1, 1, 31), zone=zone)
                self.assertEqual((result.day_count, result.total_seconds), (31, 0))
        self.assertEqual(storage.read_daily_goal_override(date.min), ancient)
        maximum = datetime.max.replace(tzinfo=UTC)
        self.manual(datetime(9999, 12, 31, 23, 59, 58, tzinfo=UTC), 1, now=maximum)
        self.assertEqual(self.read(date.max, date.max).total_seconds, 0)
        result = self.read(date.max, date.max, moment=maximum, zone=UTC)
        self.assertEqual((result.day_count, result.total_seconds, result.average_seconds), (1, 1, 1))

    def test_another_process_recovers_totals_without_changing_ids_preferences_or_active_state(self):
        saved = self.manual(local(28, 8), 60)
        active = storage.start_session(
            self.subjects["SQL"], "Aula", mode="pomodoro", focus_seconds=60,
            break_seconds=30, at=local(30, 8),
        )
        paused = storage.pause_session(active.id, at=local(30, 8, 0, 5))
        before = self.snapshot()
        script = """
import json, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    storage.initialize_database()
    result = storage.read_period_statistics(date(2026, 10, 28), date(2026, 10, 30),
                                          at=datetime.fromisoformat(sys.argv[2]),
                                          tz=timezone(timedelta(hours=-3)))
    active = storage.get_active_session()
    print(json.dumps([result.start_day.isoformat(), result.end_day.isoformat(),
                      result.as_of.isoformat(), result.day_count, result.total_seconds,
                      result.average_seconds, active.id, active.status,
                      storage.get_subject_timer_settings(active.subject_id).preset,
                      [item.id for item in storage.read_session_history().sessions]]))
"""
        moment = local(30)
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path), moment.isoformat()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(result.stdout),
                         ["2026-10-28", "2026-10-30", moment.astimezone(UTC).isoformat(),
                          3, 65, 65 / 3, paused.id, "paused", "custom", [saved.id]])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_active_session(), paused)
