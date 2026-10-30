"""Renomeação por UUID usando somente bancos temporários e horários simulados."""

import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC
from pathlib import Path
from unittest.mock import patch

import storage
from test_timer import TemporaryDatabaseTest, at


class SubjectRenameTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        for name in ("Python", "SQL"):
            storage.add_subject(name)
        self.subjects = {name: key for key, name in storage.list_subject_options()}

    def snapshot(self):
        return {
            **{table: self.execute(f"SELECT * FROM {table} ORDER BY 1")
               for table in ("subjects", "sessions", "session_events",
                             "subject_timer_settings", "pomodoro_state")},
            "schema": self.execute("PRAGMA user_version"),
        }

    def with_subject_name(self, before, subject_id, new_name):
        return before | {"subjects": [(key, new_name if key == subject_id else name)
                                      for key, name in before["subjects"]]}

    def related_sessions(self):
        subject_id = self.subjects["Python"]
        manual = storage.create_manual_session(subject_id, "Leitura", at(0), 10,
                                              "Funções 🚀", at=at(1000))
        progressive = storage.start_session(subject_id, "Aula", at=at(20))
        storage.pause_session(progressive.id, at=at(25))
        progressive = storage.finish_session(progressive.id, at=at(30))
        active = storage.start_session(subject_id, "Exercícios", mode="pomodoro", at=at(100),
                                       focus_seconds=10, break_seconds=5)
        return manual, progressive, active

    def test_unicode_trim_and_uuid_rename_preserve_all_related_rows_and_active_state(self):
        manual, progressive, active = self.related_sessions()
        subject_id = self.subjects["Python"]
        before = self.snapshot()
        settings = storage.get_subject_timer_settings(subject_id)
        self.assertIsNone(storage.rename_subject(subject_id, "Python", "  Álgebra e funções 🚀  "))
        self.assertEqual(self.snapshot(), self.with_subject_name(before, subject_id, "Álgebra e funções 🚀"))
        for saved in (manual, progressive, active):
            with self.subTest(session=saved.id):
                self.assertEqual(storage.get_session(saved.id),
                                 replace(saved, subject_name="Álgebra e funções 🚀"))
        self.assertEqual(storage.get_active_session(), replace(active, subject_name="Álgebra e funções 🚀"))
        self.assertEqual(storage.get_subject_timer_settings(subject_id), settings)
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])
        self.assertEqual(self.execute("PRAGMA user_version"), [(7,)])

    def test_options_history_and_overview_show_the_new_name_without_changing_daily_time(self):
        manual, progressive, active = self.related_sessions()
        before_overview = storage.read_study_overview(at=at(105), tz=UTC)
        self.assertEqual(before_overview.total_seconds, 20)
        storage.rename_subject(self.subjects["Python"], "Python", "Zoologia")
        self.assertEqual(storage.list_subject_options(),
                         [(self.subjects["SQL"], "SQL"), (self.subjects["Python"], "Zoologia")])
        history = storage.read_session_history()
        self.assertEqual(history.sessions, tuple(replace(saved, subject_name="Zoologia")
                                                for saved in (progressive, manual)))
        overview = storage.read_study_overview(at=at(105), tz=UTC)
        self.assertEqual(overview.total_seconds, before_overview.total_seconds)
        self.assertEqual(overview.day, before_overview.day)
        self.assertEqual(overview.subjects, tuple(storage.list_subject_options()))
        self.assertEqual(overview.active_session, replace(active, subject_name="Zoologia"))

    def test_duplicate_names_keep_distinct_ids_and_an_unchanged_name_is_a_noop(self):
        storage.add_subject("Python")
        other_id = next(key for key, name in storage.list_subject_options()
                        if name == "Python" and key != self.subjects["Python"])
        before = self.snapshot()
        storage.rename_subject(self.subjects["Python"], "Python", " SQL ")
        self.assertEqual(self.snapshot(), self.with_subject_name(before, self.subjects["Python"], "SQL"))
        self.assertEqual(dict(storage.list_subject_options())[other_id], "Python")
        sql_ids = {key for key, name in storage.list_subject_options() if name == "SQL"}
        self.assertEqual(sql_ids, {self.subjects["Python"], self.subjects["SQL"]})
        before = self.snapshot()
        self.execute("""
            CREATE TRIGGER reject_unnecessary_rename BEFORE UPDATE ON subjects
            BEGIN SELECT RAISE(ABORT, 'atualização desnecessária'); END
        """)
        storage.rename_subject(self.subjects["Python"], "SQL", "  SQL  ")
        self.assertEqual(self.snapshot(), before)

    def test_invalid_inputs_are_rejected_before_opening_the_database(self):
        parameters = dict(subject_id=self.subjects["Python"], expected_name="Python", new_name="Novo nome")
        attempts = [{field: value} for field in ("subject_id", "expected_name")
                    for value in (None, True, 2, "")]
        attempts += [{"new_name": value} for value in (None, True, 2, "", " \t\n ")]
        before = self.snapshot()
        with patch.object(storage, "_connection", side_effect=AssertionError("Conexão inesperada")) as connection:
            for arguments in attempts:
                with self.subTest(arguments=arguments):
                    with self.assertRaises(ValueError):
                        storage.rename_subject(**(parameters | arguments))
            connection.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_missing_or_stale_subject_and_inexact_expected_name_do_not_overwrite_data(self):
        before = self.snapshot()
        for subject_id, expected in (("inexistente", "Python"), (self.subjects["Python"], " Python ")):
            with self.subTest(subject_id=subject_id, expected=expected):
                with self.assertRaises(ValueError):
                    storage.rename_subject(subject_id, expected, "Outro")
                self.assertEqual(self.snapshot(), before)
        storage.rename_subject(self.subjects["Python"], "Python", "Nome recente")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "(?i)atualize"):
            storage.rename_subject(self.subjects["Python"], "Python", "Nome desatualizado")
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_different_renames_have_only_one_winner(self):
        before = self.snapshot()
        barrier = threading.Barrier(2)

        def attempt(new_name):
            barrier.wait(timeout=5)
            try:
                storage.rename_subject(self.subjects["Python"], "Python", new_name)
                return new_name
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ("Python básico", "Python avançado")))
        successful = [name for name in results if name is not None]
        self.assertEqual(len(successful), 1)
        self.assertEqual(self.snapshot(), self.with_subject_name(before, self.subjects["Python"], successful[0]))

    def test_failed_update_rolls_back_without_changing_sessions_preferences_or_subjects(self):
        self.related_sessions()
        before = self.snapshot()
        self.execute("""
            CREATE TRIGGER reject_subject_rename BEFORE UPDATE ON subjects
            BEGIN SELECT RAISE(ABORT, 'falha simulada ao renomear'); END
        """)
        with self.assertRaises(sqlite3.DatabaseError):
            storage.rename_subject(self.subjects["Python"], "Python", "Python atualizado")
        self.assertEqual(self.snapshot(), before)
        self.execute("DROP TRIGGER reject_subject_rename")
        storage.rename_subject(self.subjects["Python"], "Python", "Python atualizado")
        self.assertEqual(self.snapshot(), self.with_subject_name(before, self.subjects["Python"], "Python atualizado"))

    def test_another_process_recovers_the_name_and_preserves_history_active_session_and_preferences(self):
        manual, progressive, active = self.related_sessions()
        paused = storage.pause_session(active.id, at=at(105))
        storage.rename_subject(self.subjects["Python"], "Python", "Python — revisão 🚀")
        before = self.snapshot()
        script = """
import json, sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    storage.initialize_database()
    history = storage.read_session_history()
    overview = storage.read_study_overview(at=datetime.fromisoformat(sys.argv[2]), tz=UTC)
    print(json.dumps([[[item.id, item.subject_id, item.subject_name] for item in history.sessions],
                      overview.total_seconds, overview.active_session.id,
                      overview.active_session.subject_name, overview.active_session.status]))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path), at(200).isoformat()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(result.stdout), [
            [[progressive.id, self.subjects["Python"], "Python — revisão 🚀"],
             [manual.id, self.subjects["Python"], "Python — revisão 🚀"]],
            20, paused.id, "Python — revisão 🚀", "paused",
        ])
        self.assertEqual(self.snapshot(), before)
