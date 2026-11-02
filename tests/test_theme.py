"""Preferência visual e migração com SQLite temporário, sem dados pessoais."""

import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date
from pathlib import Path
from unittest.mock import patch

import storage
from storage import ReminderSettings
from test_timer import TemporaryDatabaseTest, at


STUDY_TABLES = (
    "subjects", "sessions", "session_events", "subject_timer_settings",
    "pomodoro_state", "daily_goal_defaults", "daily_goal_overrides",
)


class ThemeTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        storage.initialize_database()
        storage.add_subject("Python")
        self.subject_id = storage.list_subject_options()[0][0]

    def snapshot(self, *, settings=True):
        tables = STUDY_TABLES + (("app_settings",) if settings else ())
        return {table: self.execute(f"SELECT * FROM {table} ORDER BY 1") for table in tables}

    def seed_study_data(self):
        storage.create_manual_session(self.subject_id, "Leitura", at(0), 10,
                                      "Funções 🚀", at=at(1000))
        active = storage.start_session(self.subject_id, "Aula", mode="pomodoro",
                                       focus_seconds=10, break_seconds=5, at=at(100))
        storage.pause_session(active.id, at=at(105))
        storage.set_default_daily_goal(3600, None, date(2026, 10, 28), at=at(0), tz=UTC)
        storage.set_daily_goal_override(date(2026, 10, 29), None, True, None)

    def reject_writes(self):
        for operation in ("INSERT", "UPDATE", "DELETE"):
            self.execute(f"""
                CREATE TRIGGER reject_theme_{operation.lower()} BEFORE {operation} ON app_settings
                BEGIN SELECT RAISE(ABORT, 'gravação desnecessária'); END
            """)

    def set_database_back_to_v7(self):
        self.remove_sync_outbox()
        self.execute("DROP TABLE app_settings")
        self.execute("PRAGMA user_version = 7")

    def test_reminder_defaults_are_read_only_and_preferences_round_trip(self):
        before = self.snapshot()
        default = ReminderSettings()
        self.assertEqual(storage.read_reminder_settings(), default)
        self.assertEqual(self.snapshot(), before)
        selected = ReminderSettings(True, "07:45", True, "21:10")
        self.assertEqual(storage.set_reminder_settings(selected, default), selected)
        self.assertEqual(storage.read_reminder_settings(), selected)
        self.assertEqual(storage.read_theme_mode(), "system")
        self.assertEqual(self.snapshot(settings=False),
                         {table: before[table] for table in STUDY_TABLES})

    def test_reminder_stale_expected_values_and_invalid_times_are_rejected(self):
        default = ReminderSettings()
        selected = ReminderSettings(True, "07:45", False, "20:00")
        storage.set_reminder_settings(selected, default)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            storage.set_reminder_settings(ReminderSettings(False, "18:00", False, "20:00"), default)
        invalid = (
            ReminderSettings(True, "24:00", False, "20:00"),
            ReminderSettings(True, "12:60", False, "20:00"),
            ReminderSettings(True, "7:00", False, "20:00"),
            ReminderSettings(1, "07:45", False, "20:00"),
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                storage.set_reminder_settings(value, selected)
        self.assertEqual(self.snapshot(), before)

    def test_system_default_is_read_only_and_all_modes_preserve_studies(self):
        self.seed_study_data()
        before = self.snapshot()
        self.assertEqual(storage.read_theme_mode(), "system")
        self.assertEqual(storage.read_theme_mode(), "system")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(before["app_settings"], [])
        self.execute("INSERT INTO app_settings VALUES ('other_preference', 'preservada')")
        current = "system"
        for mode in ("light", "dark", "system"):
            with self.subTest(mode=mode):
                self.assertEqual(storage.set_theme_mode(mode, current), mode)
                self.assertEqual(storage.read_theme_mode(), mode)
                self.assertEqual(self.snapshot(settings=False),
                                 {table: before[table] for table in STUDY_TABLES})
                self.assertEqual(self.execute("SELECT value FROM app_settings WHERE key = ?",
                                              ("other_preference",)), [("preservada",)])
                current = mode
        self.assertEqual(self.execute("PRAGMA user_version"), [(storage.SCHEMA_VERSION,)])

    def test_invalid_input_is_rejected_before_connection_and_sql_constraint_protects_theme(self):
        invalid = (None, True, False, 1, 0, "", "Dark", " dark ", "automatic", [], object())
        before = self.snapshot()
        with patch.object(storage, "_connection", side_effect=AssertionError("Conexão inesperada")) as connection:
            for value in invalid:
                for arguments in ((value, "system"), ("dark", value)):
                    with self.subTest(arguments=arguments):
                        with self.assertRaises(ValueError):
                            storage.set_theme_mode(*arguments)
            connection.assert_not_called()
        for value in ("Dark", "unknown", "", None):
            with self.subTest(sql_value=value):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.execute("INSERT INTO app_settings (key, value) VALUES ('theme_mode', ?)", (value,))
        self.assertEqual(self.snapshot(), before)

    def test_unchanged_modes_do_not_insert_update_or_delete(self):
        self.reject_writes()
        before = self.snapshot()
        self.assertEqual(storage.set_theme_mode("system", "system"), "system")
        self.assertEqual(self.snapshot(), before)
        for operation in ("insert", "update", "delete"):
            self.execute(f"DROP TRIGGER reject_theme_{operation}")
        storage.set_theme_mode("dark", "system")
        self.reject_writes()
        before = self.snapshot()
        self.assertEqual(storage.set_theme_mode("dark", "dark"), "dark")
        with self.assertRaises(ValueError):
            storage.set_theme_mode("dark", "system")
        self.assertEqual(self.snapshot(), before)

    def test_stale_expected_mode_and_concurrent_changes_do_not_overwrite_newer_choice(self):
        storage.set_theme_mode("light", "system")
        before = self.snapshot()
        for mode in ("dark", "light"):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    storage.set_theme_mode(mode, "system")
        self.assertEqual(self.snapshot(), before)
        storage.set_theme_mode("system", "light")
        barrier = threading.Barrier(2)

        def save(mode):
            barrier.wait(timeout=10)
            try:
                return storage.set_theme_mode(mode, "system")
            except ValueError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, ("light", "dark")))
        self.assertEqual(results.count("conflict"), 1)
        winner = next(result for result in results if result != "conflict")
        self.assertEqual(storage.read_theme_mode(), winner)
        self.assertEqual(self.execute("SELECT key, value FROM app_settings"), [("theme_mode", winner)])
        self.assertEqual(self.snapshot(settings=False),
                         {table: before[table] for table in STUDY_TABLES})

    def test_failed_insert_or_update_rolls_back_and_can_be_retried(self):
        self.seed_study_data()
        for operation, mode, expected in (("INSERT", "dark", "system"), ("UPDATE", "light", "dark")):
            with self.subTest(operation=operation):
                self.execute(f"""
                    CREATE TRIGGER reject_theme BEFORE {operation} ON app_settings
                    BEGIN SELECT RAISE(ABORT, 'falha simulada'); END
                """)
                before = self.snapshot()
                with self.assertRaises(sqlite3.DatabaseError):
                    storage.set_theme_mode(mode, expected)
                self.assertEqual(storage.read_theme_mode(), expected)
                self.assertEqual(self.snapshot(), before)
                self.execute("DROP TRIGGER reject_theme")
                self.assertEqual(storage.set_theme_mode(mode, expected), mode)
                self.assertEqual(storage.read_theme_mode(), mode)

    def test_v7_migration_preserves_all_study_tables_and_is_repeatable(self):
        self.seed_study_data()
        self.set_database_back_to_v7()
        before = self.snapshot(settings=False)
        self.assertTrue(all(before[table] for table in STUDY_TABLES))
        storage.initialize_database()
        storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(storage.SCHEMA_VERSION,)])
        self.assertEqual(self.snapshot(settings=False), before)
        self.assertEqual(self.execute("SELECT * FROM app_settings"), [])
        self.assertEqual(storage.read_theme_mode(), "system")
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])
        self.assertEqual(storage.set_theme_mode("light", "system"), "light")

    def test_failed_v7_migration_rolls_back_table_and_version_before_retry(self):
        self.seed_study_data()
        self.set_database_back_to_v7()
        before = self.snapshot(settings=False)
        original_connection = storage._connection

        @contextmanager
        def reject_version(*, write=False):
            with original_connection(write=write) as connection:
                def authorize(action, first, second, database, source):
                    if action == sqlite3.SQLITE_PRAGMA and first == "user_version" and second == "8":
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                connection.set_authorizer(authorize)
                yield connection

        with patch.object(storage, "_connection", side_effect=reject_version):
            with self.assertRaises(sqlite3.DatabaseError):
                storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(7,)])
        self.assertEqual(self.execute("SELECT name FROM sqlite_master WHERE name = 'app_settings'"), [])
        self.assertEqual(self.snapshot(settings=False), before)
        storage.initialize_database()
        self.assertEqual(self.execute("PRAGMA user_version"), [(storage.SCHEMA_VERSION,)])
        self.assertEqual(storage.read_theme_mode(), "system")
        self.assertEqual(self.snapshot(settings=False), before)

    def test_new_process_recovers_each_mode_without_modifying_studies(self):
        self.seed_study_data()
        script = """
import json, sys
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    storage.initialize_database()
    print(json.dumps([storage.read_theme_mode(), storage.SCHEMA_VERSION]))
"""
        current = "system"
        for mode in ("light", "dark", "system"):
            with self.subTest(mode=mode):
                storage.set_theme_mode(mode, current)
                before = self.snapshot()
                result = subprocess.run(
                    [sys.executable, "-B", "-c", script, str(self.path)],
                    cwd=Path(__file__).resolve().parents[1], capture_output=True,
                    text=True, check=True, timeout=20,
                )
                self.assertEqual(json.loads(result.stdout), [mode, storage.SCHEMA_VERSION])
                self.assertEqual(self.snapshot(), before)
                current = mode
