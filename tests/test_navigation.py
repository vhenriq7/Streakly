"""Navegação adaptável com controles Flet reais e banco temporário."""

import asyncio
import csv
import inspect
import json
import io
import sqlite3
import tempfile
import unittest
from concurrent.futures import Future
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, DEFAULT, patch

import flet as ft

import main as app
import storage


DESTINATIONS = (
    "Início", "Matérias", "Cronômetro", "Histórico", "Configurações",
    "Calendário", "Estatísticas",
)
PANEL_TITLES = (
    "Tempo estudado hoje", "Minhas matérias", "Sessão de estudo",
    "Histórico de sessões", "Configurações", "Calendário", "Estatísticas",
)
NOW = datetime(2026, 10, 31, 15, tzinfo=UTC)


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


class FakePage:
    """Representa a janela sem abrir o cliente desktop ou executar o ticker."""

    def __init__(self, width=1200):
        self.width = width
        self.height = 800
        self.app_visible = True
        self.controls = []
        self.tasks = []
        self.dialogs = []

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self):
        pass

    def show_dialog(self, dialog):
        dialog.open = True
        self.dialogs.append(dialog)

    def pop_dialog(self):
        if self.dialogs:
            self.dialogs.pop().open = False

    def run_task(self, handler):
        future = Future()
        self.tasks.append(future)
        return future

    async def wait_until_visible(self):
        pass


