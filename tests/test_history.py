"""Consulta do histórico usando somente bancos temporários e horários simulados."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import storage
from test_timer import TemporaryDatabaseTest, at


class SessionHistoryTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        storage.add_subject("Python")
        self.subject_id = storage.list_subject_options()[0][0]

    def finish_progressive(self, start=0, end=5, *, subject_id=None, topic=None):
        session = storage.start_session(
            subject_id or self.subject_id, "Leitura", topic, at=at(start),
        )
        return storage.finish_session(session.id, at=at(end))

    def snapshot(self):
        tables = ("subjects", "sessions", "session_events",
                  "subject_timer_settings", "pomodoro_state")
        return {
            **{table: self.execute(f"SELECT * FROM {table} ORDER BY 1") for table in tables},
            "schema": self.execute("PRAGMA user_version"),
        }

    def test_empty_history_has_no_next_page(self):
        page = storage.read_session_history()
        self.assertEqual(page, storage.HistoryPage((), 0, False))
        self.assertEqual(self.execute("PRAGMA user_version"), [(7,)])

    def test_only_finished_sessions_are_listed(self):
        saved = self.finish_progressive()
        active = storage.start_session(self.subject_id, "Aula", at=at(10))
        for moment, pause in ((15, False), (20, True)):
            with self.subTest(progressive_paused=pause):
                if pause:
                    storage.pause_session(active.id, at=at(moment))
                self.assertEqual(storage.read_session_history().sessions, (saved,))
        finished = storage.finish_session(active.id, at=at(30))
        pomodoro = storage.start_session(
            self.subject_id, "Exercícios", mode="pomodoro", at=at(40),
            focus_seconds=10, break_seconds=5,
        )
        for moment, state in ((45, "running"), (50, "ready")):
            with self.subTest(pomodoro_state=state):
                if state == "ready":
                    storage.refresh_active_session(at=at(moment))
                self.assertEqual(storage.read_session_history().sessions, (finished, saved))
                self.assertEqual(storage.get_active_session().id, pomodoro.id)

    def test_history_preserves_metadata_ids_and_effective_progressive_and_pomodoro_time(self):
        storage.add_subject("Python")
        other_id = next(key for key, _ in storage.list_subject_options() if key != self.subject_id)
        active = storage.start_session(other_id, "Revisão", "Laços e funções 🚀", at=at(0))
        storage.pause_session(active.id, at=at(10))
        storage.resume_session(active.id, at=at(100))
        progressive = storage.finish_session(active.id, at=at(105))

        active = storage.start_session(
            self.subject_id, "Exercícios", mode="pomodoro", at=at(200),
            focus_seconds=10, break_seconds=5, long_break_seconds=7,
            cycles_until_long_break=2, preset="custom",
        )
        storage.refresh_active_session(at=at(210))
        storage.start_next_phase(active.id, at=at(300))
        storage.refresh_active_session(at=at(1000))
        storage.start_next_phase(active.id, at=at(1100))
        storage.refresh_active_session(at=at(2000))
        storage.start_next_phase(active.id, at=at(3000))
        pomodoro = storage.finish_session(active.id, at=at(4000))

        page = storage.read_session_history()
        self.assertEqual(page.sessions, (pomodoro, progressive))
        self.assertEqual((progressive.subject_id, progressive.subject_name,
                          progressive.study_type, progressive.topic, progressive.mode,
                          progressive.started_at, progressive.ended_at, progressive.duration_seconds),
                         (other_id, "Python", "Revisão", "Laços e funções 🚀", "progressive",
                          at(0), at(105), 15))
        self.assertEqual((pomodoro.subject_id, pomodoro.topic, pomodoro.mode,
                          pomodoro.duration_seconds, pomodoro.pomodoro.completed_focuses),
                         (self.subject_id, None, "pomodoro", 20, 2))
        self.assertNotEqual(progressive.subject_id, pomodoro.subject_id)

    def test_order_uses_start_time_then_uuid_instead_of_finish_or_insertion_time(self):
        newest_start = self.finish_progressive(30, 31)
        oldest_start = self.finish_progressive(0, 100)
        tied_first = self.finish_progressive(20, 21)
        tied_second = self.finish_progressive(20, 22)
        tied = sorted((tied_first, tied_second), key=lambda session: session.id, reverse=True)
        expected = (newest_start, *tied, oldest_start)
        self.assertEqual(storage.read_session_history().sessions, expected)
        self.assertEqual(storage.read_session_history().sessions, expected)

    def test_pagination_default_size_and_boundaries_have_no_duplicates(self):
        saved = [self.finish_progressive(index * 10, index * 10 + 2) for index in range(26)]
        expected = tuple(reversed(saved))
        first = storage.read_session_history()
        self.assertEqual((first.sessions, first.offset, first.has_more), (expected[:25], 0, True))
        last = storage.read_session_history(offset=25)
        self.assertEqual((last.sessions, last.offset, last.has_more), (expected[25:], 25, False))
        self.assertEqual(storage.read_session_history(limit=100).sessions, expected)
        pages = [storage.read_session_history(limit=7, offset=offset) for offset in (0, 7, 14, 21)]
        self.assertEqual(tuple(session for page in pages for session in page.sessions), expected)
        self.assertEqual([page.has_more for page in pages], [True, True, True, False])
        for offset in (26, 100, 2**63 - 1):
            with self.subTest(offset=offset):
                self.assertEqual(storage.read_session_history(offset=offset),
                                 storage.HistoryPage((), offset, False))

    def test_invalid_pagination_is_rejected_before_opening_the_database(self):
        attempts = [{"limit": value} for value in (0, -1, 101, True, False, "2", 2.0, None, 10**40)]
        attempts += [{"offset": value} for value in (-1, True, False, "2", 2.0, None, 2**63)]
        with patch.object(storage, "_connection", side_effect=AssertionError("Leitura inesperada")) as connect:
            for arguments in attempts:
                with self.subTest(arguments=arguments):
                    with self.assertRaises(ValueError):
                        storage.read_session_history(**arguments)
            connect.assert_not_called()

    def test_reading_history_never_changes_data_or_settles_an_expired_active_focus(self):
        saved = self.finish_progressive()
        active = storage.start_session(
            self.subject_id, "Aula", mode="pomodoro", at=at(10),
            focus_seconds=1, break_seconds=1,
        )
        before = self.snapshot()
        with patch.object(storage, "refresh_active_session",
                          side_effect=AssertionError("Histórico não deve consolidar fases")):
            for _ in range(2):
                self.assertEqual(storage.read_session_history().sessions, (saved,))
                self.assertEqual(storage.get_active_session(), active)
                self.assertEqual(self.snapshot(), before)

    def test_new_process_recovers_finished_sessions_without_changing_rows(self):
        saved = self.finish_progressive(topic="Funções")
        active = storage.start_session(self.subject_id, "Aula", at=at(10))
        storage.pause_session(active.id, at=at(20))
        before = self.snapshot()
        script = """
import json, sys
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    page = storage.read_session_history()
    print(json.dumps([[session.id, session.subject_id, session.topic,
                       session.duration_seconds, session.mode]
                      for session in page.sessions]))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(result.stdout),
                         [[saved.id, self.subject_id, "Funções", 5, "progressive"]])
        self.assertEqual(self.snapshot(), before)
