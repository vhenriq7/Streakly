"""Ofensivas com dias simulados e SQLite temporário, sem dados pessoais."""

import json
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from unittest.mock import patch

import storage
from test_daily_total import progressive
from test_timer import TemporaryDatabaseTest, at
from timer import PomodoroState, SessionEvent, session_study_seconds_by_day


LOCAL = timezone(timedelta(hours=-3))


def local(day, hour=12, minute=0, second=0):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=LOCAL)


class SeasonZone(tzinfo):
    """Fuso sintético com mudanças explícitas de uma hora, sem depender de tzdata."""

    def utcoffset(self, value):
        if value is None:
            return timedelta(0)
        naive = value.replace(tzinfo=None)
        start = datetime(2026, 3, 29, 2)
        repeated = datetime(2026, 10, 25, 1)
        end = datetime(2026, 10, 25, 2)
        summer = start <= naive < end
        if repeated <= naive < end and value.fold:
            summer = False
        return timedelta(hours=int(summer))

    def dst(self, value):
        return self.utcoffset(value)

    def tzname(self, value):
        return "SeasonZone"

    def fromutc(self, value):
        naive = value.replace(tzinfo=None)
        start = datetime(2026, 3, 29, 1)
        end = datetime(2026, 10, 25, 1)
        summer = start <= naive < end
        fold = int(end <= naive < end + timedelta(hours=1))
        return (naive + timedelta(hours=int(summer))).replace(tzinfo=self, fold=fold)


class StudyDaysCalculationTests(unittest.TestCase):
    def test_pauses_midnight_fractional_seconds_and_window_clipping_share_the_correct_days(self):
        midnight = local(29, 0)
        session = progressive((
            SessionEvent("start", midnight - timedelta(seconds=0.5)),
            SessionEvent("pause", midnight + timedelta(seconds=0.25)),
            SessionEvent("resume", midnight + timedelta(hours=1)),
            SessionEvent("finish", midnight + timedelta(hours=1, seconds=10)),
        ))
        totals = session_study_seconds_by_day(
            session, midnight - timedelta(seconds=0.25),
            midnight + timedelta(hours=1, seconds=9),
            midnight + timedelta(hours=1, seconds=8), LOCAL,
        )
        self.assertEqual(totals, {date(2026, 10, 28): 0.25, date(2026, 10, 29): 8.25})
        self.assertEqual(session_study_seconds_by_day(
            session, midnight - timedelta(days=2), midnight - timedelta(days=1),
            midnight + timedelta(days=1), LOCAL), {})

    def test_complete_local_days_can_have_23_or_25_hours_without_losing_time(self):
        zone = SeasonZone()
        for day, hours in ((date(2026, 3, 29), 23), (date(2026, 10, 25), 25)):
            with self.subTest(day=day):
                start = datetime.combine(day, datetime.min.time(), tzinfo=zone)
                end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone)
                self.assertEqual((end.astimezone(UTC) - start.astimezone(UTC)).total_seconds(), hours * 3600)
                session = progressive((SessionEvent("start", start), SessionEvent("finish", end)))
                self.assertEqual(session_study_seconds_by_day(session, start, end, end, zone),
                                 {day: hours * 3600})

    def test_an_unconsolidated_pomodoro_focus_stops_at_its_deadline(self):
        midnight = local(29, 0)
        session = progressive((
            SessionEvent("start", midnight - timedelta(seconds=20)),
            SessionEvent("pause", midnight - timedelta(seconds=10)),
            SessionEvent("resume", midnight + timedelta(seconds=10)),
        ))
        session = replace(session, mode="pomodoro", pomodoro=PomodoroState(
            phase="focus", state="running", focus_seconds=50, break_seconds=10,
            remaining_seconds=40, started_at=midnight + timedelta(seconds=10),
            updated_at=midnight + timedelta(seconds=10),
        ))
        self.assertEqual(session_study_seconds_by_day(
            session, midnight - timedelta(days=1), midnight + timedelta(days=1),
            midnight + timedelta(hours=12), LOCAL),
            {date(2026, 10, 28): 10, date(2026, 10, 29): 40})

    def test_invalid_windows_naive_instants_and_invalid_events_are_rejected(self):
        session = progressive((SessionEvent("start", at(0)),))
        for start, end, now in ((at(0), at(0), at(1)), (at(2), at(1), at(3)),
                                (at(0).replace(tzinfo=None), at(2), at(1)),
                                (at(0), at(2), at(1).replace(tzinfo=None))):
            with self.subTest(start=start, end=end, now=now):
                with self.assertRaises(ValueError):
                    session_study_seconds_by_day(session, start, end, now, LOCAL)
        invalid = replace(session, events=(SessionEvent("pause", at(0)),))
        with self.assertRaises(ValueError):
            session_study_seconds_by_day(invalid, at(-1), at(2), at(1), LOCAL)


