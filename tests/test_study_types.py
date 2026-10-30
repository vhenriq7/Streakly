"""Tipos personalizados com validação da entrada e leitura de SQLite temporário."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import main
import storage
from test_timer import TemporaryDatabaseTest, at


class StudyTypeParsingTests(unittest.TestCase):
    def test_existing_names_are_preserved_and_do_not_collide_with_the_custom_option(self):
        names = ("Leitura", "custom", "type:custom", "Personalizado…", " Tipo legado ")
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(main.resolve_study_type(f"type:{name}", None), name)
                self.assertEqual(main.resolve_study_type(f"type:{name}", "Outro nome"), name)

    def test_new_custom_names_trim_only_the_edges_and_preserve_unicode_and_case(self):
        self.assertEqual(main.CUSTOM_STUDY_TYPE_KEY, "custom")
        for name in ("Projeto prático 🚀", "custom", "type:custom", "Revisão  SQL"):
            with self.subTest(name=name):
                self.assertEqual(main.resolve_study_type(main.CUSTOM_STUDY_TYPE_KEY, f"  {name}\t"), name)

    def test_missing_selection_or_an_empty_custom_name_is_rejected(self):
        for choice in (None, "", "Leitura", "outro", "type:", "type: \t"):
            with self.subTest(choice=choice):
                with self.assertRaises(ValueError):
                    main.resolve_study_type(choice, "Nome válido")
        for name in (None, "", " \t\n "):
            with self.subTest(custom_name=name):
                with self.assertRaises(ValueError):
                    main.resolve_study_type(main.CUSTOM_STUDY_TYPE_KEY, name)


class RecordedStudyTypeTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        storage.add_subject("Python")
        self.subject_id = storage.list_subject_options()[0][0]

    def snapshot(self):
        return {
            **{table: self.execute(f"SELECT * FROM {table} ORDER BY 1")
               for table in ("subjects", "sessions", "session_events",
                             "subject_timer_settings", "pomodoro_state")},
            "schema": self.execute("PRAGMA user_version"),
        }

    def manual(self, name, start):
        return storage.create_manual_session(self.subject_id, name, at(start), 10, at=at(1000))

    def assert_types_read_only(self, expected):
        before = self.snapshot()
        with patch.object(storage, "refresh_active_session", side_effect=AssertionError("Consolidação inesperada")):
            self.assertEqual(storage.list_recorded_study_types(), expected)
        self.assertEqual(self.snapshot(), before)

    def test_empty_recorded_types_do_not_create_defaults_or_change_the_schema(self):
        self.assert_types_read_only([])
        self.assertEqual(self.execute("PRAGMA user_version"), [(6,)])
        self.assertEqual(self.row_counts(), (0, 0))

    def test_all_session_sources_reuse_exact_distinct_names_in_a_stable_order(self):
        for index, name in enumerate(("custom", "Leitura", "leitura", "Projeto prático 🚀",
                                      "Revisão", "type:custom", "Álgebra", "Revisão")):
            self.manual(name, index * 20)
        legacy = self.manual("Legado", 160)
        self.execute("UPDATE sessions SET study_type = ? WHERE id = ?", ("  Legado  ", legacy.id))
        progressive = storage.start_session(self.subject_id, "custom", at=at(200))
        storage.finish_session(progressive.id, at=at(205))
        pomodoro = storage.start_session(self.subject_id, "Projeto prático 🚀", mode="pomodoro",
                                         focus_seconds=10, break_seconds=5, at=at(300))
        self.assert_types_read_only([
            "  Legado  ", "custom", "Leitura", "leitura", "Projeto prático 🚀",
            "Revisão", "type:custom", "Álgebra",
        ])
        self.assertEqual(storage.get_active_session(), pomodoro)
        self.assertEqual(storage.get_session(legacy.id).study_type, "  Legado  ")

    def test_edits_and_deleting_the_last_use_update_recorded_suggestions(self):
        first = self.manual("Projeto prático", 0)
        second = self.manual("Projeto prático", 20)
        self.assert_types_read_only(["Projeto prático"])
        first = storage.update_finished_session(first, self.subject_id, "Revisão dirigida", at=at(1000))
        self.assert_types_read_only(["Projeto prático", "Revisão dirigida"])
        storage.delete_finished_session(second)
        self.assert_types_read_only(["Revisão dirigida"])
        storage.delete_subject(storage.read_subject_deletion_preview(self.subject_id))
        self.assert_types_read_only([])
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_reading_an_overdue_focus_includes_its_type_without_consolidating_it(self):
        active = storage.start_session(self.subject_id, "Laboratório", mode="pomodoro",
                                       focus_seconds=10, break_seconds=5, at=at(0))
        self.assert_types_read_only(["Laboratório"])
        self.assertEqual(storage.get_active_session(), active)
        self.assertEqual(storage.get_active_session().pomodoro.state, "running")
        self.assertEqual(self.row_counts(), (1, 1))

    def test_another_process_reuses_names_without_changing_ids_events_or_preferences(self):
        manual = self.manual("Projeto prático 🚀", 0)
        active = storage.start_session(self.subject_id, "custom", mode="pomodoro",
                                       focus_seconds=10, break_seconds=5, at=at(100))
        active = storage.pause_session(active.id, at=at(105))
        before = self.snapshot()
        script = """
import json, sys
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    storage.initialize_database()
    types = storage.list_recorded_study_types()
    active = storage.get_active_session()
    history = storage.read_session_history()
    print(json.dumps([types, active.id, active.study_type, active.status,
                      storage.get_subject_timer_settings(active.subject_id).preset,
                      [item.id for item in history.sessions]]))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(result.stdout), [
            ["custom", "Projeto prático 🚀"], active.id, "custom", "paused", "custom", [manual.id],
        ])
        self.assertEqual(self.snapshot(), before)
