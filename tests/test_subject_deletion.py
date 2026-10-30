"""Exclusão integral de matérias usando somente SQLite temporário e horas simuladas."""

import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import storage
from test_timer import TemporaryDatabaseTest, at


class SubjectDeletionTests(TemporaryDatabaseTest):
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

    def without_subject(self, before, subject_id):
        session_ids = {row[0] for row in before["sessions"] if row[1] == subject_id}
        return before | {
            "subjects": [row for row in before["subjects"] if row[0] != subject_id],
            "sessions": [row for row in before["sessions"] if row[0] not in session_ids],
            "session_events": [row for row in before["session_events"] if row[1] not in session_ids],
            "pomodoro_state": [row for row in before["pomodoro_state"] if row[0] not in session_ids],
            "subject_timer_settings": [row for row in before["subject_timer_settings"] if row[0] != subject_id],
        }

    def related_sessions(self):
        subject_id = self.subjects["Python"]
        manual = storage.create_manual_session(subject_id, "Leitura", at(0), 10,
                                              "Funções 🚀", at=at(1000))
        progressive = storage.start_session(subject_id, "Aula", at=at(20))
        storage.pause_session(progressive.id, at=at(25))
        storage.resume_session(progressive.id, at=at(35))
        progressive = storage.finish_session(progressive.id, at=at(45))
        pomodoro = storage.start_session(subject_id, "Exercícios", mode="pomodoro", at=at(100),
                                         focus_seconds=10, break_seconds=5)
        storage.refresh_active_session(at=at(110))
        pomodoro = storage.finish_session(pomodoro.id, at=at(120))
        return manual, progressive, pomodoro

    def test_preview_is_read_only_and_summarizes_all_sources_by_uuid(self):
        records = self.related_sessions()
        storage.add_subject("Python")
        duplicate_id = next(key for key, name in storage.list_subject_options()
                            if name == "Python" and key != self.subjects["Python"])
        storage.create_manual_session(duplicate_id, "Revisão", at(200), 40, at=at(1000))
        active = storage.start_session(self.subjects["SQL"], "Aula", at=at(300))
        active = storage.pause_session(active.id, at=at(310))
        before = self.snapshot()
        with patch.object(storage, "refresh_active_session", side_effect=AssertionError("Consolidação inesperada")):
            preview = storage.read_subject_deletion_preview(self.subjects["Python"])
        self.assertEqual((preview.id, preview.name), (self.subjects["Python"], "Python"))
        self.assertEqual(preview.sessions, tuple(sorted(records, key=lambda item: item.id)))
        self.assertEqual(preview.total_seconds, 35)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_active_session(), active)

    def test_delete_removes_the_target_and_every_dependency_while_preserving_other_data(self):
        records = self.related_sessions()
        storage.add_subject("Python")
        duplicate_id = next(key for key, name in storage.list_subject_options()
                            if name == "Python" and key != self.subjects["Python"])
        retained = storage.create_manual_session(duplicate_id, "Revisão", at(200), 20, at=at(1000))
        active = storage.start_session(self.subjects["SQL"], "Aula", at=at(300))
        active = storage.pause_session(active.id, at=at(310))
        preview = storage.read_subject_deletion_preview(self.subjects["Python"])
        before = self.snapshot()
        self.assertIsNone(storage.delete_subject(preview))
        self.assertEqual(self.snapshot(), self.without_subject(before, preview.id))
        self.assertEqual(storage.read_session_history().sessions, (retained,))
        self.assertEqual(storage.get_active_session(), active)
        self.assertEqual(dict(storage.list_subject_options())[duplicate_id], "Python")
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])
        self.assertEqual(self.execute("PRAGMA user_version"), [(7,)])
        for record in records:
            with self.subTest(session=record.id):
                with self.assertRaises(ValueError):
                    storage.get_session(record.id)
        with self.assertRaises(ValueError):
            storage.get_subject_timer_settings(preview.id)
        with self.assertRaises(ValueError):
            storage.rename_subject(preview.id, preview.name, "Nome novo")

    def test_empty_subject_and_subject_with_only_preferences_can_be_deleted(self):
        subject_id = self.subjects["Python"]
        active = storage.start_session(subject_id, "Leitura", at=at(0))
        finished = storage.finish_session(active.id, at=at(10))
        storage.delete_finished_session(finished)
        self.assertEqual(len(self.execute("SELECT * FROM subject_timer_settings")), 1)
        for subject_id in self.subjects.values():
            with self.subTest(subject=subject_id):
                preview = storage.read_subject_deletion_preview(subject_id)
                self.assertEqual(preview.sessions, ())
                self.assertEqual(preview.total_seconds, 0)
                before = self.snapshot()
                storage.delete_subject(preview)
                self.assertEqual(self.snapshot(), self.without_subject(before, subject_id))
        self.assertEqual(storage.list_subject_options(), [])
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_running_paused_overdue_and_ready_sessions_block_preview_and_deletion_without_writes(self):
        subject_id = self.subjects["Python"]
        empty_preview = storage.read_subject_deletion_preview(subject_id)

        def blocked():
            before = self.snapshot()
            with patch.object(storage, "refresh_active_session", side_effect=AssertionError("Consolidação inesperada")):
                with self.assertRaises(ValueError):
                    storage.read_subject_deletion_preview(subject_id)
                with self.assertRaises(ValueError):
                    storage.delete_subject(empty_preview)
            self.assertEqual(self.snapshot(), before)

        active = storage.start_session(subject_id, "Aula", at=at(0))
        blocked()
        storage.pause_session(active.id, at=at(10))
        blocked()
        storage.finish_session(active.id, at=at(20))
        active = storage.start_session(subject_id, "Aula", mode="pomodoro", at=at(100),
                                       focus_seconds=10, break_seconds=5)
        blocked()  # O prazo já passou no relógio real, mas a confirmação não o consolida.
        storage.pause_session(active.id, at=at(105))
        blocked()
        storage.resume_session(active.id, at=at(110))
        ready = storage.refresh_active_session(at=at(115))
        self.assertEqual(ready.pomodoro.state, "ready")
        blocked()
        self.assertEqual(storage.get_active_session(), ready)

    def test_invalid_missing_and_repeated_requests_preserve_remaining_data(self):
        preview = storage.read_subject_deletion_preview(self.subjects["Python"])
        before = self.snapshot()
        with patch.object(storage, "_connection", side_effect=AssertionError("Conexão inesperada")) as connection:
            for invalid in (None, True, 2, ""):
                with self.subTest(subject=invalid):
                    with self.assertRaises(ValueError):
                        storage.read_subject_deletion_preview(invalid)
            for invalid in (None, True, 2, "id", replace(preview, id=""), replace(preview, id=2)):
                with self.subTest(expected=invalid):
                    with self.assertRaises(ValueError):
                        storage.delete_subject(invalid)
            connection.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(ValueError):
            storage.read_subject_deletion_preview("inexistente")
        with self.assertRaises(ValueError):
            storage.delete_subject(replace(preview, id="inexistente"))
        self.assertEqual(self.snapshot(), before)
        storage.delete_subject(preview)
        after = self.snapshot()
        with self.assertRaises(ValueError):
            storage.delete_subject(preview)
        self.assertEqual(self.snapshot(), after)

    def test_confirmation_becomes_stale_after_name_content_membership_or_period_changes(self):
        subject_id = self.subjects["Python"]
        saved = storage.create_manual_session(subject_id, "Leitura", at(0), 10, at=at(1000))

        def stale_after(change):
            preview = storage.read_subject_deletion_preview(subject_id)
            change()
            before = self.snapshot()
            with self.assertRaises(ValueError):
                storage.delete_subject(preview)
            self.assertEqual(self.snapshot(), before)

        stale_after(lambda: storage.rename_subject(subject_id, "Python", "Python atualizado"))
        stale_after(lambda: storage.update_finished_session(
            storage.get_session(saved.id), subject_id, "Revisão", "Novo assunto", at=at(1000)))
        stale_after(lambda: storage.update_finished_session(
            storage.get_session(saved.id), subject_id, "Revisão", "Novo assunto",
            started_at=at(20), duration_seconds=15, at=at(1000)))
        stale_after(lambda: storage.create_manual_session(subject_id, "Leitura", at(50), 10, at=at(1000)))
        stale_after(lambda: storage.delete_finished_session(storage.get_session(saved.id)))
        retained = storage.read_subject_deletion_preview(subject_id).sessions[0]
        stale_after(lambda: storage.update_finished_session(
            retained, self.subjects["SQL"], retained.study_type, retained.topic, at=at(1000)))
        self.assertEqual(storage.get_session(retained.id).subject_id, self.subjects["SQL"])

    def test_failure_at_each_delete_stage_rolls_back_the_entire_subject_and_related_rows(self):
        self.related_sessions()
        preview = storage.read_subject_deletion_preview(self.subjects["Python"])
        for table in ("pomodoro_state", "session_events", "sessions", "subject_timer_settings", "subjects"):
            with self.subTest(table=table):
                before = self.snapshot()
                condition = "WHEN OLD.sequence = 1" if table == "session_events" else ""
                self.execute(f"""
                    CREATE TRIGGER reject_subject_delete BEFORE DELETE ON {table} {condition}
                    BEGIN SELECT RAISE(ABORT, 'falha simulada ao excluir matéria'); END
                """)
                try:
                    with self.assertRaises(sqlite3.DatabaseError):
                        storage.delete_subject(preview)
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])
                finally:
                    self.execute("DROP TRIGGER reject_subject_delete")
        before = self.snapshot()
        storage.delete_subject(preview)
        self.assertEqual(self.snapshot(), self.without_subject(before, preview.id))

    def test_two_concurrent_confirmations_delete_the_subject_only_once(self):
        self.related_sessions()
        preview = storage.read_subject_deletion_preview(self.subjects["Python"])
        before = self.snapshot()
        barrier = threading.Barrier(2)

        def attempt(_):
            barrier.wait(timeout=5)
            try:
                storage.delete_subject(preview)
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, range(2)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.snapshot(), self.without_subject(before, preview.id))
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_concurrent_start_and_delete_cannot_leave_an_active_session_without_its_subject(self):
        subject_id = self.subjects["Python"]
        preview = storage.read_subject_deletion_preview(subject_id)
        barrier = threading.Barrier(2)

        def attempt(action):
            barrier.wait(timeout=5)
            try:
                if action == "start":
                    storage.start_session(subject_id, "Leitura", at=at(0))
                else:
                    storage.delete_subject(preview)
                return action
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ("start", "delete")))
        successful = [action for action in results if action]
        self.assertEqual(len(successful), 1)
        if successful == ["start"]:
            self.assertEqual(storage.get_active_session().subject_id, subject_id)
            self.assertIn(subject_id, dict(storage.list_subject_options()))
        else:
            self.assertIsNone(storage.get_active_session())
            self.assertNotIn(subject_id, dict(storage.list_subject_options()))
            self.assertEqual(self.row_counts(), (0, 0))
        self.assertEqual(self.execute("PRAGMA foreign_key_check"), [])

    def test_midnight_totals_are_recalculated_without_erasing_other_subjects(self):
        zone = timezone(timedelta(hours=-3))
        now = datetime(2026, 10, 29, 1, tzinfo=zone)
        storage.create_manual_session(
            self.subjects["Python"], "Leitura", datetime(2026, 10, 28, 23, 50, tzinfo=zone),
            20 * 60, at=now,
        )
        retained = storage.create_manual_session(
            self.subjects["SQL"], "Revisão", datetime(2026, 10, 29, 0, 30, tzinfo=zone),
            5 * 60, at=now,
        )
        yesterday = datetime(2026, 10, 28, 23, 59, tzinfo=zone)
        self.assertEqual(storage.read_study_overview(at=yesterday, tz=zone).total_seconds, 540)
        self.assertEqual(storage.read_study_overview(at=now, tz=zone).total_seconds, 900)
        storage.delete_subject(storage.read_subject_deletion_preview(self.subjects["Python"]))
        self.assertEqual(storage.read_study_overview(at=yesterday, tz=zone).total_seconds, 0)
        self.assertEqual(storage.read_study_overview(at=now, tz=zone).total_seconds, 300)
        self.assertEqual(storage.read_session_history().sessions, (retained,))

    def test_another_process_recovers_the_deletion_and_keeps_other_history_active_ids_and_preferences(self):
        self.related_sessions()
        retained = storage.create_manual_session(
            self.subjects["SQL"], "Leitura", at(200), 20, "Funções 🚀", at=at(1000),
        )
        active = storage.start_session(self.subjects["SQL"], "Aula", at=at(300))
        paused = storage.pause_session(active.id, at=at(310))
        storage.delete_subject(storage.read_subject_deletion_preview(self.subjects["Python"]))
        before = self.snapshot()
        script = """
import json, sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
import storage
with patch.object(storage, 'database_path', return_value=Path(sys.argv[1])):
    storage.initialize_database()
    page = storage.read_session_history()
    overview = storage.read_study_overview(at=datetime.fromisoformat(sys.argv[2]), tz=UTC)
    print(json.dumps([storage.list_subject_options(),
                      [[item.id, item.subject_id, item.topic] for item in page.sessions],
                      overview.total_seconds, overview.active_session.id,
                      overview.active_session.status,
                      storage.get_subject_timer_settings(overview.active_session.subject_id).mode]))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path), at(1000).isoformat()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True, timeout=20,
        )
        self.assertEqual(json.loads(result.stdout), [
            [[self.subjects["SQL"], "SQL"]], [[retained.id, self.subjects["SQL"], "Funções 🚀"]],
            30, paused.id, "paused", "progressive",
        ])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(storage.get_active_session(), paused)