class StreakStorageTests(TemporaryDatabaseTest):
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

    def goal(self, day, seconds=60):
        return storage.set_default_daily_goal(
            seconds, storage.read_default_daily_goal(date(2026, 10, day)), date(2026, 10, day),
            at=local(day), tz=LOCAL,
        )

    def override(self, day, seconds=None, *, rest=False):
        selected = date(2026, 10, day)
        return storage.set_daily_goal_override(
            selected, seconds, rest, storage.read_daily_goal_override(selected),
        )

    def manual(self, day, seconds=60, *, subject="Python", hour=8, minute=0):
        return storage.create_manual_session(
            self.subjects[subject], "Leitura", local(day, hour, minute), seconds,
            at=local(31, 23, 59),
        )

    def assert_base(self, day, current, best, *, moment=None, zone=LOCAL):
        before = self.snapshot()
        with patch.object(storage, "refresh_active_session", side_effect=AssertionError("Consolidação inesperada")):
            result = storage.read_streak_base(at=moment or local(day), tz=zone)
        self.assertEqual(result, storage.StreakBase(date(2026, 10, day), current, best))
        self.assertEqual(self.snapshot(), before)
        return result

    def test_unconfigured_days_and_rest_without_a_positive_goal_are_read_only_and_do_not_start_a_streak(self):
        self.manual(20)
        self.override(21, rest=True)
        self.override(31, 60)
        self.assert_base(30, 0, 0)
        self.assertEqual(self.execute("PRAGMA user_version"), [(7,)])
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_a_closed_missed_day_breaks_the_current_streak_but_keeps_the_record(self):
        self.goal(20)
        for day in (20, 21, 22, 24, 25, 26):
            self.manual(day)
        self.assert_base(23, 3, 3)
        self.assert_base(24, 0, 3)
        self.assert_base(26, 2, 3)

    def test_rest_and_unconfigured_days_are_neutral_and_future_days_are_ignored(self):
        self.override(5, 60)
        self.override(7, rest=True)
        self.override(8, 120)
        self.manual(5, 60)
        self.manual(8, 120)
        self.override(10, 1)
        self.manual(10, 1)
        self.assert_base(5, 0, 0)
        self.assert_base(9, 2, 2)
        self.assert_base(10, 2, 2)

    def test_today_is_excluded_even_when_its_goal_is_already_reached(self):
        self.goal(29)
        self.manual(29)
        self.override(30, 1)
        self.manual(30, 1)
        self.assert_base(29, 0, 0)
        overview = storage.read_study_overview(at=local(29), tz=LOCAL, include_streak_base=True)
        self.assertEqual(overview.total_seconds, 60)
        self.assertEqual(overview.streak_base, storage.StreakBase(date(2026, 10, 29), 0, 0))

    def test_an_ordinary_overview_does_not_rebuild_closed_history_and_the_opt_in_flag_is_validated(self):
        self.goal(20)
        self.manual(20)
        with patch.object(storage, "_read_streak_base", side_effect=AssertionError("Histórico desnecessário")):
            self.assertIsNone(storage.read_study_overview(at=local(21), tz=LOCAL).streak_base)
        with patch.object(storage, "_connection", side_effect=AssertionError("Conexão inesperada")):
            for invalid in (None, 0, 1, "sim"):
                with self.subTest(flag=invalid):
                    with self.assertRaises(ValueError):
                        storage.read_study_overview(at=local(21), tz=LOCAL, include_streak_base=invalid)

    def test_default_goal_periods_and_specific_dates_survive_gaps_with_the_app_closed(self):
        self.goal(20, 60)
        self.goal(23, 120)
        self.override(22, rest=True)
        self.override(26, 30)
        for day, seconds in ((20, 60), (21, 60), (23, 120), (24, 120), (25, 60), (26, 30)):
            self.manual(day, seconds)
        self.assert_base(27, 1, 4)
        self.assert_base(28, 0, 4)

    def test_multiple_subjects_and_fractional_intervals_reach_the_threshold_only_after_the_full_second(self):
        self.goal(28, 1)
        for subject, minute, microseconds in (("Python", 0, 500000), ("SQL", 1, 499999)):
            started = local(28, 8, minute)
            session = storage.start_session(self.subjects[subject], "Aula", at=started)
            storage.finish_session(session.id, at=started + timedelta(microseconds=microseconds))
        self.assert_base(29, 0, 0)
        started = local(28, 8, 2)
        session = storage.start_session(self.subjects["SQL"], "Revisão", at=started)
        storage.finish_session(session.id, at=started + timedelta(microseconds=1))
        self.assert_base(29, 1, 1)

    def test_decimal_intervals_and_multiple_sessions_sum_to_an_exact_goal_without_rounding_down(self):
        self.goal(28, 1)
        started = local(28, 8)
        session = storage.start_session(self.subjects["Python"], "Aula", at=started)
        for index in range(10):
            storage.pause_session(session.id, at=started + timedelta(seconds=index, milliseconds=100))
            if index < 9:
                storage.resume_session(session.id, at=started + timedelta(seconds=index + 1))
        storage.finish_session(session.id, at=started + timedelta(seconds=10))
        self.assert_base(29, 1, 1)
        for minute in range(10):
            started = local(29, 8, minute)
            session = storage.start_session(self.subjects["SQL"], "Aula", at=started)
            storage.finish_session(session.id, at=started + timedelta(milliseconds=100))
        self.assert_base(30, 2, 2)
        overview = storage.read_study_overview(at=local(29), tz=LOCAL, include_streak_base=True)
        self.assertEqual(overview.total_seconds, 1)
        self.assertEqual(overview.streak_base, storage.StreakBase(date(2026, 10, 29), 1, 1))

    def test_an_active_session_is_split_at_midnight_without_counting_pauses_or_today_twice(self):
        self.goal(27, 30)
        active = storage.start_session(self.subjects["Python"], "Aula", at=local(27, 23, 59, 30))
        storage.pause_session(active.id, at=local(28, 0, 0, 30))
        running = storage.resume_session(active.id, at=local(29, 23, 59, 30))
        moment = local(30, 0, 0, 30)
        base = self.assert_base(30, 3, 3, moment=moment)
        before = self.snapshot()
        overview = storage.read_study_overview(at=moment, tz=LOCAL, include_streak_base=True)
        self.assertEqual((overview.streak_base, overview.total_seconds, overview.active_session),
                         (base, 30, running))
        self.assertEqual(self.snapshot(), before)
        self.override(28, 60)
        self.assert_base(30, 1, 1, moment=moment)

    def test_an_overdue_pomodoro_and_its_waiting_and_break_time_do_not_create_extra_study(self):
        self.goal(28, 10)
        self.override(29, 11)
        active = storage.start_session(self.subjects["Python"], "Exercícios", mode="pomodoro",
                                       focus_seconds=20, break_seconds=60, at=local(28, 23, 59, 50))
        self.assert_base(30, 0, 1)
        self.assertEqual(storage.get_active_session(), active)
        self.assertEqual(storage.get_active_session().pomodoro.state, "running")
        storage.refresh_active_session(at=local(29, 0, 0, 10))
        storage.start_next_phase(active.id, at=local(29, 8))
        waiting = storage.refresh_active_session(at=local(29, 9))
        self.assertEqual((waiting.pomodoro.phase, waiting.pomodoro.state), ("focus", "ready"))
        self.assert_base(30, 0, 1)
        self.assertEqual(storage.get_active_session(), waiting)

    def test_edits_deletions_and_retroactive_goal_changes_recalculate_both_counts(self):
        self.goal(20)
        records = {day: self.manual(day) for day in (20, 21, 22)}
        self.assert_base(23, 3, 3)
        records[21] = storage.update_finished_session(
            records[21], self.subjects["Python"], "Leitura", duration_seconds=30, at=local(31),
        )
        self.assert_base(23, 1, 1)
        self.manual(21, 30, hour=9)
        self.assert_base(23, 3, 3)
        storage.delete_finished_session(records[22])
        self.assert_base(23, 0, 2)
        rest = self.override(22, rest=True)
        self.assert_base(23, 2, 2)
        storage.clear_daily_goal_override(rest.day, rest)
        self.assert_base(23, 0, 2)
        self.override(21, 120)
        self.assert_base(23, 0, 1)
        storage.delete_subject(storage.read_subject_deletion_preview(self.subjects["Python"]))
        self.assert_base(23, 0, 0)

    def test_the_requested_timezone_changes_daily_boundaries_without_rewriting_records(self):
        self.goal(28, 600)
        storage.create_manual_session(
            self.subjects["Python"], "Leitura", datetime(2026, 10, 28, 23, 50, tzinfo=UTC),
            1200, at=local(31),
        )
        moment = datetime(2026, 10, 30, 12, tzinfo=UTC)
        self.assert_base(30, 2, 2, moment=moment, zone=UTC)
        self.assert_base(30, 0, 1, moment=moment, zone=LOCAL)

    def test_opt_in_overview_uses_one_snapshot_for_today_and_closed_days(self):
        goal = self.goal(28)
        closed = self.manual(28)
        self.manual(29)
        self.execute("PRAGMA journal_mode = WAL")
        original_read = storage._read_default_daily_goal
        changed = False

        def mutate_during_read(connection, selected_day):
            nonlocal changed
            if not changed:
                changed = True
                with closing(sqlite3.connect(self.path)) as writer:
                    with writer:
                        writer.execute("UPDATE daily_goal_defaults SET duration_seconds = 120 WHERE id = ?", (goal.id,))
                        writer.execute("DELETE FROM session_events WHERE session_id = ?", (closed.id,))
                        writer.execute("DELETE FROM sessions WHERE id = ?", (closed.id,))
            return original_read(connection, selected_day)

        with patch.object(storage, "_read_default_daily_goal", side_effect=mutate_during_read):
            overview = storage.read_study_overview(at=local(29), tz=LOCAL, include_streak_base=True)
        self.assertTrue(changed)
        self.assertEqual((overview.goal, overview.total_seconds, overview.streak_base),
                         (goal, 60, storage.StreakBase(date(2026, 10, 29), 1, 1)))
        current = storage.read_study_overview(at=local(29), tz=LOCAL, include_streak_base=True)
        self.assertEqual((current.goal.duration_seconds, current.total_seconds, current.streak_base),
                         (120, 60, storage.StreakBase(date(2026, 10, 29), 0, 0)))
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_reading_a_long_period_batches_queries_instead_of_querying_each_day(self):
        storage.set_default_daily_goal(60, None, date(2026, 1, 1),
                                       at=datetime(2026, 1, 1, 12, tzinfo=LOCAL), tz=LOCAL)
        self.manual(28)
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
            base = storage.read_streak_base(at=local(30), tz=LOCAL)
        self.assertEqual(base, storage.StreakBase(date(2026, 10, 30), 0, 1))
        selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        self.assertLess(len(selects), 30, "A leitura não deve fazer uma consulta por dia do período.")
        self.assertEqual(self.snapshot(), before)

    def test_another_process_recovers_counts_and_preserves_ids_preferences_and_active_state(self):
        self.goal(27, 30)
        finished = self.manual(27, 30)
        self.override(28, rest=True)
        active = storage.start_session(self.subjects["Python"], "Aula", at=local(29, 23, 59, 30))
        active = storage.pause_session(active.id, at=local(30, 0, 0, 30))
        before = self.snapshot()
        script = """
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    moment = datetime.fromisoformat(sys.argv[2])
    zone = timezone(timedelta(hours=-3))
    storage.initialize_database()
    base = storage.read_streak_base(at=moment, tz=zone)
    view = storage.read_study_overview(at=moment, tz=zone, include_streak_base=True)
    print(json.dumps([base.day.isoformat(), base.current_before_today, base.best_before_today,
                      view.streak_base.current_before_today, view.total_seconds,
                      view.active_session.id, view.active_session.status,
                      [item.id for item in storage.read_session_history().sessions],
                      storage.get_subject_timer_settings(view.active_session.subject_id).mode]))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path), local(30, 1).isoformat()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(result.stdout),
                         ["2026-10-30", 2, 2, 2, 30, active.id, "paused", [finished.id], "progressive"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_active_session(), active)
