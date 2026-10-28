import asyncio
import sqlite3
from datetime import UTC, datetime
from math import ceil

import flet as ft

from storage import (
    add_subject,
    finish_session,
    get_subject_timer_settings,
    initialize_database,
    list_subject_options,
    pause_session,
    refresh_active_session,
    resume_session,
    start_next_phase,
    start_session,
)
from timer import (
    StudySession,
    format_duration,
    pomodoro_remaining_seconds,
    session_elapsed_seconds,
)


POMODORO_PRESETS = {"25/5": (25 * 60, 5 * 60), "50/10": (50 * 60, 10 * 60)}


async def main(page: ft.Page) -> None:
    page.title = "Streakly — prova de conceito"
    page.padding = 16

    session: StudySession | None = None
    subject_options: list[tuple[str, str]] = []
    settings_subject: str | None = None
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
        label="Tipo de estudo", value="Leitura",
        options=[ft.DropdownOption(key=name, text=name) for name in (
            "Leitura", "Aula", "Exercícios", "Revisão"
        )],
    )
    topic = ft.TextField(label="Assunto (opcional)", hint_text="Ex.: Funções")
    mode_choice = ft.Dropdown(
        label="Modo", value="progressive",
        options=[
            ft.DropdownOption(key="progressive", text="Progressivo"),
            ft.DropdownOption(key="pomodoro", text="Pomodoro"),
        ],
    )
    preset_choice = ft.Dropdown(
        label="Preset Pomodoro", value="25/5",
        options=[
            ft.DropdownOption(key="25/5", text="25 min foco / 5 min pausa"),
            ft.DropdownOption(key="50/10", text="50 min foco / 10 min pausa"),
            ft.DropdownOption(key="custom", text="Personalizado"),
        ],
    )
    focus_minutes = ft.TextField(label="Foco (minutos)", value="25", width=210,
                                 keyboard_type=ft.KeyboardType.NUMBER)
    break_minutes = ft.TextField(label="Pausa curta (minutos)", value="5", width=210,
                                 keyboard_type=ft.KeyboardType.NUMBER)
    long_break_minutes = ft.TextField(label="Pausa longa (minutos)", value="15", width=210,
                                      keyboard_type=ft.KeyboardType.NUMBER)
    cycles = ft.TextField(label="Focos até a pausa longa", value="4", width=210,
                          keyboard_type=ft.KeyboardType.NUMBER)
    custom_fields = (focus_minutes, break_minutes, long_break_minutes, cycles)
    custom_panel = ft.Row(list(custom_fields), wrap=True, visible=False)
    elapsed = ft.Text("00:00:00", size=40, weight=ft.FontWeight.BOLD)
    study_total = ft.Text()
    cycle_info = ft.Text()
    timer_status = ft.Text("Nenhuma sessão ativa.")
    session_details = ft.Text()
    timer_note = ft.Text()

    def read_state():
        return list_subject_options(), refresh_active_session()

    def fill_pomodoro_fields(settings) -> None:
        preset_choice.value = settings.preset
        focus_minutes.value = str(settings.focus_seconds // 60)
        break_minutes.value = str(settings.break_seconds // 60)
        long_break_minutes.value = str(settings.long_break_seconds // 60)
        cycles.value = str(settings.cycles_until_long_break)

    def pomodoro_options() -> dict:
        if preset_choice.value in POMODORO_PRESETS:
            focus, pause = POMODORO_PRESETS[preset_choice.value]
            return dict(focus_seconds=focus, break_seconds=pause,
                        long_break_seconds=15 * 60, cycles_until_long_break=4,
                        preset=preset_choice.value)
        if preset_choice.value != "custom":
            raise ValueError("Escolha um preset Pomodoro.")
        values = []
        for field in custom_fields:
            try:
                value = int((field.value or "").strip())
            except ValueError:
                raise ValueError(f"{field.label}: use um número inteiro maior que zero.") from None
            if value <= 0:
                raise ValueError(f"{field.label}: use um número inteiro maior que zero.")
            values.append(value)
        focus, pause, long_pause, count = values
        return dict(focus_seconds=focus * 60, break_seconds=pause * 60,
                    long_break_seconds=long_pause * 60, cycles_until_long_break=count,
                    preset="custom")

    async def refresh_state() -> None:
        nonlocal session, subject_options, settings_subject, ready
        previous = session
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
        if not active and subject_choice.value and settings_subject != subject_choice.value:
            settings = await asyncio.to_thread(get_subject_timer_settings, subject_choice.value)
            mode_choice.value = settings.mode
            fill_pomodoro_fields(settings)
            settings_subject = subject_choice.value
        session = active
        ready = True
        if active and active.pomodoro and active.pomodoro.state == "ready":
            if (not previous or previous.id != active.id or not previous.pomodoro
                    or previous.pomodoro.state != "ready"
                    or previous.pomodoro.phase != active.pomodoro.phase):
                if active.pomodoro.phase == "break":
                    label = "pausa longa" if active.pomodoro.is_long_break else "pausa curta"
                    message.value = f"Foco concluído. Inicie a {label} quando quiser."
                else:
                    message.value = "Pausa concluída. Inicie o próximo foco quando quiser."

    def render() -> None:
        active = session is not None
        locked = busy or not ready
        if session:
            subject_choice.value = session.subject_id
            study_type.value = session.study_type
            topic.value = session.topic or ""
            mode_choice.value = session.mode
            if session.pomodoro:
                fill_pomodoro_fields(session.pomodoro)
        is_pomodoro = mode_choice.value == "pomodoro"
        phase = session.pomodoro if session else None
        waiting = phase is not None and phase.state == "ready"

        name_field.disabled = save_button.disabled = locked
        for field in (subject_choice, study_type, topic, mode_choice, preset_choice, *custom_fields):
            field.disabled = locked or active
        preset_choice.visible = is_pomodoro
        custom_panel.visible = is_pomodoro and preset_choice.value == "custom"
        start_button.disabled = locked or active or not subject_options
        pause_button.visible = active and (phase.state == "running" if phase else session.status == "running")
        resume_button.visible = active and (phase.state == "paused" if phase else session.status == "paused")
        next_button.visible = waiting
        if phase and phase.phase == "break":
            next_button.content = "Iniciar pausa longa" if phase.is_long_break else "Iniciar pausa"
        else:
            next_button.content = "Iniciar foco"
        finish_button.visible = active
        for button in (pause_button, resume_button, next_button, finish_button):
            button.disabled = locked

        now = datetime.now(UTC)
        study_total.visible = is_pomodoro
        cycle_info.visible = is_pomodoro
        timer_note.value = (
            "Cada fase termina no horário previsto e aguarda seu comando para a próxima."
            if is_pomodoro else
            "O tempo continua contando com o app fechado até você pausar ou finalizar."
        )
        if session:
            session_details.value = f"{session.subject_name} · {session.study_type}"
            if session.topic:
                session_details.value += f" · {session.topic}"
            seconds = session_elapsed_seconds(session, now)
            if phase:
                elapsed.value = format_duration(ceil(pomodoro_remaining_seconds(phase, now)))
                study_total.value = f"Tempo de estudo nesta sessão: {format_duration(seconds)}"
                label = "foco" if phase.phase == "focus" else (
                    "pausa longa" if phase.is_long_break else "pausa curta"
                )
                cycle_info.value = (
                    f"Focos concluídos nesta sessão: {phase.completed_focuses}. "
                    f"Pausa longa a cada {phase.cycles_until_long_break} focos."
                )
                if waiting:
                    timer_status.value = f"Aguardando início manual: {label}"
                elif phase.state == "paused":
                    timer_status.value = f"Contagem interrompida: {label}"
                else:
                    timer_status.value = f"Em andamento: {label}"
            else:
                elapsed.value = format_duration(seconds)
                timer_status.value = "Em andamento" if session.status == "running" else "Pausado"
        else:
            focus = 0
            if is_pomodoro:
                try:
                    options = pomodoro_options()
                    focus = options["focus_seconds"]
                    cycle_info.value = (
                        f"Pausa longa de {options['long_break_seconds'] // 60} min "
                        f"a cada {options['cycles_until_long_break']} focos completos."
                    )
                except ValueError:
                    cycle_info.value = "Preencha os tempos e a quantidade de focos com inteiros positivos."
            elapsed.value = format_duration(focus if is_pomodoro else 0)
            study_total.value = "Tempo de estudo nesta sessão: 00:00:00"
            timer_status.value = "Pronto para iniciar o foco." if is_pomodoro else "Nenhuma sessão ativa."
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

    async def choose_subject(event) -> None:
        nonlocal busy, settings_subject, ready
        if busy or not ready or session:
            return
        busy = True
        render()
        async with state_lock:
            try:
                settings_subject = None
                await refresh_state()
            except (sqlite3.Error, OSError, ValueError):
                ready = False
                message.value = "Não foi possível carregar a configuração da matéria."
            finally:
                busy = False
                render()

    def choose_mode_or_preset(event) -> None:
        render()

    async def perform(action, success_message: str, *args, **kwargs) -> None:
        nonlocal busy, ready, session
        if busy or not ready:
            return
        busy = True
        render()
        saved = False
        async with state_lock:
            try:
                result = await asyncio.to_thread(action, *args, **kwargs)
                saved = True
                session = result if result.status != "finished" else None
                message.value = success_message
                if result.status == "finished":
                    message.value += f" Tempo de estudo: {format_duration(result.duration_seconds)}."
                await refresh_state()
            except ValueError as error:
                message.value = str(error)
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
        options = {"mode": mode_choice.value}
        if mode_choice.value == "pomodoro":
            try:
                options.update(pomodoro_options())
            except ValueError as error:
                message.value = str(error)
                page.update()
                return
        await perform(
            start_session, "Sessão iniciada.",
            subject_choice.value, study_type.value, topic.value, **options,
        )

    async def pause(event) -> None:
        if session:
            await perform(pause_session, "Contagem pausada.", session.id)

    async def resume(event) -> None:
        if session:
            await perform(resume_session, "Contagem retomada.", session.id)

    async def next_phase(event) -> None:
        if session:
            await perform(start_next_phase, "Próxima fase iniciada.", session.id)

    async def finish(event) -> None:
        if session:
            await perform(finish_session, "Sessão finalizada e salva.", session.id)

    save_button = ft.Button("Salvar matéria", on_click=save_subject)
    start_button = ft.Button("Iniciar", on_click=start)
    pause_button = ft.Button("Pausar", on_click=pause)
    resume_button = ft.Button("Retomar", on_click=resume)
    next_button = ft.Button("Iniciar próxima fase", on_click=next_phase)
    finish_button = ft.Button("Finalizar", on_click=finish)
    subject_choice.on_select = choose_subject
    mode_choice.on_select = preset_choice.on_select = choose_mode_or_preset
    for field in custom_fields:
        field.on_change = choose_mode_or_preset

    subjects_panel = ft.Column(
        [ft.Text("Minhas matérias", size=20), name_field, save_button, subjects],
        expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    timer_panel = ft.Column(
        [
            ft.Text("Sessão de estudo", size=20),
            elapsed, timer_status, study_total, cycle_info, session_details,
            subject_choice, study_type, topic, mode_choice, preset_choice, custom_panel,
            ft.Row([start_button, pause_button, resume_button, next_button, finish_button], wrap=True),
            timer_note,
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
        if not session.pomodoro or session.pomodoro.state != "ready":
            message.value = "Sessão recuperada com os horários salvos."
    render()

    async def update_timer() -> None:
        nonlocal ready
        while True:
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