def walk(control):
    yield control
    for child in getattr(control, "controls", ()):
        yield from walk(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from walk(content)


async def dispatch(handler, control, **values):
    result = handler(SimpleNamespace(control=control, **values))
    if inspect.isawaitable(result):
        await result


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory(prefix="streakly-navigation-test-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "test.sqlite3"
        self.enterContext(patch.object(storage, "database_path", return_value=self.path))
        self.enterContext(patch.object(app, "datetime", FixedClock))
        self.enterContext(patch.object(app, "windows_notifications_available", return_value=False))
        storage.initialize_database()
        storage.add_subject("Python")
        self.subject_id = storage.list_subject_options()[0][0]
        storage.create_manual_session(
            self.subject_id, "Leitura", NOW - timedelta(days=2), 600,
            "Funções", at=NOW,
        )
        active = storage.start_session(
            self.subject_id, "Aula", at=NOW - timedelta(hours=1),
        )
        storage.pause_session(active.id, at=NOW - timedelta(minutes=50))
        self.page = FakePage()
        await app.main(self.page)
        self.addCleanup(self.page.on_disconnect)

    def controls(self):
        return [item for root in self.page.controls for item in walk(root)]

    def rail(self):
        return next(item for item in self.controls() if isinstance(item, ft.NavigationRail))

    def compact_navigation(self):
        return next(
            item for item in self.controls()
            if isinstance(item, ft.Row)
            and tuple(getattr(child, "content", None) for child in item.controls) == DESTINATIONS
            and all(isinstance(child, ft.TextButton) for child in item.controls)
        )

    def panel(self, index):
        return next(
            item for item in self.controls()
            if isinstance(item, ft.Column) and item.controls
            and isinstance(item.controls[0], ft.Text)
            and item.controls[0].value == PANEL_TITLES[index]
        )

    def field(self, label):
        return next(item for item in self.controls() if getattr(item, "label", None) == label)

    def button(self, label):
        return next(
            item for item in self.controls()
            if isinstance(item, (ft.Button, ft.TextButton)) and item.content == label
        )

    async def click(self, label):
        button = self.button(label)
        self.assertFalse(button.disabled, label)
        await dispatch(button.on_click, button)

    async def resize(self, width):
        self.page.width = width
        await dispatch(self.page.on_resize, self.page, width=width, height=self.page.height)

    async def navigate(self, index, *, rail=True):
        if rail:
            navigation = self.rail()
            navigation.selected_index = index
            await dispatch(navigation.on_change, navigation)
        else:
            button = self.compact_navigation().controls[index]
            self.assertFalse(button.disabled, DESTINATIONS[index])
            await dispatch(button.on_click, button)

    def assert_destination(self, index):
        self.assertEqual(self.rail().selected_index, index)
        self.assertEqual([self.panel(i).visible for i in range(7)],
                         [i == index for i in range(7)])
        # A seleção precisa ser perceptível também na barra inferior.
        buttons = self.compact_navigation().controls
        self.assertTrue(all(button.style != buttons[index].style
                            for i, button in enumerate(buttons) if i != index))

    def database_snapshot(self):
        tables = (
            "subjects", "sessions", "session_events", "subject_timer_settings",
            "pomodoro_state", "daily_goal_defaults", "daily_goal_overrides", "app_settings",
        )
        with closing(sqlite3.connect(self.path)) as connection:
            return {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                    for table in tables}

    async def test_breakpoint_and_unknown_width_keep_all_destinations_accessible(self):
        rail, compact = self.rail(), self.compact_navigation()
        self.assertEqual(tuple(item.label for item in rail.destinations), DESTINATIONS)
        self.assertIsNotNone(compact.scroll)
        self.assertFalse(compact.wrap)
        for width, wide in ((None, False), (0, False), (390, False), (799, False),
                            (800, True), (1200, True), (799, False)):
            with self.subTest(width=width):
                await self.resize(width)
                self.assertEqual(rail.visible, wide)
                self.assertEqual(compact.visible, not wide)
                self.assert_destination(0)

    async def test_both_navigation_controls_select_the_same_seven_views(self):
        for use_rail, width in ((True, 1200), (False, 390)):
            await self.resize(width)
            for index, label in enumerate(DESTINATIONS):
                with self.subTest(rail=use_rail, destination=label):
                    await self.navigate(index, rail=use_rail)
                    self.assert_destination(index)

    async def test_home_shortcuts_update_navigation_selection(self):
        for use_rail, width in ((True, 1200), (False, 390)):
            await self.resize(width)
            for label, target in (("Python", 2), ("Abrir sessão em andamento", 2),
                                  ("Configurar meta diária", 4)):
                with self.subTest(width=width, shortcut=label):
                    await self.navigate(0, rail=use_rail)
                    await self.click(label)
                    self.assert_destination(target)

    async def test_resize_preserves_session_drafts_history_and_statistics_without_queries(self):
        await self.navigate(3)
        history_list = self.panel(3).controls[-1]
        self.assertTrue(history_list.controls)
        await self.click("Adicionar sessão")
        self.field("Assunto da sessão no histórico (opcional)").value = "Rascunho manual"
        self.field("Duração estudada (HH:MM)").value = "00:47"
        await self.navigate(6)
        statistics_panel = self.panel(6)
        statistics_total = statistics_panel.controls[8].value
        comparison_rows = statistics_panel.controls[16]
        self.field("Data inicial (DD/MM/AAAA)").value = "01/09/2026"
        await self.navigate(4)
        self.field("Tema do aplicativo").value = "dark"
        self.field("Meta diária padrão (HH:MM)").value = "03:17"
        await self.navigate(1)
        self.field("Nome da matéria").value = "Rascunho SQL"
        panels = [self.panel(index) for index in range(7)]
        snapshot = self.database_snapshot()
        readers = (
            "read_study_overview", "read_session_history", "read_period_statistics",
            "read_monthly_calendar", "read_daily_plan", "read_theme_mode",
        )
        with patch.multiple(app, **{name: DEFAULT for name in readers}) as mocks:
            for width in (390, 800, None, 1200):
                await self.resize(width)
                self.assert_destination(1)
                for index, panel in enumerate(panels):
                    self.assertIs(self.panel(index), panel)
                self.assertIs(self.panel(3).controls[-1], history_list)
                self.assertIs(self.panel(6).controls[16], comparison_rows)
                self.assertEqual(statistics_panel.controls[8].value, statistics_total)
                self.assertEqual(self.field("Nome da matéria").value, "Rascunho SQL")
                self.assertEqual(self.field("Tema do aplicativo").value, "dark")
                self.assertEqual(self.field("Meta diária padrão (HH:MM)").value, "03:17")
                self.assertEqual(self.field("Assunto da sessão no histórico (opcional)").value,
                                 "Rascunho manual")
                self.assertEqual(self.field("Duração estudada (HH:MM)").value, "00:47")
                self.assertEqual(self.field("Data inicial (DD/MM/AAAA)").value, "01/09/2026")
            for reader in mocks.values():
                reader.assert_not_called()
        self.assertEqual(self.database_snapshot(), snapshot)
        await self.navigate(2)
        self.assertTrue(self.button("Retomar").visible)
        self.assertTrue(any(isinstance(item, ft.Text) and item.value == "00:10:00"
                            for item in walk(self.panel(2))))
        with patch.object(app, "resume_session", side_effect=lambda session_id:
                          storage.resume_session(session_id, at=NOW)):
            await self.click("Retomar")
        running_snapshot = self.database_snapshot()
        for width in (390, 1200):
            await self.resize(width)
            self.assert_destination(2)
            self.assertTrue(self.button("Pausar").visible)
            self.assertFalse(self.button("Retomar").visible)
        self.assertEqual(self.database_snapshot(), running_snapshot)

    async def test_calendar_horizontal_resize_preserves_grid_without_queries(self):
        await self.navigate(5)
        self.assert_destination(5)
        panel = self.panel(5)
        viewport = next(
            item for item in walk(panel)
            if isinstance(item, ft.Container) and item.on_size_change is not None
        )
        self.assertEqual(viewport.content.scroll, ft.ScrollMode.ALWAYS)
        table = viewport.content.controls[0]
        weekday_header, month_grid = table.controls
        original_rows = tuple(month_grid.controls)
        original_cells = tuple(
            cell for row in original_rows for cell in row.controls
        )
        snapshot = self.database_snapshot()
        readers = (
            "read_study_overview", "read_monthly_calendar", "read_daily_plan",
            "read_period_statistics", "read_theme_mode",
        )

        with patch.multiple(app, **{name: DEFAULT for name in readers}) as mocks:
            for width, expected in ((360, 640), (640, 640), (920, 920), (0, 920), (920, 920)):
                with self.subTest(width=width):
                    await dispatch(
                        viewport.on_size_change, viewport, width=width, height=400,
                    )
                    self.assertEqual(table.width, expected)
                    self.assertIs(table.controls[0], weekday_header)
                    self.assertIs(table.controls[1], month_grid)
                    self.assertEqual(tuple(month_grid.controls), original_rows)
                    self.assertEqual(
                        tuple(cell for row in month_grid.controls for cell in row.controls),
                        original_cells,
                    )
            for reader in mocks.values():
                reader.assert_not_called()

        self.assertEqual(self.database_snapshot(), snapshot)
        self.assert_destination(5)

    async def test_history_export_saves_finalized_sessions_without_database_changes(self):
        await self.navigate(3)
        record = storage.read_session_history().sessions[0]
        for index in range(26):
            topic = "Funções, gráficos\ncálculo" if index == 0 else None
            storage.create_manual_session(
                self.subject_id, "Leitura", NOW - timedelta(days=10, minutes=index * 2),
                60, topic, at=NOW,
            )
        snapshot = self.database_snapshot()

        with patch.object(
            app.ft.FilePicker, "save_file", new_callable=AsyncMock,
            return_value="C:/exports/Streakly.csv",
        ) as save_file:
            await self.click("Exportar CSV")

        save_file.assert_awaited_once()
        arguments = save_file.await_args.kwargs
        self.assertEqual(arguments["file_type"], ft.FilePickerFileType.CUSTOM)
        self.assertEqual(arguments["allowed_extensions"], ["csv"])
        self.assertTrue(arguments["file_name"].startswith("Streakly_historico_"))
        rows = list(csv.DictReader(io.StringIO(arguments["src_bytes"].decode("utf-8-sig"))))
        self.assertEqual(len(rows), 27)
        self.assertEqual(rows[0]["ID da sessão"], record.id)
        self.assertEqual(rows[0]["ID da matéria"], record.subject_id)
        self.assertEqual(rows[0]["Matéria"], record.subject_name)
        self.assertEqual(rows[0]["Tipo de estudo"], record.study_type)
        self.assertEqual(rows[0]["Duração efetiva (segundos)"], repr(record.duration_seconds))
        self.assertIn("T", rows[0]["Início local (ISO 8601)"])
        self.assertIn("Funções, gráficos\ncálculo", [row["Assunto"] for row in rows])
        self.assertEqual(self.database_snapshot(), snapshot)

    async def test_json_backup_exports_all_local_tables_without_database_changes(self):
        storage.set_theme_mode("dark", "system")
        snapshot = self.database_snapshot()

        with patch.object(
            app.ft.FilePicker, "save_file", new_callable=AsyncMock,
            return_value="C:/exports/Streakly_backup.json",
        ) as save_file:
            await self.click("Exportar backup JSON")

        save_file.assert_awaited_once()
        arguments = save_file.await_args.kwargs
        self.assertEqual(arguments["file_type"], ft.FilePickerFileType.CUSTOM)
        self.assertEqual(arguments["allowed_extensions"], ["json"])
        self.assertTrue(arguments["file_name"].startswith("Streakly_backup_"))
        backup = json.loads(arguments["src_bytes"].decode("utf-8"))
        self.assertEqual(backup["format"], "streakly-backup")
        self.assertEqual(backup["format_version"], 1)
        self.assertEqual(backup["schema_version"], storage.SCHEMA_VERSION)
        self.assertEqual(
            set(backup["tables"]),
            {"subjects", "sessions", "session_events", "subject_timer_settings",
             "pomodoro_state", "daily_goal_defaults", "daily_goal_overrides", "app_settings"},
        )
        self.assertEqual(backup["tables"]["app_settings"], [{"key": "theme_mode", "value": "dark"}])
        self.assertEqual(len(backup["tables"]["sessions"]), 2)
        self.assertEqual(self.database_snapshot(), snapshot)

    async def test_json_backup_import_shows_preview_saves_safety_copy_and_merges(self):
        source_directory = tempfile.TemporaryDirectory(prefix="streakly-backup-source-")
        self.addCleanup(source_directory.cleanup)
        source_path = Path(source_directory.name) / "source.sqlite3"
        with patch.object(storage, "database_path", return_value=source_path):
            storage.initialize_database()
            storage.add_subject("Física")
            imported_subject_id = storage.list_subject_options()[0][0]
            imported_session = storage.create_manual_session(
                imported_subject_id, "Exercícios", NOW - timedelta(days=1), 90,
                "Energia e movimento", at=NOW,
            )
            source_backup = app.backup_json_bytes(storage.read_backup_snapshot(), exported_at=NOW)

        before = self.database_snapshot()
        with patch.object(
            app.ft.FilePicker, "pick_files", new_callable=AsyncMock,
            return_value=[SimpleNamespace(bytes=source_backup)],
        ) as pick_files, patch.object(
            app.ft.FilePicker, "save_file", new_callable=AsyncMock,
            return_value="C:/exports/Streakly_before_import.json",
        ) as save_file:
            await self.click("Importar backup JSON")

            pick_files.assert_awaited_once()
            self.assertEqual(len(self.page.dialogs), 1)
            dialog = self.page.dialogs[0]
            self.assertEqual(dialog.title.value, "Prévia da importação")
            self.assertTrue(dialog.content.controls[0].value.startswith("Registros novos:"))
            await dispatch(dialog.actions[1].on_click, dialog.actions[1])

        save_file.assert_awaited_once()
        safety_doc = json.loads(save_file.await_args.kwargs["src_bytes"].decode("utf-8"))
        self.assertEqual(
            safety_doc["tables"]["subjects"],
            [{"id": item[0], "name": item[1]} for item in before["subjects"]],
        )
        self.assertIn(imported_session.id, {
            item.id for item in storage.read_all_finished_sessions()
        })
        self.assertIn(imported_subject_id, dict(storage.list_subject_options()))
        self.assertEqual(self.page.dialogs, [])

    async def test_delayed_reads_do_not_restore_a_view_after_navigating_away(self):
        for index, reader_name in ((3, "read_session_history"), (4, "read_theme_mode"),
                                   (5, "read_monthly_calendar"), (6, "read_period_statistics")):
            with self.subTest(destination=DESTINATIONS[index]):
                await self.resize(1200)
                await self.navigate(1)
                entered, release = asyncio.Event(), asyncio.Event()
                original_to_thread = asyncio.to_thread
                target = getattr(app, reader_name)

                async def delayed(function, *args, **kwargs):
                    if function is target:
                        entered.set()
                        await release.wait()
                    return await original_to_thread(function, *args, **kwargs)

                with patch.object(app.asyncio, "to_thread", side_effect=delayed):
                    pending = asyncio.create_task(self.navigate(index))
                    try:
                        await asyncio.wait_for(entered.wait(), timeout=5)
                        await self.resize(390)
                        await self.navigate(1, rail=False)
                        self.assert_destination(1)
                        if index == 3:
                            self.assertTrue(self.rail().destinations[3].disabled)
                            self.assertTrue(self.compact_navigation().controls[3].disabled)
                            # Um evento atrasado de um destino bloqueado não troca a tela.
                            await self.navigate(3)
                            self.assert_destination(1)
                    finally:
                        release.set()
                        await asyncio.wait_for(pending, timeout=5)
                self.assert_destination(1)
                if index == 3:
                    self.assertFalse(self.rail().destinations[3].disabled)
                    self.assertFalse(self.compact_navigation().controls[3].disabled)

    async def test_subject_shortcut_does_not_return_to_timer_after_delayed_read(self):
        self.page.on_disconnect()
        storage.finish_session(storage.get_active_session().id, at=NOW)
        self.page = FakePage()
        await app.main(self.page)
        self.addCleanup(self.page.on_disconnect)
        snapshot = self.database_snapshot()
        entered, release = asyncio.Event(), asyncio.Event()
        original_to_thread = asyncio.to_thread

        async def delayed(function, *args, **kwargs):
            if function is app.read_study_overview:
                entered.set()
                await release.wait()
            return await original_to_thread(function, *args, **kwargs)

        with patch.object(app.asyncio, "to_thread", side_effect=delayed):
            pending = asyncio.create_task(self.click("Python"))
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                self.assert_destination(2)
                await self.navigate(1)
                await self.resize(390)
                self.assert_destination(1)
            finally:
                release.set()
                await asyncio.wait_for(pending, timeout=5)
        self.assert_destination(1)
        self.assertEqual(self.field("Matéria").value, self.subject_id)
        self.assertEqual(self.database_snapshot(), snapshot)


if __name__ == "__main__":
    unittest.main()
