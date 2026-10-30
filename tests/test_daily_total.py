"""Totais por dia com relógio simulado e bancos temporários, sem dados pessoais."""

import json
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import storage
from test_timer import TemporaryDatabaseTest, at
from timer import (
    PomodoroState, SessionEvent, StudySession, elapsed_seconds,
    local_day_window, session_study_seconds_between,
)


LOCAL = timezone(timedelta(hours=-3))


def local(day, hour=0, minute=0, second=0):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=LOCAL)


def progressive(events):
    events = tuple(events)
    finished = events[-1].kind == "finish"
    return StudySession(
        id="session", subject_id="subject", subject_name="Python",
        study_type="Leitura", topic=None,
        status="finished" if finished else (
            "paused" if events[-1].kind == "pause" else "running"
        ),
        started_at=events[0].occurred_at,
        ended_at=events[-1].occurred_at if finished else None,
        events=events, duration_seconds=elapsed_seconds(events, events[-1].occurred_at),
    )


class DailyCalculationTests(unittest.TestCase):
    def test_local_day_uses_the_requested_zone_instead_of_the_utc_date(self):
        cases = (
            (datetime(2026, 10, 29, 2, 30, tzinfo=UTC), LOCAL, date(2026, 10, 28),
             datetime(2026, 10, 28, 3, tzinfo=UTC), datetime(2026, 10, 29, 3, tzinfo=UTC)),
            (datetime(2026, 10, 28, 20, tzinfo=UTC), timezone(timedelta(hours=5, minutes=30)),
             date(2026, 10, 29), datetime(2026, 10, 28, 18, 30, tzinfo=UTC),
             datetime(2026, 10, 29, 18, 30, tzinfo=UTC)),
        )
        for moment, zone, day, start, end in cases:
            with self.subTest(zone=zone):
                self.assertEqual(local_day_window(moment, zone), (day, start, end))

    def test_half_open_midnight_boundaries_preserve_fractional_seconds(self):
        midnight = local(29)
        session = progressive((
            SessionEvent("start", midnight - timedelta(seconds=0.5)),
            SessionEvent("finish", midnight + timedelta(seconds=0.25)),
        ))
        previous = local_day_window(local(28, 12), LOCAL)
        current = local_day_window(local(29, 12), LOCAL)
        now = midnight + timedelta(seconds=1)
        self.assertEqual(session_study_seconds_between(session, *previous[1:], now), 0.5)
        self.assertEqual(session_study_seconds_between(session, *current[1:], now), 0.25)

        ended_at_midnight = progressive((
            SessionEvent("start", midnight - timedelta(seconds=30)),
            SessionEvent("finish", midnight),
        ))
        started_at_midnight = progressive((SessionEvent("start", midnight),))
        self.assertEqual(session_study_seconds_between(ended_at_midnight, *current[1:], now), 0)
        self.assertEqual(session_study_seconds_between(started_at_midnight, *previous[1:], now), 0)

    def test_window_intersection_caps_even_finished_events_at_now(self):
        session = progressive((SessionEvent("start", at(0)), SessionEvent("finish", at(100))))
        self.assertEqual(session_study_seconds_between(session, at(20), at(80), at(40)), 20)
        self.assertEqual(session_study_seconds_between(session, at(100), at(200), at(200)), 0)
        self.assertEqual(session_study_seconds_between(session, at(-100), at(-1), at(200)), 0)
        self.assertEqual(session_study_seconds_between(session, at(-100), at(200), at(-1)), 0)

    def test_day_windows_can_have_23_or_25_hours_without_losing_time(self):
        start = datetime(2026, 3, 29, tzinfo=UTC)
        for hours in (23, 25):
            with self.subTest(hours=hours):
                end = start + timedelta(hours=hours)
                session = progressive((SessionEvent("start", start), SessionEvent("finish", end)))
                self.assertEqual(session_study_seconds_between(
                    session, start, end, end + timedelta(hours=1)), hours * 3600)

    def test_pomodoro_manual_pauses_crossing_midnight_keep_the_exact_focus_deadline(self):
        midnight = local(29)
        moment = lambda seconds: midnight + timedelta(seconds=seconds)
        session = progressive((
            SessionEvent("start", moment(-20)), SessionEvent("pause", moment(-10)),
            SessionEvent("resume", moment(10)), SessionEvent("pause", moment(20)),
            SessionEvent("resume", moment(30)),
        ))
        session = replace(session, mode="pomodoro", pomodoro=PomodoroState(
            phase="focus", state="running", focus_seconds=60, break_seconds=10,
            remaining_seconds=40, started_at=moment(30), updated_at=moment(30),
        ))
        previous = local_day_window(local(28, 12), LOCAL)
        current = local_day_window(local(29, 12), LOCAL)
        self.assertEqual(session_study_seconds_between(session, *previous[1:], moment(1000)), 10)
        self.assertEqual(session_study_seconds_between(session, *current[1:], moment(1000)), 50)

    def test_invalid_windows_times_and_events_are_rejected(self):
        session = progressive((SessionEvent("start", at(0)),))
        cases = (
            (at(0), at(0), at(1)),
            (at(1), at(0), at(1)),
            (at(0).replace(tzinfo=None), at(2), at(1)),
            (at(0), at(2), at(1).replace(tzinfo=None)),
        )
        for start, end, now in cases:
            with self.subTest(start=start, end=end, now=now):
                with self.assertRaises(ValueError):
                    session_study_seconds_between(session, start, end, now)
        invalid = replace(session, events=(SessionEvent("pause", at(0)),))
        with self.assertRaises(ValueError):
            session_study_seconds_between(invalid, at(-1), at(2), at(1))


class DailyStorageTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        for name in ("Python", "SQL"):
            storage.add_subject(name)
        self.subjects = {name: key for key, name in storage.list_subject_options()}

    def overview(self, moment):
        return storage.read_study_overview(at=moment, tz=LOCAL)

    def start(self, name="Python", *, moment=None, **kwargs):
        return storage.start_session(self.subjects[name], "Leitura",
                                     at=at(0) if moment is None else moment, **kwargs)

    def snapshot(self):
        return {table: self.execute(f"SELECT * FROM {table} ORDER BY 1")
                for table in ("subjects", "sessions", "session_events",
                              "subject_timer_settings", "pomodoro_state")}

    def test_empty_day_has_zero_total_and_keeps_the_existing_schema(self):
        overview = self.overview(local(29, 10))
        self.assertEqual(overview.day, date(2026, 10, 29))
        self.assertEqual(overview.total_seconds, 0)
        self.assertIsNone(overview.active_session)
        self.assertEqual(overview.subjects, tuple(storage.list_subject_options()))
        self.assertEqual(self.execute("PRAGMA user_version"), [(6,)])
        self.assertEqual(self.row_counts(), (0, 0))

    def test_finished_and_active_subjects_are_summed_once_and_pauses_do_not_count(self):
        first = self.start()
        storage.finish_session(first.id, at=at(60))
        second = self.start("SQL", moment=at(120))
        storage.finish_session(second.id, at=at(240))
        active = self.start(moment=at(300))
        overview = self.overview(at(330))
        self.assertEqual(overview.total_seconds, 210)
        self.assertEqual(overview.active_session.id, active.id)

        paused = storage.pause_session(active.id, at=at(360))
        before = self.snapshot()
        for moment in (at(600), at(1200)):
            with self.subTest(moment=moment):
                overview = self.overview(moment)
                self.assertEqual(overview.total_seconds, 240)
                self.assertEqual(overview.active_session, paused)
                self.assertEqual(self.snapshot(), before)

        storage.resume_session(active.id, at=at(1200))
        self.assertEqual(self.overview(at(1230)).total_seconds, 270)
        storage.finish_session(active.id, at=at(1260))
        self.assertEqual(self.overview(at(1500)).total_seconds, 300)
        self.assertEqual(self.overview(at(24 * 3600)).total_seconds, 0)

    def test_midnight_resets_the_display_without_resetting_the_session_or_history(self):
        start = local(28, 23, 59)
        active = self.start(moment=start)
        self.assertEqual(self.overview(local(28, 23, 59, 30)).total_seconds, 30)
        midnight = self.overview(local(29))
        self.assertEqual(midnight.total_seconds, 0)
        self.assertEqual(midnight.active_session.id, active.id)
        self.assertEqual(self.overview(local(29, 0, 1)).total_seconds, 60)
        finished = storage.finish_session(active.id, at=local(29, 0, 1))
        self.assertEqual(finished.duration_seconds, 120)
        before = self.snapshot()
        self.assertEqual(self.overview(local(29, 1)).total_seconds, 60)
        previous = local_day_window(local(28, 12), LOCAL)
        self.assertEqual(session_study_seconds_between(
            finished, *previous[1:], local(29, 1)), 60)
        self.assertEqual(self.snapshot(), before)

    def test_a_manual_pause_across_midnight_does_not_carry_yesterday_into_today(self):
        active = self.start(moment=local(28, 23, 58))
        paused = storage.pause_session(active.id, at=local(28, 23, 59))
        overview = self.overview(local(29, 0, 10))
        self.assertEqual(overview.total_seconds, 0)
        self.assertEqual(overview.active_session, paused)
        storage.resume_session(active.id, at=local(29, 0, 20))
        self.assertEqual(self.overview(local(29, 0, 21)).total_seconds, 60)
        finished = storage.finish_session(active.id, at=local(29, 0, 22))
        self.assertEqual((finished.duration_seconds, self.overview(local(29, 1)).total_seconds),
                         (180, 120))

    def test_expired_pomodoro_short_long_and_waiting_periods_only_add_focus_time(self):
        start = local(28, 23, 59, 50)
        moment = lambda seconds: start + timedelta(seconds=seconds)
        active = self.start(moment=start, mode="pomodoro", focus_seconds=20,
                            break_seconds=5, long_break_seconds=7,
                            cycles_until_long_break=2, preset="custom")
        overview = self.overview(moment(100))
        self.assertEqual(overview.total_seconds, 10)
        self.assertEqual((overview.active_session.pomodoro.phase,
                          overview.active_session.pomodoro.state), ("break", "ready"))
        self.assertEqual(overview.active_session.duration_seconds, 20)
        storage.start_next_phase(active.id, at=moment(100))
        self.assertEqual(self.overview(moment(500)).total_seconds, 10)
        storage.start_next_phase(active.id, at=moment(500))
        overview = self.overview(moment(1000))
        self.assertEqual(overview.total_seconds, 30)
        self.assertTrue(overview.active_session.pomodoro.is_long_break)
        self.assertEqual(overview.active_session.pomodoro.completed_focuses, 2)
        storage.start_next_phase(active.id, at=moment(1000))
        self.assertEqual(self.overview(moment(1003)).total_seconds, 30)
        storage.pause_session(active.id, at=moment(1003))
        self.assertEqual(self.overview(moment(2000)).total_seconds, 30)
        storage.resume_session(active.id, at=moment(2000))
        self.assertEqual(self.overview(moment(2004)).total_seconds, 30)
        finished = storage.finish_session(active.id, at=moment(3000))
        self.assertEqual((finished.duration_seconds, self.overview(moment(4000)).total_seconds),
                         (40, 30))

    def test_another_process_recovers_running_and_paused_daily_totals(self):
        active = self.start(moment=local(28, 23, 59))
        script = """
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    storage.initialize_database()
    overview = storage.read_study_overview(
        at=datetime.fromisoformat(sys.argv[2]), tz=timezone(timedelta(hours=-3)))
    print(json.dumps([overview.day.isoformat(), overview.total_seconds,
                      overview.active_session.id, overview.active_session.status]))
"""
        def reopen(moment):
            result = subprocess.run(
                [sys.executable, "-B", "-c", script, str(self.path), moment.isoformat()],
                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                text=True, check=True, timeout=20,
            )
            return json.loads(result.stdout)

        self.assertEqual(reopen(local(29, 0, 2)), ["2026-10-29", 120, active.id, "running"])
        storage.pause_session(active.id, at=local(29, 0, 2))
        before = self.snapshot()
        self.assertEqual(reopen(local(29, 0, 10)), ["2026-10-29", 120, active.id, "paused"])
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
