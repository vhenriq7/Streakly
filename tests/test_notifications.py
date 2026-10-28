"""Verifica avisos de fases sem abrir PowerShell nem exibir notificações reais."""

import base64
import json
import subprocess
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import notifications
from timer import PomodoroState, SessionEvent, StudySession


START = datetime(2026, 10, 28, 12, tzinfo=UTC)


def at(seconds):
    return START + timedelta(seconds=seconds)


def running_session():
    return StudySession(
        id="session-1", subject_id="subject-1", subject_name="Python",
        study_type="Leitura", topic=None, status="running", started_at=at(0),
        ended_at=None, events=(SessionEvent("start", at(0)),), duration_seconds=0,
        mode="pomodoro", pomodoro=PomodoroState(
            phase="focus", state="running", focus_seconds=60, break_seconds=30,
            remaining_seconds=60, started_at=at(0), updated_at=at(0),
            long_break_seconds=90, cycles_until_long_break=2, preset="custom",
        ),
    )


def ready_session(*, phase="break", deadline=60, long_break=False):
    session = running_session()
    return replace(
        session, status="paused", duration_seconds=60,
        events=(*session.events, SessionEvent("pause", at(60))),
        pomodoro=replace(
            session.pomodoro, phase=phase, state="ready", started_at=None,
            updated_at=at(deadline), completed_focuses=2 if long_break else 1,
            remaining_seconds=(90 if long_break else 30) if phase == "break" else 60,
            is_long_break=long_break,
        ),
    )


class PhaseAlertTrackerTests(unittest.TestCase):
    def test_first_read_of_old_ready_phase_and_reopening_do_not_alert(self):
        ready = ready_session()
        first_page = notifications.PhaseAlertTracker()
        reopened_page = notifications.PhaseAlertTracker()
        self.assertIsNone(first_page.observe(ready))
        self.assertIsNone(first_page.observe(ready))
        self.assertIsNone(reopened_page.observe(ready))

    def test_completed_focus_announces_manual_short_break_without_changing_state(self):
        tracker = notifications.PhaseAlertTracker()
        self.assertIsNone(tracker.observe(None))
        self.assertIsNone(tracker.observe(running_session()))
        ready = ready_session()
        before = replace(ready)
        notice = tracker.observe(ready)
        self.assertIsInstance(notice, notifications.PhaseNotice)
        self.assertIn("Streakly", notice.title)
        self.assertIn("Python", notice.body)
        self.assertIn("foco", notice.body.lower())
        self.assertIn("pausa curta", notice.body.lower())
        self.assertEqual(ready, before)

    def test_long_break_and_completed_break_have_different_manual_instructions(self):
        tracker = notifications.PhaseAlertTracker()
        tracker.observe(running_session())
        notice = tracker.observe(ready_session(long_break=True))
        self.assertIn("pausa longa", notice.body.lower())
        notice = tracker.observe(ready_session(phase="focus", deadline=150))
        self.assertIn("pausa", notice.body.lower())
        self.assertIn("foco", notice.body.lower())
        self.assertNotIn("pausa longa", notice.body.lower())

    def test_repeated_reads_and_reconnection_do_not_repeat_a_notice(self):
        tracker = notifications.PhaseAlertTracker()
        tracker.observe(running_session())
        ready = ready_session()
        self.assertIsNotNone(tracker.observe(ready))
        self.assertIsNone(tracker.observe(ready))
        self.assertIsNone(tracker.observe(replace(ready, subject_name="Python renomeado")))
        self.assertIsNone(tracker.observe(running_session()))
        self.assertIsNone(tracker.observe(ready))
        self.assertIsNone(tracker.observe(None))
        self.assertIsNone(tracker.observe(ready))

    def test_another_deadline_or_session_can_announce_the_same_next_phase(self):
        tracker = notifications.PhaseAlertTracker()
        tracker.observe(running_session())
        first = ready_session()
        self.assertIsNotNone(tracker.observe(first))
        self.assertIsNotNone(tracker.observe(ready_session(deadline=150)))
        self.assertIsNotNone(tracker.observe(replace(first, id="session-2")))

    def test_progressive_paused_running_and_finished_states_never_alert(self):
        tracker = notifications.PhaseAlertTracker()
        tracker.observe(None)
        running = running_session()
        candidates = (
            None,
            running,
            replace(running, status="paused", pomodoro=replace(
                running.pomodoro, state="paused", started_at=None, updated_at=at(10),
            )),
            replace(running, mode="progressive", pomodoro=None),
            replace(ready_session(), status="finished", ended_at=at(60)),
        )
        for candidate in candidates:
            with self.subTest(session=candidate):
                self.assertIsNone(tracker.observe(candidate))


class WindowsNoticeTests(unittest.TestCase):
    def test_unavailable_platform_never_starts_a_process(self):
        with patch.object(notifications, "windows_notifications_available", return_value=False), \
                patch.object(notifications.subprocess, "run") as run:
            self.assertFalse(notifications.send_windows_notice(
                notifications.PhaseNotice("Streakly", "Foco concluído."),
            ))
            run.assert_not_called()

    def test_non_windows_platform_is_unavailable_even_if_powershell_path_exists(self):
        with patch.object(notifications.sys, "platform", "linux"), \
                patch("pathlib.Path.is_file", return_value=True):
            self.assertFalse(notifications.windows_notifications_available())

    def test_user_text_is_json_data_and_process_is_hidden_and_bounded(self):
        notice = notifications.PhaseNotice(
            "Streakly — teste", "Matéria 🚀 '); Start-Process calc; #\n$env:USERNAME",
        )
        with patch.object(notifications, "windows_notifications_available", return_value=True), \
                patch.object(notifications.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
                patch.object(notifications.subprocess, "run", return_value=
                             subprocess.CompletedProcess([], 0)) as run:
            self.assertTrue(notifications.send_windows_notice(notice))
        command = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertIsInstance(command, list)
        self.assertFalse(options.get("shell", False))
        self.assertTrue(options["capture_output"])
        self.assertGreater(options["timeout"], 0)
        self.assertLessEqual(options["timeout"], 30)
        self.assertTrue(options["creationflags"] & 0x08000000)
        self.assertEqual(json.loads(options["input"]), {"title": notice.title, "body": notice.body})
        self.assertTrue(options["input"].isascii())
        lowered = [part.lower() for part in command]
        encoded_index = lowered.index("-encodedcommand") + 1
        script = base64.b64decode(command[encoded_index]).decode("utf-16-le")
        self.assertNotIn(notice.body, script)
        self.assertNotIn("Start-Process calc", script)
        self.assertIn("Dispose", script)

    def test_process_failure_and_timeout_are_reported_without_raising(self):
        notice = notifications.PhaseNotice("Streakly", "Inicie a próxima fase.")
        failures = (
            subprocess.CompletedProcess([], 1),
            OSError("PowerShell indisponível"),
            subprocess.TimeoutExpired("powershell.exe", 20),
        )
        with patch.object(notifications, "windows_notifications_available", return_value=True):
            for failure in failures:
                with self.subTest(failure=failure):
                    arguments = ({"side_effect": failure} if isinstance(failure, Exception)
                                 else {"return_value": failure})
                    with patch.object(notifications.subprocess, "run", **arguments):
                        self.assertFalse(notifications.send_windows_notice(notice))


if __name__ == "__main__":
    unittest.main()
