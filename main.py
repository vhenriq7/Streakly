import asyncio
import sqlite3
from datetime import UTC, date, datetime, timedelta
from math import ceil

import flet as ft

from notifications import (
    PhaseAlertTracker,
    PhaseNotice,
    send_windows_notice,
    windows_notifications_available,
)
from storage import (
    add_subject,
    create_manual_session,
    delete_finished_session,
    finish_session,
    get_subject_timer_settings,
    initialize_database,
    pause_session,
    read_session_history,
    read_study_overview,
    rename_subject,
    resume_session,
    start_next_phase,
    start_session,
    update_finished_session,
)
from timer import (
    StudySession,
    format_duration,
    pomodoro_remaining_seconds,
    session_elapsed_seconds,
)


POMODORO_PRESETS = {"25/5": (25 * 60, 5 * 60), "50/10": (50 * 60, 10 * 60)}
HISTORY_PAGE_SIZE = 25
STUDY_TYPES = ("Leitura", "Aula", "Exercícios", "Revisão")


def parse_local_start(date_text: str, time_text: str) -> datetime:
    """Interpreta uma data/hora local sem escolher silenciosamente um fuso ambíguo."""
    try:
        local_start = datetime.strptime(f"{date_text.strip()} {time_text.strip()}", "%d/%m/%Y %H:%M")
    except ValueError:
        raise ValueError("Informe a data como DD/MM/AAAA e o horário como HH:MM.") from None

    # O relógio local pode pular ou repetir um horário em uma mudança de fuso.
    candidates = set()
    try:
        for fold in (0, 1):
            candidate = local_start.replace(fold=fold).astimezone(UTC)
            if candidate.astimezone().replace(tzinfo=None) == local_start:
                candidates.add(candidate)
    except (ValueError, OSError, OverflowError):
        raise ValueError("Não foi possível interpretar o início no fuso local do dispositivo.") from None
    if not candidates:
        raise ValueError("Esse horário não existe no fuso local. Confira o início informado.")
    if len(candidates) > 1:
        raise ValueError("Esse horário se repete no fuso local. Informe um início sem ambiguidade.")
    return candidates.pop()


def parse_study_duration(duration_text: str) -> int:
    """Converte uma duração positiva HH:MM em segundos."""
    parts = duration_text.strip().split(":")
    if (len(parts) != 2 or len(parts[1]) != 2
            or not all(part.isascii() and part.isdigit() for part in parts)):
        raise ValueError("Informe a duração como HH:MM, por exemplo 01:30.")
    try:
        hours, minutes = map(int, parts)
        seconds = (hours * 60 + minutes) * 60
        if minutes >= 60 or seconds <= 0:
            raise ValueError
    except ValueError:
        raise ValueError("A duração deve ser positiva, com minutos entre 00 e 59.") from None
    return seconds


def parse_manual_period(date_text: str, time_text: str, duration_text: str) -> tuple[datetime, int]:
    """Converte início local e duração HH:MM em um instante UTC e segundos."""
    start = parse_local_start(date_text, time_text)
    seconds = parse_study_duration(duration_text)
    try:
        start + timedelta(seconds=seconds)
    except OverflowError:
        raise ValueError("A duração informada é alta demais.") from None
    return start, seconds


