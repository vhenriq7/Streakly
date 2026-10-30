"""Edições de sessões com snapshots e bancos temporários, sem dados pessoais."""

import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import storage
from test_timer import TemporaryDatabaseTest, at
from timer import SessionEvent, local_day_window, session_study_seconds_between


class FinishedSessionEditTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        for name in ("Python", "SQL"):
            storage.add_subject(name)
        self.subjects = {name: key for key, name in storage.list_subject_options()}

    def manual(self, start=0, duration=60, *, subject="Python", now=1000, **kwargs):
        return storage.create_manual_session(
            self.subjects[subject], "Leitura", at(start), duration, at=at(now), **kwargs,
        )

    def edit(self, expected, *, subject="Python", study_type="Leitura", topic=None, now=1000, **kwargs):
        return storage.update_finished_session(
            expected, self.subjects[subject], study_type, topic, at=at(now), **kwargs,
        )

    def snapshot(self):
        return {
            **{table: self.execute(f"SELECT * FROM {table} ORDER BY 1")
               for table in ("subjects", "sessions", "session_events",
                             "subject_timer_settings", "pomodoro_state")},
            "schema": self.execute("PRAGMA user_version"),
        }

    def event_ids(self, session_id):
        return self.execute(
            "SELECT id, sequence, kind FROM session_events WHERE session_id = ? ORDER BY sequence",
            (session_id,),
        )

    def test_metadata_of_progressive_and_pomodoro_preserves_events_pauses_and_active_session(self):
        progressive = storage.start_session(self.subjects["Python"], "Aula", at=at(0))
        storage.pause_session(progressive.id, at=at(10))
        storage.resume_session(progressive.id, at=at(20))
        progressive = storage.finish_session(progressive.id, at=at(25))
        pomodoro = storage.start_session(
            self.subjects["Python"], "Exercícios", "Antigo", mode="pomodoro", at=at(100),
            focus_seconds=10, break_seconds=5, long_break_seconds=7,
            cycles_until_long_break=2, preset="custom",
        )
        storage.refresh_active_session(at=at(110))
        storage.start_next_phase(pomodoro.id, at=at(200))
        storage.refresh_active_session(at=at(205))
        storage.start_next_phase(pomodoro.id, at=at(300))
        storage.refresh_active_session(at=at(310))
        pomodoro = storage.finish_session(pomodoro.id, at=at(400))
        active = storage.start_session(self.subjects["SQL"], "Aula", at=at(500))
        paused = storage.pause_session(active.id, at=at(510))
        storage.add_subject("Python")
        other_id = next(key for key, name in storage.list_subject_options()
                        if name == "Python" and key != self.subjects["Python"])
        for saved, topic, cleaned in ((progressive, "  Funções 🚀  ", "Funções 🚀"),
                                       (pomodoro, "  ", None)):
            with self.subTest(mode=saved.mode):
                before = self.snapshot()
                updated = storage.update_finished_session(
                    saved, other_id, "  Revisão  ", topic, at=at(1000),
                )
                self.assertEqual(updated, replace(
                    saved, subject_id=other_id, subject_name="Python", study_type="Revisão", topic=cleaned,
                ))
                after = self.snapshot()
                for table in ("subjects", "session_events", "subject_timer_settings", "pomodoro_state", "schema"):
                    self.assertEqual(after[table], before[table])
                self.assertEqual(storage.get_active_session(), paused)
        self.assertEqual((progressive.duration_seconds, pomodoro.duration_seconds), (15, 20))
        self.assertEqual(self.row_counts(), (3, len(progressive.events) + len(pomodoro.events) + 2))

    def test_manual_time_changes_recalculate_midnight_totals_and_keep_session_and_event_ids(self):
        zone = timezone(timedelta(hours=-3))
        original_start = datetime(2026, 10, 28, 23, 50, tzinfo=zone)
        now = datetime(2026, 10, 29, 1, tzinfo=zone)
        saved = storage.create_manual_session(self.subjects["Python"], "Leitura", original_start, 1200, at=now)
        original_ids = self.event_ids(saved.id)
        self.assertEqual(storage.read_study_overview(at=now, tz=zone).total_seconds, 600)
        start = datetime(2026, 10, 29, 0, 5, tzinfo=zone)
        updated = storage.update_finished_session(
            saved, self.subjects["SQL"], "Revisão", "Correção", started_at=start,
            duration_seconds=1800, at=now,
        )
        self.assertEqual((updated.id, updated.source, updated.mode), (saved.id, "manual", "progressive"))
        self.assertEqual((updated.started_at, updated.ended_at, updated.duration_seconds),
                         (start.astimezone(UTC), start.astimezone(UTC) + timedelta(minutes=30), 1800))
        self.assertEqual(updated.events, (SessionEvent("start", updated.started_at),
                                          SessionEvent("finish", updated.ended_at)))
        self.assertEqual(self.event_ids(saved.id), original_ids)
        self.assertEqual(storage.read_study_overview(at=now, tz=zone).total_seconds, 1800)
        previous = local_day_window(original_start, zone)
        self.assertEqual(session_study_seconds_between(updated, *previous[1:], now), 0)
        shorter = storage.update_finished_session(
            updated, updated.subject_id, updated.study_type, updated.topic, duration_seconds=600, at=now,
        )
        self.assertEqual(shorter.started_at, updated.started_at)
        moved = storage.update_finished_session(
            shorter, shorter.subject_id, shorter.study_type, shorter.topic,
            started_at=datetime(2026, 10, 29, 0, 40, tzinfo=zone), at=now,
        )
        self.assertEqual((moved.duration_seconds, moved.ended_at),
                         (600, datetime(2026, 10, 29, 0, 50, tzinfo=zone)))
        self.assertEqual(self.event_ids(saved.id), original_ids)
        self.assertEqual(storage.read_study_overview(at=now, tz=zone).total_seconds, 600)
        self.assertEqual(self.row_counts(), (1, 2))
        self.assertEqual(self.execute("PRAGMA user_version"), [(6,)])

    def test_metadata_and_noop_edits_keep_exact_seconds_microseconds_and_all_rows(self):
        start = at(0) + timedelta(microseconds=123456)
        saved = storage.create_manual_session(self.subjects["Python"], "Leitura", start, 61, at=at(1000))
        ids = self.event_ids(saved.id)
        before = self.snapshot()
        self.assertEqual(self.edit(saved, now=-100), saved)
        self.assertEqual(self.snapshot(), before)
        same = self.edit(saved, started_at=saved.started_at, duration_seconds=61)
        self.assertEqual(same, saved)
        self.assertEqual(self.snapshot(), before)
        updated = self.edit(saved, topic="Somente assunto")
        self.assertEqual((updated.started_at, updated.ended_at, updated.duration_seconds, updated.events),
                         (start, start + timedelta(seconds=61), 61, saved.events))
        self.assertEqual(self.event_ids(saved.id), ids)
        longer = self.edit(updated, topic=updated.topic, duration_seconds=62)
        self.assertEqual((longer.started_at, longer.ended_at, longer.duration_seconds),
                         (start, start + timedelta(seconds=62), 62))
        self.assertEqual(self.event_ids(saved.id), ids)

    def test_invalid_metadata_periods_and_missing_subject_roll_back_without_partial_updates(self):
        saved = self.manual()
        before = self.snapshot()
        arguments = dict(expected=saved, subject_id=self.subjects["SQL"], study_type="Revisão", at=at(1000))
        attempts = [{"expected": value} for value in (None, True, "id")]
        attempts += [{"subject_id": value} for value in (None, "", 2, "inexistente")]
        attempts += [{"study_type": value} for value in (None, 2, "   ")]
        attempts += [{"topic": value} for value in (True, 2)]
        attempts += [{"duration_seconds": value} for value in (0, -1, True, 1.5, "2", 10**40)]
        attempts += [{"started_at": value} for value in (at(0).replace(tzinfo=None), "2026-10-28")]
        attempts += [
            {"started_at": at(1001)}, {"duration_seconds": 1001},
            {"started_at": datetime.min.replace(tzinfo=timezone(timedelta(hours=1)))},
            {"duration_seconds": 61, "at": at(1000).replace(tzinfo=None)},
            {"started_at": datetime(9999, 12, 31, 23, 59, 50, tzinfo=UTC), "duration_seconds": 60,
             "at": datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)},
        ]
        for changes in attempts:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    storage.update_finished_session(**(arguments | changes))
                self.assertEqual(self.snapshot(), before)

    def test_timer_time_arguments_are_rejected_even_when_the_values_are_unchanged(self):
        progressive = storage.start_session(self.subjects["Python"], "Aula", at=at(0))
        progressive = storage.finish_session(progressive.id, at=at(10))
        pomodoro = storage.start_session(self.subjects["SQL"], "Aula", mode="pomodoro", at=at(20))
        pomodoro = storage.finish_session(pomodoro.id, at=at(25))
        before = self.snapshot()
        for saved in (progressive, pomodoro):
            for timing in ({"started_at": saved.started_at},
                           {"duration_seconds": int(saved.duration_seconds)},
                           {"started_at": saved.started_at, "duration_seconds": int(saved.duration_seconds)}):
                with self.subTest(mode=saved.mode, timing=timing):
                    with self.assertRaises(ValueError):
                        self.edit(saved, **timing)
                    self.assertEqual(self.snapshot(), before)

    def test_active_ready_and_unknown_sessions_cannot_be_edited(self):
        active = storage.start_session(self.subjects["Python"], "Aula", at=at(0))
        for state in ("running", "paused"):
            if state == "paused":
                active = storage.pause_session(active.id, at=at(10))
            before = self.snapshot()
            with self.assertRaises(ValueError):
                self.edit(active, topic="Não deve salvar")
            self.assertEqual(self.snapshot(), before)
        finished = storage.finish_session(active.id, at=at(20))
        pomodoro = storage.start_session(
            self.subjects["SQL"], "Aula", mode="pomodoro", at=at(100), focus_seconds=10, break_seconds=5,
        )
        ready = storage.refresh_active_session(at=at(110))
        before = self.snapshot()
        for saved in (ready, replace(finished, id="sessão-inexistente")):
            with self.subTest(id=saved.id):
                with self.assertRaises(ValueError):
                    self.edit(saved)
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_active_session().id, pomodoro.id)

    def test_manual_overlap_checks_exclude_self_and_allow_boundaries_and_manual_pauses(self):
        target = self.manual(0, 10)
        self.manual(30, 10, subject="SQL")
        active = storage.start_session(self.subjects["Python"], "Aula", at=at(100))
        paused = storage.pause_session(active.id, at=at(110))
        before = self.snapshot()
        for start, duration in ((30, 10), (39, 2), (105, 10)):
            with self.subTest(start=start, duration=duration):
                with self.assertRaises(ValueError):
                    self.edit(target, subject="SQL", started_at=at(start), duration_seconds=duration)
                self.assertEqual(self.snapshot(), before)
        for start in (5, 20, 40, 120):
            target = self.edit(target, subject="SQL", started_at=at(start), duration_seconds=10)
            self.assertEqual(target.started_at, at(start))
            self.assertEqual(storage.get_active_session(), paused)
        self.assertEqual(storage.get_subject_timer_settings(active.subject_id).mode, "progressive")

    def test_expired_pomodoro_limits_overlap_to_focus_without_settling_the_active_phase(self):
        target = self.manual(0, 5)
        active = storage.start_session(
            self.subjects["SQL"], "Aula", mode="pomodoro", at=at(100), focus_seconds=10, break_seconds=5,
        )
        moved = self.edit(target, started_at=at(110), duration_seconds=10, now=200)
        self.assertEqual(storage.get_active_session(), active)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.edit(moved, started_at=at(109), duration_seconds=2, now=200)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_active_session(), active)

    def test_a_stale_snapshot_cannot_overwrite_a_newer_edit(self):
        saved = self.manual()
        updated = self.edit(saved, topic="Primeira correção")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "(?i)atualize"):
            self.edit(saved, study_type="Revisão", topic="Leitura antiga")
        self.assertEqual(storage.get_session(saved.id), updated)
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_different_edits_from_the_same_snapshot_have_only_one_winner(self):
        saved = self.manual()
        ids = self.event_ids(saved.id)
        barrier = threading.Barrier(2)

        def attempt(topic):
            barrier.wait(timeout=5)
            try:
                return self.edit(saved, topic=topic)
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ("Correção A", "Correção B")))
        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(storage.get_session(saved.id), winners[0])
        self.assertEqual(self.event_ids(saved.id), ids)
        self.assertEqual(self.row_counts(), (1, 2))

    def test_second_event_update_failure_rolls_back_metadata_period_and_all_related_rows(self):
        saved = self.manual()
        active = storage.start_session(self.subjects["SQL"], "Aula", at=at(200))
        paused = storage.pause_session(active.id, at=at(210))
        ids = self.event_ids(saved.id)
        before = self.snapshot()
        self.execute("""
            CREATE TRIGGER reject_edit_finish BEFORE UPDATE OF occurred_at ON session_events
            WHEN OLD.kind = 'finish'
            BEGIN SELECT RAISE(ABORT, 'falha simulada no segundo evento'); END
        """)
        with self.assertRaises(sqlite3.DatabaseError):
            self.edit(saved, subject="SQL", study_type="Revisão", topic="Correção",
                      started_at=at(10), duration_seconds=120)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_active_session(), paused)
        self.execute("DROP TRIGGER reject_edit_finish")
        updated = self.edit(saved, subject="SQL", study_type="Revisão", topic="Correção",
                            started_at=at(10), duration_seconds=120)
        self.assertEqual(updated.ended_at, at(130))
        self.assertEqual(self.event_ids(saved.id), ids)
        self.assertEqual(storage.get_active_session(), paused)

    def test_another_process_recovers_edits_and_daily_total_with_the_original_ids(self):
        saved = self.manual()
        active = storage.start_session(self.subjects["Python"], "Aula", at=at(300))
        storage.pause_session(active.id, at=at(310))
        updated = self.edit(saved, subject="SQL", study_type="Revisão", topic="Funções 🚀",
                            started_at=at(50), duration_seconds=120)
        ids = self.event_ids(saved.id)
        before = self.snapshot()
        script = """
import json, sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    storage.initialize_database()
    saved = storage.get_session(sys.argv[2])
    overview = storage.read_study_overview(at=datetime.fromisoformat(sys.argv[3]), tz=UTC)
    print(json.dumps([saved.id, saved.subject_id, saved.study_type, saved.topic, saved.source,
                      saved.started_at.isoformat(), saved.ended_at.isoformat(), saved.duration_seconds,
                      overview.total_seconds, overview.active_session.id]))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path), saved.id, at(1000).isoformat()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(result.stdout),
                         [updated.id, updated.subject_id, "Revisão", "Funções 🚀", "manual",
                          at(50).isoformat(), at(170).isoformat(), 120, 130, active.id])
        self.assertEqual(self.event_ids(saved.id), ids)
        self.assertEqual(self.snapshot(), before)
