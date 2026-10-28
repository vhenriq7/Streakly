import asyncio
import sqlite3
from datetime import UTC, datetime

import flet as ft

from storage import (
    add_subject,
    finish_session,
    get_active_session,
    initialize_database,
    list_subject_options,
    pause_session,
    resume_session,
    start_session,
)
from timer import StudySession, elapsed_seconds, format_duration


async def main(page: ft.Page) -> None:
    page.title = "Streakly — prova de conceito"
    page.padding = 16

    session: StudySession | None = None
    subject_options: list[tuple[str, str]] = []
    busy = False
    ready = False
    state_lock = asyncio.Lock()
    ticker = None

    message = ft.Text()
    name_field = ft.TextField(label="Nome da matéria", hint_text="Ex.: Python")
    subjects = ft.ListView(
        controls=[ft.Text("Nenhuma matéria cadastrada ainda.")],
        expand=True, spacing=8,
    )
    subject_choice = ft.Dropdown(label="Matéria")
    study_type = ft.Dropdown(
        label="Tipo de estudo",
        value="Leitura",
        options=[ft.DropdownOption(key=name, text=name) for name in (
            "Leitura", "Aula", "Exercícios", "Revisão"
        )],
    )
    topic = ft.TextField(label="Assunto (opcional)", hint_text="Ex.: Funções")
    elapsed = ft.Text("00:00:00", size=40, weight=ft.FontWeight.BOLD)
    timer_status = ft.Text("Nenhuma sessão ativa.")
    session_details = ft.Text()

    def read_state():
        return list_subject_options(), get_active_session()

    async def refresh_state() -> None:
        nonlocal session, subject_options, ready
        choices, active = await asyncio.to_thread(read_state)
        if choices != subject_options:
            subject_options = choices
            subjects.controls = [ft.Text(name) for _, name in choices]
            if not choices:
                subjects.controls = [ft.Text("Nenhuma matéria cadastrada ainda.")]
            subject_choice.options = [
                ft.DropdownOption(key=subject_id, text=name)
                for subject_id, name in choices
            ]
        if subject_choice.value not in {subject_id for subject_id, _ in choices}:
            subject_choice.value = choices[0][0] if choices else None
        session = active
        ready = True

    def render() -> None:
        active = session is not None
        locked = busy or not ready
        name_field.disabled = save_button.disabled = locked
        for field in (subject_choice, study_type, topic):
            field.disabled = locked or active
        start_button.disabled = locked or active or not subject_options
        pause_button.visible = active and session.status == "running"
        resume_button.visible = active and session.status == "paused"
        finish_button.visible = active
        for button in (pause_button, resume_button, finish_button):
            button.disabled = locked
        if session:
            subject_choice.value = session.subject_id
            study_type.value = session.study_type
            topic.value = session.topic or ""
            elapsed.value = format_duration(elapsed_seconds(session.events, datetime.now(UTC)))
            timer_status.value = "Em andamento" if session.status == "running" else "Pausado"
            session_details.value = f"{session.subject_name} · {session.study_type}"
            if session.topic:
                session_details.value += f" · {session.topic}"
        else:
            elapsed.value = "00:00:00"
            timer_status.value = "Nenhuma sessão ativa."
            session_details.value = "Escolha a matéria e o tipo de estudo para começar."
        page.update()

    async def save_subject(event) -> None:
        nonlocal busy, ready
        if busy or not ready:
            return
        name = (name_field.value or "").strip()
        if not name:
            message.value = "Digite o nome da matéria."
            page.update()
            return
        busy = True
        render()
        saved = False
        async with state_lock:
            try:
                await asyncio.to_thread(add_subject, name)
                saved = True
                name_field.value = ""
                await refresh_state()
                message.value = "Matéria salva neste dispositivo."
            except ValueError as error:
                message.value = str(error)
            except (sqlite3.Error, OSError):
                ready = False
                message.value = (
                    "Matéria salva, mas a lista não pôde ser atualizada."
                    if saved else "Não foi possível salvar a matéria."
                )
            finally:
                busy = False
                render()

    async def perform(action, success_message: str, *args) -> None:
        nonlocal busy, ready, session
        if busy or not ready:
            return
        busy = True
        render()
        saved = False
        async with state_lock:
            try:
                result = await asyncio.to_thread(action, *args)
                saved = True
                session = result if result.status != "finished" else None
                message.value = success_message
                if result.status == "finished":
                    message.value += f" Tempo de estudo: {format_duration(result.duration_seconds)}."
                await refresh_state()
            except ValueError as error:
                message.value = str(error)
                # Outra janela pode ter alterado a mesma sessão.
                try:
                    await refresh_state()
                except (sqlite3.Error, OSError, ValueError):
                    ready = False
            except (sqlite3.Error, OSError):
                ready = False
                message.value = (
                    "Ação salva, mas a tela não pôde ser atualizada."
                    if saved else "Não foi possível salvar a ação do cronômetro."
                )
            finally:
                busy = False
                render()

    async def start(event) -> None:
        if not subject_choice.value:
            message.value = "Cadastre e escolha uma matéria para começar."
            page.update()
            return
        await perform(
            start_session, "Sessão iniciada.",
            subject_choice.value, study_type.value, topic.value,
        )

    async def pause(event) -> None:
        if session:
            await perform(pause_session, "Sessão pausada.", session.id)

    async def resume(event) -> None:
        if session:
            await perform(resume_session, "Sessão retomada.", session.id)

    async def finish(event) -> None:
        if session:
            await perform(finish_session, "Sessão finalizada e salva.", session.id)

    save_button = ft.Button("Salvar matéria", on_click=save_subject)
    start_button = ft.Button("Iniciar", on_click=start)
    pause_button = ft.Button("Pausar", on_click=pause)
    resume_button = ft.Button("Retomar", on_click=resume)
    finish_button = ft.Button("Finalizar", on_click=finish)
    subjects_panel = ft.Column(
        [ft.Text("Minhas matérias", size=20), name_field, save_button, subjects],
        expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    timer_panel = ft.Column(
        [
            ft.Text("Cronômetro progressivo", size=20),
            elapsed, timer_status, session_details,
            subject_choice, study_type, topic,
            ft.Row([start_button, pause_button, resume_button, finish_button], wrap=True),
            ft.Text("O tempo continua contando com o app fechado até você pausar ou finalizar."),
        ],
        expand=True, visible=False, scroll=ft.ScrollMode.AUTO,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    def show_subjects(event) -> None:
        subjects_panel.visible, timer_panel.visible = True, False
        page.update()

    def show_timer(event) -> None:
        subjects_panel.visible, timer_panel.visible = False, True
        page.update()

    page.add(ft.SafeArea(
        content=ft.Column(
            [
                ft.Text("Streakly", size=28),
                ft.Row([
                    ft.TextButton("Matérias", on_click=show_subjects),
                    ft.TextButton("Cronômetro", on_click=show_timer),
                ], wrap=True),
                message, subjects_panel, timer_panel,
            ],
            expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        expand=True,
    ))
    render()

    try:
        await asyncio.to_thread(initialize_database)
        await refresh_state()
    except ValueError as error:
        message.value = str(error)
        render()
        return
    except (sqlite3.Error, OSError):
        message.value = "Não foi possível abrir o banco local. Verifique o acesso à pasta de dados."
        render()
        return
    if session:
        subjects_panel.visible, timer_panel.visible = False, True
        message.value = "Sessão em andamento recuperada." if session.status == "running" else "Sessão pausada recuperada."
    render()

    async def update_timer() -> None:
        nonlocal ready
        while True:
            # Quando o app fica oculto, apenas a atualização da tela espera.
            await page.wait_until_visible()
            if not busy:
                async with state_lock:
                    if not busy:
                        try:
                            was_ready = ready
                            await refresh_state()
                            if not was_ready:
                                message.value = "Banco local disponível novamente."
                        except (sqlite3.Error, OSError, ValueError):
                            ready = False
                            message.value = "Não foi possível consultar a sessão. Tentando novamente…"
                        render()
            await asyncio.sleep(1)

    def start_updates(event=None) -> None:
        nonlocal ticker
        if ticker is None or ticker.done():
            ticker = page.run_task(update_timer)

    def stop_updates(event=None) -> None:
        if ticker:
            ticker.cancel()

    def on_lifecycle(event: ft.AppLifecycleStateChangeEvent) -> None:
        if event.state == ft.AppLifecycleState.DETACH:
            stop_updates()
        elif event.state in (ft.AppLifecycleState.RESUME, ft.AppLifecycleState.SHOW):
            start_updates()

    page.on_connect = start_updates
    page.on_disconnect = stop_updates
    page.on_close = stop_updates
    page.on_app_lifecycle_state_change = on_lifecycle
    start_updates()


if __name__ == "__main__":
    ft.run(main)