async def main(page: ft.Page) -> None:
    page.title = "Streakly — prova de conceito"
    page.padding = 16

    session: StudySession | None = None
    subject_options: list[tuple[str, str]] = []
    settings_subject: str | None = None
    today: date | None = None
    today_seconds = 0.0
    history_offset = 0
    history_has_more = False
    history_loaded = False
    history_loading = False
    manual_open = False
    manual_saving = False
    editing_session: StudySession | None = None
    original_period_texts: tuple[str, str, str] | None = None
    delete_dialog: ft.AlertDialog | None = None
    delete_busy = False
    renaming_subject: tuple[str, str] | None = None
    rename_saving = False
    busy = False
    ready = False
    state_lock = asyncio.Lock()
    ticker = None
    connected = True
    notifications_available = windows_notifications_available()
    alert_tracker = PhaseAlertTracker()
    pending_notices: set[asyncio.Task] = set()

    message = ft.Text()
    name_field = ft.TextField(label="Nome da matéria", hint_text="Ex.: Python")
    rename_name = ft.TextField(label="Novo nome da matéria")
    rename_context = ft.Text()
    rename_message = ft.Text()
    subjects = ft.ListView(
        controls=[ft.Text("Nenhuma matéria cadastrada ainda.")],
        expand=True, spacing=8,
    )
    daily_date = ft.Text()
    daily_total = ft.Text("Carregando…", size=40, weight=ft.FontWeight.BOLD)
    home_session = ft.Text()
    home_subjects = ft.ListView(
        controls=[ft.Text("Nenhuma matéria cadastrada ainda.")],
        expand=True, spacing=8,
    )
    history_status = ft.Text("Abra o histórico para consultar as sessões finalizadas.")
    history_page_label = ft.Text()
    history_list = ft.ListView(expand=True, spacing=16)
    manual_subject = ft.Dropdown(label="Matéria da sessão no histórico")
    manual_study_type = ft.Dropdown(
        label="Tipo de estudo da sessão no histórico", value="Leitura",
        options=[ft.DropdownOption(key=name, text=name) for name in STUDY_TYPES],
    )
    manual_topic = ft.TextField(label="Assunto da sessão no histórico (opcional)")
    manual_date = ft.TextField(label="Data de início (DD/MM/AAAA)", width=230)
    manual_time = ft.TextField(label="Horário de início (HH:MM)", width=230)
    manual_duration = ft.TextField(label="Duração estudada (HH:MM)", value="00:30", width=230)
    manual_message = ft.Text()
    manual_end = ft.Text()
    manual_title = ft.Text("Adicionar sessão manual", size=20)
    manual_hint = ft.Text()
    subject_choice = ft.Dropdown(label="Matéria")
    study_type = ft.Dropdown(
        label="Tipo de estudo", value="Leitura",
        options=[ft.DropdownOption(key=name, text=name) for name in STUDY_TYPES],
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
    notification_hint = ft.Text(
        "Mantenha o app aberto para receber avisos do Windows, inclusive minimizado."
        if notifications_available else "A conclusão de cada fase aparece nesta tela."
    )

    async def deliver_notice(notice: PhaseNotice) -> None:
        try:
            sent = await asyncio.to_thread(send_windows_notice, notice)
            notification_hint.value = (
                "Aviso enviado ao Windows. A exibição depende das configurações do sistema."
                if sent else "Não foi possível enviar o aviso do Windows. Confira a conclusão nesta tela."
            )
        finally:
            pending_notices.discard(asyncio.current_task())
            if connected:
                render()

    def queue_notice(notice: PhaseNotice) -> None:
        if notifications_available and connected:
            task = asyncio.create_task(deliver_notice(notice))
            pending_notices.add(task)
            task.add_done_callback(pending_notices.discard)

    def test_notice(event) -> None:
        if pending_notices:
            return
        queue_notice(PhaseNotice(
            "Streakly — teste de aviso",
            "Os avisos do Pomodoro funcionam com o app aberto, inclusive minimizado.",
        ))
        render()

    def subject_shortcut(subject_id: str, name: str) -> ft.TextButton:
        async def open_subject(event) -> None:
            if busy or not ready:
                return
            if session:
                if session.subject_id != subject_id:
                    message.value = "Retome ou finalize a sessão ativa antes de trocar de matéria."
            else:
                subject_choice.value = subject_id
                await choose_subject(event)
            show_timer(event)

        return ft.TextButton(name, on_click=open_subject)

    def subject_row(subject_id: str, name: str) -> ft.Row:
        def choose_rename(event) -> None:
            open_subject_rename(subject_id, name)

        return ft.Row([ft.Text(name, expand=True), ft.TextButton("Renomear", on_click=choose_rename)])

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
        nonlocal session, subject_options, settings_subject, ready, today, today_seconds
        previous = session
        overview = await asyncio.to_thread(read_study_overview, at=datetime.now(UTC))
        choices, active = list(overview.subjects), overview.active_session
        if choices != subject_options:
            subject_options = choices
            subjects.controls = [subject_row(key, name) for key, name in choices]
            home_subjects.controls = [subject_shortcut(key, name) for key, name in choices]
            if not choices:
                subjects.controls = [ft.Text("Nenhuma matéria cadastrada ainda.")]
                home_subjects.controls = [ft.Text("Nenhuma matéria cadastrada ainda.")]
            subject_choice.options = [
                ft.DropdownOption(key=subject_id, text=name)
                for subject_id, name in choices
            ]
            manual_subject.options = [
                ft.DropdownOption(key=subject_id, text=name)
                for subject_id, name in choices
            ]
        if manual_subject.value not in {subject_id for subject_id, _ in choices}:
            manual_subject.value = choices[0][0] if choices else None
        if subject_choice.value not in {subject_id for subject_id, _ in choices}:
            subject_choice.value = choices[0][0] if choices else None
        if not active and subject_choice.value and settings_subject != subject_choice.value:
            settings = await asyncio.to_thread(get_subject_timer_settings, subject_choice.value)
            mode_choice.value = settings.mode
            fill_pomodoro_fields(settings)
            settings_subject = subject_choice.value
        session = active
        today, today_seconds = overview.day, overview.total_seconds
        ready = True
        notice = alert_tracker.observe(active)
        if notice:
            queue_notice(notice)
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
        name_field.visible = save_button.visible = renaming_subject is None
        rename_panel.visible = renaming_subject is not None
        rename_name.disabled = locked or rename_saving
        rename_save_button.disabled = locked or rename_saving or history_loading
        rename_cancel_button.disabled = rename_saving
        for row in subjects.controls:
            if isinstance(row, ft.Row):
                for control in row.controls:
                    if isinstance(control, ft.TextButton):
                        control.disabled = (locked or history_loading or renaming_subject is not None
                                            or delete_dialog is not None)
        open_session_button.visible = active
        open_session_button.disabled = locked
        for shortcut in home_subjects.controls:
            shortcut.disabled = locked
        daily_date.value = today.strftime("%d/%m/%Y") if today else ""
        daily_total.value = format_duration(today_seconds) if ready else "Total indisponível"
        history_locked = locked or history_loading or delete_dialog is not None
        timing_editable = editing_session is None or editing_session.source == "manual"
        history_nav_button.disabled = history_loading or manual_saving or delete_dialog is not None
        if delete_dialog:
            delete_dialog.actions[0].disabled = delete_busy
            delete_dialog.actions[1].disabled = locked or delete_busy
        manual_open_button.disabled = history_locked or not subject_options
        manual_save_button.disabled = locked or manual_saving or not subject_options
        manual_cancel_button.disabled = manual_saving
        manual_save_button.content = "Salvar alterações" if editing_session else "Salvar registro manual"
        manual_title.value = "Editar sessão" if editing_session else "Adicionar sessão manual"
        manual_timing_row.visible = timing_editable
        manual_hint.value = (
            "Informe apenas o tempo estudado. Cada registro representa um período contínuo, sem pausas."
            if timing_editable else
            "Os horários e as pausas registrados pelo cronômetro serão preservados."
        )
        for field in (manual_subject, manual_study_type, manual_topic,
                      manual_date, manual_time, manual_duration):
            field.disabled = locked or manual_saving
        for field in (manual_date, manual_time, manual_duration):
            field.disabled = locked or manual_saving or not timing_editable
        manual_panel.visible = manual_open
        history_toolbar.visible = not manual_open
        history_list.visible = not manual_open
        history_refresh_button.disabled = history_locked
        history_previous_button.disabled = history_locked or not history_loaded or history_offset == 0
        history_next_button.disabled = history_locked or not history_loaded or not history_has_more
        for row in history_list.controls:
            if isinstance(row, ft.Column):
                for control in row.controls:
                    if isinstance(control, ft.TextButton):
                        control.disabled = history_locked or manual_saving
        for field in (subject_choice, study_type, topic, mode_choice, preset_choice, *custom_fields):
            field.disabled = locked or active
        preset_choice.visible = is_pomodoro
        custom_panel.visible = is_pomodoro and preset_choice.value == "custom"
        notification_hint.visible = is_pomodoro
        test_notice_button.visible = is_pomodoro and notifications_available
        test_notice_button.disabled = locked or bool(pending_notices)
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
        home_session.value = (
            f"{session.subject_name} · {session.study_type}\n"
            f"{timer_status.value} · {format_duration(session_elapsed_seconds(session, now))} de estudo na sessão."
            if session else "Nenhuma sessão em andamento."
        )
        if connected and page.app_visible:
            page.update()

    async def save_subject(event) -> None:
        nonlocal busy, ready
        if busy or not ready or renaming_subject is not None:
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

    def open_subject_rename(subject_id: str, name: str) -> None:
        nonlocal renaming_subject
        if (busy or not ready or rename_saving or renaming_subject is not None
                or history_loading or delete_dialog is not None):
            return
        renaming_subject = (subject_id, name)
        rename_context.value = f"Nome atual: {name}"
        rename_name.value = name
        rename_message.value = ""
        render()

    def cancel_subject_rename(event) -> None:
        nonlocal renaming_subject
        if not rename_saving:
            renaming_subject = None
            render()

    async def save_subject_name(event) -> None:
        nonlocal busy, ready, renaming_subject, rename_saving
        if busy or not ready or rename_saving or history_loading or renaming_subject is None:
            return
        new_name = (rename_name.value or "").strip()
        if not new_name:
            rename_message.value = "Digite o novo nome da matéria."
            render()
            return
        subject_id, expected_name = renaming_subject
        busy = rename_saving = True
        saved = False
        render()
        async with state_lock:
            try:
                await asyncio.to_thread(rename_subject, subject_id, expected_name, new_name)
                saved = True
                renaming_subject = None
                message.value = f"Matéria renomeada para {new_name}."
                await refresh_state()
            except ValueError as error:
                if saved:
                    ready = False
                    message.value = f"Nome alterado, mas a tela não pôde ser atualizada: {error}"
                else:
                    rename_message.value = str(error)
                    try:
                        await refresh_state()
                    except (sqlite3.Error, OSError, ValueError):
                        ready = False
            except (sqlite3.Error, OSError):
                if saved:
                    ready = False
                    message.value = "Nome alterado, mas a tela não pôde ser atualizada."
                else:
                    rename_message.value = "Não foi possível renomear a matéria. Tente novamente."
            finally:
                busy = rename_saving = False
                render()
        if saved and history_loaded:
            await load_history(history_offset)

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

    def history_entry(record: StudySession) -> ft.Column:
        if record.ended_at is None:
            raise ValueError("Sessão finalizada sem horário de término.")
        mode = ("Registro manual" if record.source == "manual" else
                "Pomodoro" if record.mode == "pomodoro" else "Progressivo")
        start = record.started_at.astimezone().strftime("%d/%m/%Y %H:%M:%S (%z)")
        end = record.ended_at.astimezone().strftime("%d/%m/%Y %H:%M:%S (%z)")
        details = [
            ft.Text(f"{record.subject_name} · {format_duration(record.duration_seconds)}",
                    size=18, weight=ft.FontWeight.BOLD),
            ft.Text(f"{record.study_type} · {mode}"),
        ]
        if record.topic:
            details.append(ft.Text(f"Assunto: {record.topic}"))
        def edit_record(event) -> None:
            open_edit(record)

        def delete_record(event) -> None:
            request_delete(record)

        details.extend([ft.Text(f"Início: {start}"), ft.Text(f"Fim: {end}"),
                        ft.TextButton("Editar", on_click=edit_record),
                        ft.TextButton("Excluir", on_click=delete_record), ft.Divider()])
        return ft.Column(details, spacing=4, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    def close_delete_dialog(dialog: ft.AlertDialog) -> None:
        nonlocal delete_dialog
        if delete_dialog is not dialog:
            return
        delete_dialog = None
        if dialog.open:
            if connected:
                page.pop_dialog()
            else:
                dialog.open = False

    def request_delete(record: StudySession) -> None:
        nonlocal delete_dialog
        if (busy or not ready or history_loading or manual_open or delete_busy
                or delete_dialog is not None or not connected or not page.app_visible):
            return
        error_message = ft.Text()

        def dismissed(event) -> None:
            nonlocal delete_dialog
            if delete_dialog is dialog:
                delete_dialog = None
                render()

        def cancel_delete(event) -> None:
            if delete_busy or delete_dialog is not dialog:
                return
            close_delete_dialog(dialog)
            render()

        async def confirm_delete(event) -> None:
            nonlocal busy, delete_busy, ready
            if (busy or delete_busy or not ready or not connected
                    or delete_dialog is not dialog or not dialog.open):
                return
            busy = delete_busy = True
            deleted = False
            error_message.value = ""
            render()
            async with state_lock:
                try:
                    await asyncio.to_thread(delete_finished_session, record)
                    deleted = True
                    close_delete_dialog(dialog)
                    message.value = "Sessão excluída."
                    await refresh_state()
                except ValueError as error:
                    if deleted:
                        ready = False
                        message.value = f"Sessão excluída, mas a tela não pôde ser atualizada: {error}"
                    elif delete_dialog is dialog:
                        error_message.value = str(error)
                    else:
                        message.value = str(error)
                except (sqlite3.Error, OSError):
                    if deleted:
                        ready = False
                        message.value = "Sessão excluída, mas o total não pôde ser atualizado."
                    elif delete_dialog is dialog:
                        error_message.value = "Não foi possível excluir a sessão. Tente novamente."
                    else:
                        message.value = "Não foi possível excluir a sessão. Tente novamente."
                finally:
                    busy = delete_busy = False
                    render()
            if deleted:
                await load_history()

        start = record.started_at.astimezone().strftime("%d/%m/%Y %H:%M:%S (%z)")
        end = record.ended_at.astimezone().strftime("%d/%m/%Y %H:%M:%S (%z)")
        origin = ("Registro manual" if record.source == "manual" else
                  "Pomodoro" if record.mode == "pomodoro" else "Progressivo")
        summary = [
            ft.Text(f"{record.subject_name} · {record.study_type}"),
            ft.Text(origin),
            ft.Text(f"Início: {start}\nFim: {end}"),
            ft.Text(f"Tempo de estudo: {format_duration(record.duration_seconds)}"),
        ]
        if record.topic:
            summary.append(ft.Text(f"Assunto: {record.topic}"))
        summary.extend([
            ft.Text("A exclusão é permanente e remove esta sessão do Histórico e dos totais."),
            error_message,
        ])
        dialog = ft.AlertDialog(
            modal=True, scrollable=True, title=ft.Text("Excluir sessão?"),
            content=ft.Column(summary, tight=True),
            actions=[ft.TextButton("Cancelar", on_click=cancel_delete),
                     ft.Button("Excluir definitivamente", on_click=confirm_delete)],
            on_dismiss=dismissed,
        )
        delete_dialog = dialog
        page.show_dialog(dialog)
        render()

    async def load_history(offset: int = 0) -> None:
        nonlocal history_loading, history_loaded, history_offset, history_has_more, history_list
        if history_loading:
            return
        if not ready:
            history_status.value = "Histórico indisponível enquanto o banco local não puder ser lido."
            render()
            return
        history_loading = True
        history_status.value = "Carregando histórico…"
        render()
        try:
            result = await asyncio.to_thread(read_session_history, limit=HISTORY_PAGE_SIZE, offset=offset)
            entries = [history_entry(record) for record in result.sessions]
            # Recriar a lista faz cada página começar no topo.
            history_list = ft.ListView(
                controls=entries or [ft.Text("Nenhuma sessão finalizada nesta página.")],
                expand=True, spacing=16,
            )
            history_panel.controls[-1] = history_list
            history_offset, history_has_more = result.offset, result.has_more
            history_loaded = True
            history_page_label.value = f"Página {history_offset // HISTORY_PAGE_SIZE + 1}"
            history_status.value = "Sessões finalizadas, ordenadas pelo início mais recente. Horários locais."
        except (sqlite3.Error, OSError, ValueError):
            history_status.value = "Não foi possível carregar o histórico. Tente Atualizar."
        finally:
            history_loading = False
            render()

    async def refresh_history(event) -> None:
        await load_history()

    async def previous_history_page(event) -> None:
        await load_history(max(0, history_offset - HISTORY_PAGE_SIZE))

    async def next_history_page(event) -> None:
        await load_history(history_offset + HISTORY_PAGE_SIZE)

    def form_period() -> tuple[datetime, float]:
        if editing_session and original_period_texts:
            original_date, original_time, original_duration = original_period_texts
            start = editing_session.started_at
            seconds = editing_session.duration_seconds
            if (manual_date.value, manual_time.value) != (original_date, original_time):
                start = parse_local_start(manual_date.value or "", manual_time.value or "")
            if manual_duration.value != original_duration:
                seconds = parse_study_duration(manual_duration.value or "")
            return start, seconds
        return parse_manual_period(
            manual_date.value or "", manual_time.value or "", manual_duration.value or "",
        )

    def preview_manual_end(event=None) -> None:
        if editing_session and editing_session.source != "manual":
            start = editing_session.started_at.astimezone()
            end = editing_session.ended_at.astimezone()
            manual_end.value = (
                f"Período registrado: {start:%d/%m/%Y %H:%M:%S (%z)} "
                f"até {end:%d/%m/%Y %H:%M:%S (%z)}. "
                f"Estudo efetivo: {format_duration(editing_session.duration_seconds)}."
            )
            render()
            return
        try:
            start, seconds = form_period()
            end = (start + timedelta(seconds=seconds)).astimezone()
            manual_end.value = f"Fim calculado: {end:%d/%m/%Y %H:%M:%S (%z)}"
        except (ValueError, OverflowError):
            manual_end.value = "Preencha o início e a duração para conferir o fim calculado."
        render()

    def open_manual(event) -> None:
        nonlocal manual_open, editing_session, original_period_texts
        if busy or not ready or manual_saving or delete_dialog is not None:
            return
        start = (datetime.now(UTC) - timedelta(minutes=30)).astimezone()
        manual_date.value, manual_time.value = start.strftime("%d/%m/%Y"), start.strftime("%H:%M")
        manual_duration.value = "00:30"
        editing_session, original_period_texts = None, None
        manual_subject.value = subject_choice.value
        manual_study_type.value = "Leitura"
        manual_study_type.options = [ft.DropdownOption(key=name, text=name) for name in STUDY_TYPES]
        manual_topic.value = ""
        manual_message.value = ""
        manual_open = True
        preview_manual_end()

    def open_edit(record: StudySession) -> None:
        nonlocal manual_open, editing_session, original_period_texts
        if busy or not ready or manual_saving or history_loading or delete_dialog is not None:
            return
        editing_session = record
        start = record.started_at.astimezone()
        manual_date.value, manual_time.value = start.strftime("%d/%m/%Y"), start.strftime("%H:%M")
        manual_duration.value = format_duration(record.duration_seconds)[:-3]
        original_period_texts = (manual_date.value, manual_time.value, manual_duration.value)
        manual_subject.value, manual_study_type.value = record.subject_id, record.study_type
        types = list(STUDY_TYPES)
        if record.study_type not in types:
            types.append(record.study_type)
        manual_study_type.options = [ft.DropdownOption(key=name, text=name) for name in types]
        manual_topic.value = record.topic or ""
        manual_message.value = ""
        manual_open = True
        preview_manual_end()

    def cancel_manual(event) -> None:
        nonlocal manual_open, editing_session, original_period_texts
        if not manual_saving:
            manual_open = False
            editing_session, original_period_texts = None, None
            render()

    async def save_manual(event) -> None:
        nonlocal busy, manual_saving, manual_open, ready, editing_session, original_period_texts
        if busy or not ready or manual_saving:
            return
        if not manual_subject.value:
            manual_message.value = "Cadastre e escolha uma matéria para a sessão."
            render()
            return
        target = editing_session
        timing_changes = {}
        try:
            if target:
                if target.source == "manual":
                    start, seconds = form_period()
                    original_date, original_time, original_duration = original_period_texts
                    if (manual_date.value, manual_time.value) != (original_date, original_time):
                        timing_changes["started_at"] = start
                    if manual_duration.value != original_duration:
                        timing_changes["duration_seconds"] = seconds
            else:
                start, seconds = form_period()
        except ValueError as error:
            manual_message.value = str(error)
            render()
            return
        busy = manual_saving = True
        saved = False
        render()
        async with state_lock:
            try:
                if target:
                    result = await asyncio.to_thread(
                        update_finished_session, target, manual_subject.value, manual_study_type.value,
                        manual_topic.value, **timing_changes,
                    )
                else:
                    result = await asyncio.to_thread(
                        create_manual_session, manual_subject.value, manual_study_type.value,
                        start, seconds, manual_topic.value,
                    )
                saved = True
                manual_open = False
                editing_session, original_period_texts = None, None
                action = "Alterações salvas" if target else "Registro manual salvo"
                message.value = f"{action}. Tempo de estudo: {format_duration(result.duration_seconds)}."
                await refresh_state()
            except ValueError as error:
                if saved:
                    ready = False
                    action = "Alterações salvas" if target else "Registro salvo"
                    message.value = f"{action}, mas a tela não pôde ser atualizada: {error}"
                else:
                    manual_message.value = str(error)
            except (sqlite3.Error, OSError):
                if saved:
                    ready = False
                    action = "Alterações salvas" if target else "Registro salvo"
                    message.value = f"{action}, mas o total não pôde ser atualizado."
                else:
                    manual_message.value = "Não foi possível salvar. Tente novamente."
            finally:
                busy = manual_saving = False
                render()
        if saved:
            await load_history()

    save_button = ft.Button("Salvar matéria", on_click=save_subject)
    rename_save_button = ft.Button("Salvar nome", on_click=save_subject_name)
    rename_cancel_button = ft.TextButton("Cancelar renomeação", on_click=cancel_subject_rename)
    start_button = ft.Button("Iniciar", on_click=start)
    pause_button = ft.Button("Pausar", on_click=pause)
    resume_button = ft.Button("Retomar", on_click=resume)
    next_button = ft.Button("Iniciar próxima fase", on_click=next_phase)
    finish_button = ft.Button("Finalizar", on_click=finish)
    test_notice_button = ft.TextButton("Testar aviso do Windows", on_click=test_notice)
    open_session_button = ft.TextButton("Abrir sessão em andamento", on_click=lambda event: show_timer(event))
    history_refresh_button = ft.TextButton("Atualizar", on_click=refresh_history)
    history_previous_button = ft.TextButton("Mais recentes", on_click=previous_history_page)
    history_next_button = ft.TextButton("Mais antigas", on_click=next_history_page)
    manual_open_button = ft.TextButton("Adicionar sessão", on_click=open_manual)
    manual_save_button = ft.Button("Salvar registro manual", on_click=save_manual)
    manual_cancel_button = ft.TextButton("Cancelar", on_click=cancel_manual)
    for field in (manual_date, manual_time, manual_duration):
        field.on_change = preview_manual_end
    subject_choice.on_select = choose_subject
    mode_choice.on_select = preset_choice.on_select = choose_mode_or_preset
    for field in custom_fields:
        field.on_change = choose_mode_or_preset

    rename_panel = ft.Column(
        [ft.Text("Renomear matéria", size=20), rename_context, rename_name, rename_message,
         ft.Row([rename_save_button, rename_cancel_button], wrap=True)],
        visible=False, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    subjects_panel = ft.Column(
        [ft.Text("Minhas matérias", size=20), name_field, save_button, rename_panel, subjects],
        expand=True, visible=False, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    home_panel = ft.Column(
        [
            ft.Text("Tempo estudado hoje", size=20), daily_date, daily_total,
            ft.Text("Somando todas as matérias. Pausas e intervalos não contam."),
            ft.Text("Sessão em andamento", size=20), home_session, open_session_button,
            ft.Text("Minhas matérias", size=20), home_subjects,
        ],
        expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    timer_panel = ft.Column(
        [
            ft.Text("Sessão de estudo", size=20),
            elapsed, timer_status, study_total, cycle_info, session_details,
            subject_choice, study_type, topic, mode_choice, preset_choice, custom_panel,
            ft.Row([start_button, pause_button, resume_button, next_button, finish_button], wrap=True),
            timer_note, notification_hint, test_notice_button,
        ],
        expand=True, visible=False, scroll=ft.ScrollMode.AUTO,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    manual_timing_row = ft.Row([manual_date, manual_time, manual_duration], wrap=True)
    manual_panel = ft.Column(
        [
            manual_title, manual_subject, manual_study_type, manual_topic,
            manual_timing_row, manual_hint,
            manual_end, manual_message, ft.Row([manual_save_button, manual_cancel_button], wrap=True),
        ],
        expand=True, visible=False, scroll=ft.ScrollMode.AUTO,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    history_toolbar = ft.Row(
        [manual_open_button, history_refresh_button, history_previous_button, history_page_label,
         history_next_button], wrap=True,
    )
    history_panel = ft.Column(
        [
            ft.Text("Histórico de sessões", size=20), history_status,
            history_toolbar, manual_panel, history_list,
        ],
        expand=True, visible=False, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    def show_subjects(event) -> None:
        history_panel.visible = False
        home_panel.visible, subjects_panel.visible, timer_panel.visible = False, True, False
        page.update()

    def show_timer(event) -> None:
        history_panel.visible = False
        home_panel.visible, subjects_panel.visible, timer_panel.visible = False, False, True
        page.update()

    def show_home(event) -> None:
        history_panel.visible = False
        home_panel.visible, subjects_panel.visible, timer_panel.visible = True, False, False
        page.update()

    async def show_history(event) -> None:
        home_panel.visible = subjects_panel.visible = timer_panel.visible = False
        history_panel.visible = True
        if manual_open:
            render()
        else:
            await load_history()

    history_nav_button = ft.TextButton("Histórico", on_click=show_history)

    page.add(ft.SafeArea(
        content=ft.Column(
            [
                ft.Text("Streakly", size=28),
                ft.Row([
                    ft.TextButton("Início", on_click=show_home),
                    ft.TextButton("Matérias", on_click=show_subjects),
                    ft.TextButton("Cronômetro", on_click=show_timer),
                    history_nav_button,
                ], wrap=True),
                message, home_panel, subjects_panel, timer_panel, history_panel,
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
        if not session.pomodoro or session.pomodoro.state != "ready":
            message.value = "Sessão recuperada com os horários salvos."
    render()

    async def update_timer() -> None:
        nonlocal ready
        while True:
            # No Windows, observar prazos também com a janela minimizada.
            # As atualizações visuais continuam restritas à janela visível.
            if not notifications_available:
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
        nonlocal ticker, connected
        connected = True
        if ticker is None or ticker.done():
            ticker = page.run_task(update_timer)

    def stop_updates(event=None) -> None:
        nonlocal connected, delete_dialog
        connected = False
        if delete_dialog:
            delete_dialog.open = False
            delete_dialog = None
        if ticker:
            ticker.cancel()
        for task in tuple(pending_notices):
            task.cancel()

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
