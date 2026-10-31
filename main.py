import asyncio
import calendar
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
    CalendarDay,
    DailyGoalOverride,
    DailyPlan,
    DefaultDailyGoal,
    MonthlyCalendar,
    PeriodStatistics,
    StreakBase,
    add_subject,
    create_manual_session,
    clear_daily_goal_override,
    delete_finished_session,
    delete_subject,
    finish_session,
    get_subject_timer_settings,
    initialize_database,
    list_recorded_study_types,
    pause_session,
    read_session_history,
    read_daily_plan,
    read_monthly_calendar,
    read_period_statistics,
    read_study_overview,
    read_subject_deletion_preview,
    rename_subject,
    resume_session,
    set_default_daily_goal,
    set_daily_goal_override,
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
CUSTOM_STUDY_TYPE_KEY = "custom"
MONTH_NAMES = ("Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
               "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro")


def resolve_study_type(choice: str | None, custom_name: str | None) -> str:
    """Separa a opção da interface do nome que será salvo na sessão."""
    if choice == CUSTOM_STUDY_TYPE_KEY:
        name = (custom_name or "").strip()
        if not name:
            raise ValueError("Digite o nome do tipo de estudo personalizado.")
        return name
    if isinstance(choice, str) and choice.startswith("type:"):
        name = choice.removeprefix("type:")
        if name.strip():
            return name
    raise ValueError("Escolha um tipo de estudo.")


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


def parse_plan_date(text: str) -> date:
    """Lê uma data do calendário sem reinterpretá-la como um instante UTC."""
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y").date()
    except ValueError:
        raise ValueError("Informe a data como DD/MM/AAAA.") from None


def statistics_period(kind: str, today: date, start_text: str = "", end_text: str = "") -> tuple[date, date]:
    """Resolve o filtro sem transformar datas locais em horários."""
    if kind == "today":
        return today, today
    if kind == "week":
        return today - timedelta(days=today.weekday()), today
    if kind == "month":
        return today.replace(day=1), today
    if kind == "year":
        return today.replace(month=1, day=1), today
    if kind == "custom":
        start, end = parse_plan_date(start_text), parse_plan_date(end_text)
        if end < start:
            raise ValueError("A data final precisa ser igual ou posterior à inicial.")
        return start, end
    raise ValueError("Escolha um período para consultar as estatísticas.")


async def main(page: ft.Page) -> None:
    page.title = "Streakly — prova de conceito"
    page.padding = 16

    session: StudySession | None = None
    subject_options: list[tuple[str, str]] = []
    recorded_study_types: list[str] = []
    settings_subject: str | None = None
    subject_selection_removed = False
    today: date | None = None
    today_seconds = 0.0
    daily_goal: DefaultDailyGoal | None = None
    goal_expected: DefaultDailyGoal | None = None
    goal_edit_day: date | None = None
    goal_original_text = ""
    goal_editing = False
    goal_saving = False
    today_override: DailyGoalOverride | None = None
    date_plan: DailyPlan | None = None
    date_editing = False
    date_saving = False
    date_loading = False
    date_request: object | None = None
    date_original_text = ""
    date_original_seconds: int | None = None
    streak_base: StreakBase | None = None
    streak_zone_key: tuple[date, str | None, timedelta | None] | None = None
    month_data: MonthlyCalendar | None = None
    month_dirty = True
    month_loading = False
    month_request: object | None = None
    statistics_data: PeriodStatistics | None = None
    statistics_kind = "month"
    statistics_dirty = True
    statistics_loading = False
    statistics_request: object | None = None
    statistics_fields_initialized = False
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
    subject_delete_request: object | None = None
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
    goal_summary = ft.Text()
    goal_progress = ft.ProgressBar(value=0, visible=False)
    goal_remaining = ft.Text()
    goal_settings_status = ft.Text()
    goal_duration = ft.TextField(label="Meta diária padrão (HH:MM)", hint_text="Ex.: 03:00")
    goal_message = ft.Text()
    streak_current = ft.Text("Ofensiva indisponível.", size=18, weight=ft.FontWeight.BOLD)
    streak_record = ft.Text("Recorde indisponível.")
    streak_hint = ft.Text()
    plan_date = ft.TextField(label="Data do planejamento (DD/MM/AAAA)", width=260)
    plan_status = ft.Text("Consulte uma data para ver seu planejamento.")
    plan_mode = ft.Dropdown(
        label="Planejamento da data", value="default",
        options=[ft.DropdownOption(key="default", text="Usar meta padrão"),
                 ft.DropdownOption(key="custom", text="Meta específica"),
                 ft.DropdownOption(key="rest", text="Dia de descanso")],
    )
    plan_duration = ft.TextField(label="Meta da data (HH:MM)", hint_text="Ex.: 02:00")
    plan_message = ft.Text()
    month_title = ft.Text("Calendário mensal", size=20)
    month_status = ft.Text("O mês ainda não foi consultado.")
    month_error = ft.Text()
    month_grid = ft.Column(spacing=4)
    statistics_choice = ft.Dropdown(
        label="Período das estatísticas", value="month",
        options=[ft.DropdownOption(key=key, text=label) for key, label in (
            ("today", "Hoje"), ("week", "Semana atual (segunda até hoje)"),
            ("month", "Mês atual (até hoje)"), ("year", "Ano atual (até hoje)"),
            ("custom", "Personalizado"),
        )],
    )
    statistics_start = ft.TextField(label="Data inicial (DD/MM/AAAA)", width=260)
    statistics_end = ft.TextField(label="Data final (DD/MM/AAAA)", width=260)
    statistics_status = ft.Text("As estatísticas ainda não foram consultadas.")
    statistics_error = ft.Text()
    statistics_range = ft.Text()
    statistics_total = ft.Text("—", size=32)
    statistics_average = ft.Text("—", size=32)
    statistics_days = ft.Text()
    statistics_stamp = ft.Text(size=12)
    statistics_subjects = ft.Column(
        [ft.Text("Consulte um período para ver o tempo por matéria.")], spacing=8,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
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
        label="Tipo de estudo da sessão no histórico", value="type:Leitura",
    )
    manual_custom_type = ft.TextField(label="Nome do tipo personalizado no histórico", visible=False)
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
        label="Tipo de estudo", value="type:Leitura",
    )
    custom_study_type = ft.TextField(label="Nome do tipo de estudo personalizado", visible=False)
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

        async def choose_delete(event) -> None:
            await request_subject_delete(subject_id)

        return ft.Row([
            ft.Text(name, expand=True), ft.TextButton("Renomear", on_click=choose_rename),
            ft.TextButton("Excluir", on_click=choose_delete),
        ])

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

    def fill_study_type_options(dropdown: ft.Dropdown) -> None:
        names = list(dict.fromkeys([*STUDY_TYPES, *recorded_study_types]))
        # Uma atualização das sugestões não deve substituir uma seleção do rascunho.
        if dropdown.value and dropdown.value.startswith("type:"):
            selected = dropdown.value.removeprefix("type:")
            if selected not in names:
                names.append(selected)
        options = [(f"type:{name}", name) for name in names]
        options.append((CUSTOM_STUDY_TYPE_KEY, "Personalizado…"))
        if [(item.key, item.text) for item in dropdown.options] != options:
            dropdown.options = [ft.DropdownOption(key=key, text=name) for key, name in options]

    async def refresh_state(*, reload_study_types: bool = False, reload_streak: bool = False,
                            reload_statistics: bool = False) -> None:
        nonlocal session, subject_options, settings_subject, ready, today, today_seconds
        nonlocal subject_selection_removed, recorded_study_types, daily_goal, today_override
        nonlocal streak_base, streak_zone_key
        nonlocal month_dirty, statistics_dirty
        previous = session
        moment = datetime.now(UTC)
        local_moment = moment.astimezone()
        zone_key = (local_moment.date(), local_moment.tzname(), local_moment.utcoffset())
        zone_changed = streak_zone_key is not None and streak_zone_key != zone_key
        if zone_changed:
            month_dirty = True
            statistics_dirty = True
        need_streak = reload_streak or streak_base is None or streak_zone_key != zone_key
        if need_streak:
            streak_base = None
            streak_zone_key = None
        overview = await asyncio.to_thread(read_study_overview, at=moment, include_streak_base=need_streak)
        if need_streak:
            if overview.streak_base is None:
                raise ValueError("Não foi possível calcular a ofensiva.")
            streak_base, streak_zone_key = overview.streak_base, zone_key
        if reload_study_types:
            recorded_study_types = await asyncio.to_thread(list_recorded_study_types)
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
        valid_subjects = {subject_id for subject_id, _ in choices}
        if manual_subject.value not in valid_subjects:
            if manual_open:
                if manual_subject.value is not None:
                    manual_subject.value = None
                    manual_subject.error_text = "A matéria selecionada foi excluída. Escolha outra."
            else:
                manual_subject.value = choices[0][0] if choices else None
                manual_subject.error_text = None
        else:
            manual_subject.error_text = None
        if editing_session and editing_session.subject_id not in valid_subjects:
            manual_message.value = "A matéria original foi excluída. Cancele a edição e atualize o Histórico."
        if subject_choice.value not in valid_subjects:
            if subject_choice.value is not None:
                subject_selection_removed = True
                subject_choice.value = None
                subject_choice.error_text = "A matéria selecionada foi excluída. Escolha outra."
            elif not subject_selection_removed:
                subject_choice.value = choices[0][0] if choices else None
        else:
            subject_selection_removed = False
            subject_choice.error_text = None
        if not active and subject_choice.value and settings_subject != subject_choice.value:
            settings = await asyncio.to_thread(get_subject_timer_settings, subject_choice.value)
            mode_choice.value = settings.mode
            fill_pomodoro_fields(settings)
            settings_subject = subject_choice.value
        session = active
        today, today_seconds = overview.day, overview.total_seconds
        daily_goal = overview.goal
        today_override = overview.goal_override
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
        if calendar_panel.visible and month_dirty and (reload_streak or zone_changed):
            await read_month_view(month_data.year if month_data else today.year,
                                  month_data.month if month_data else today.month)
        if statistics_panel.visible and statistics_dirty and (reload_streak or zone_changed or reload_statistics):
            await read_statistics_view(statistics_kind, *applied_statistics_period())

    def render() -> None:
        active = session is not None
        locked = busy or not ready
        if session:
            subject_choice.value = session.subject_id
            study_type.value = f"type:{session.study_type}"
            topic.value = session.topic or ""
            mode_choice.value = session.mode
            if session.pomodoro:
                fill_pomodoro_fields(session.pomodoro)
        is_pomodoro = mode_choice.value == "pomodoro"
        phase = session.pomodoro if session else None
        waiting = phase is not None and phase.state == "ready"
        for dropdown in (study_type, manual_study_type):
            fill_study_type_options(dropdown)
        custom_study_type.visible = study_type.value == CUSTOM_STUDY_TYPE_KEY
        custom_study_type.disabled = locked or active
        manual_custom_type.visible = manual_study_type.value == CUSTOM_STUDY_TYPE_KEY

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
        effective_seconds = (today_override.duration_seconds if today_override else
                             daily_goal.duration_seconds if daily_goal else None)
        is_rest_day = today_override is not None and today_override.is_rest_day
        goal_progress.visible = ready and effective_seconds is not None and not is_rest_day
        if not ready:
            goal_summary.value = goal_settings_status.value = "Meta indisponível."
            goal_remaining.value = ""
        else:
            goal_settings_status.value = (f"Meta padrão vigente: {format_duration(daily_goal.duration_seconds)}"
                                          if daily_goal else "Meta padrão ainda não configurada.")
            if is_rest_day:
                goal_summary.value = "Dia de descanso hoje."
                goal_remaining.value = "Você pode registrar estudos normalmente."
            elif effective_seconds is None:
                goal_summary.value = "Meta diária ainda não configurada."
                goal_remaining.value = ""
            else:
                label = "Meta específica de hoje" if today_override else "Meta de hoje"
                goal_summary.value = f"{label}: {format_duration(effective_seconds)}"
                goal_progress.value = min(1.0, today_seconds / effective_seconds)
                remaining = max(0, effective_seconds - today_seconds)
                goal_remaining.value = (f"Faltam {format_duration(ceil(remaining))} para atingir a meta."
                                        if remaining else "Meta atingida hoje!")
        streak_refresh_button.disabled = busy
        if not ready or streak_base is None or streak_base.day != today:
            streak_current.value = "Ofensiva indisponível."
            streak_record.value = "Recorde indisponível."
            streak_hint.value = ""
        else:
            today_counts = not is_rest_day and effective_seconds is not None and today_seconds >= effective_seconds
            current = streak_base.current_before_today + int(today_counts)
            best = max(streak_base.best_before_today, current)
            streak_current.value = f"🔥 Ofensiva atual: {current} {'dia' if current == 1 else 'dias'}"
            streak_record.value = f"Recorde: {best} {'dia' if best == 1 else 'dias'}"
            if is_rest_day:
                streak_hint.value = "Descanso mantém a sequência sem acrescentar um dia."
            elif today_counts:
                streak_hint.value = "Hoje já conta para a ofensiva."
            elif effective_seconds is not None:
                streak_hint.value = "Hoje ainda está em andamento. Atinja a meta para acrescentar um dia."
            else:
                streak_hint.value = "Dias sem meta não acrescentam nem interrompem a sequência."
        goal_settings_button.disabled = goal_edit_button.disabled = locked or delete_dialog is not None
        goal_edit_button.visible = not goal_editing
        goal_form.visible = goal_editing
        goal_duration.disabled = locked or goal_saving
        goal_save_button.disabled = locked or goal_saving or delete_dialog is not None or goal_edit_day != today
        goal_cancel_button.disabled = goal_saving
        if goal_editing and goal_edit_day != today:
            goal_message.value = "O dia mudou. Cancele e abra novamente a configuração da meta."
        date_matches = False
        if date_plan:
            try:
                date_matches = parse_plan_date(plan_date.value or "") == date_plan.day
            except ValueError:
                pass
            stamp = date_plan.day.strftime("%d/%m/%Y")
            if date_plan.is_rest_day:
                plan_status.value = f"{stamp}: dia de descanso."
            elif date_plan.effective_seconds is not None:
                source = "Meta específica" if date_plan.override else "Meta padrão vigente"
                plan_status.value = f"{stamp} · {source}: {format_duration(date_plan.effective_seconds)}"
            else:
                plan_status.value = f"{stamp}: nenhuma meta configurada."
        else:
            plan_status.value = "Consulte uma data para ver seu planejamento."
        plan_form.visible = date_editing
        plan_edit_button.visible = date_plan is not None and not date_editing
        plan_edit_button.disabled = locked or not date_matches or delete_dialog is not None
        plan_query_button.disabled = locked or date_loading or date_saving or delete_dialog is not None
        plan_date.disabled = locked or date_loading or date_saving or delete_dialog is not None
        plan_mode.disabled = plan_duration.disabled = locked or date_saving or delete_dialog is not None
        plan_duration.visible = plan_mode.value == "custom"
        plan_save_button.disabled = (locked or date_saving or history_loading or not date_matches
                                     or delete_dialog is not None or not date_editing)
        plan_cancel_button.disabled = date_saving or delete_busy
        plan_date.error_text = ("A data foi alterada. Consulte a data informada antes de salvar."
                                if date_editing and not date_matches else None)
        month_locked = locked or month_loading or delete_dialog is not None
        month_previous_button.disabled = month_locked or month_data is None or (month_data.year, month_data.month) == (1, 1)
        month_next_button.disabled = month_locked or month_data is None or (month_data.year, month_data.month) == (9999, 12)
        month_current_button.disabled = month_refresh_button.disabled = month_locked
        if month_loading:
            month_status.value = "Carregando mês…"
        elif month_data is None:
            month_status.value = "O mês ainda não foi consultado. Use Mês atual ou Atualizar mês."
        elif month_dirty:
            month_status.value = "Os dados do mês precisam de atualização. Use Atualizar mês."
        else:
            month_status.value = "Mês consultado. Use Atualizar mês para ver o tempo e alterações mais recentes."
        for row in month_grid.controls:
            for cell in row.controls:
                if isinstance(cell, ft.Button):
                    cell.disabled = month_locked
        statistics_locked = locked or statistics_loading or delete_dialog is not None
        statistics_choice.disabled = statistics_start.disabled = statistics_end.disabled = statistics_locked
        statistics_apply_button.disabled = statistics_refresh_button.disabled = statistics_locked
        statistics_custom_row.visible = statistics_choice.value == "custom"
        if statistics_loading:
            statistics_status.value = "Carregando estatísticas…"
        elif statistics_data is None:
            statistics_status.value = "As estatísticas ainda não foram consultadas. Use Aplicar período."
        elif statistics_dirty:
            statistics_status.value = "Os resultados precisam de atualização. Use Atualizar estatísticas."
        else:
            statistics_status.value = "Resultados da última consulta. Use Aplicar período para consultar os filtros preenchidos."
        if statistics_data is not None:
            result = statistics_data
            statistics_range.value = f"Período aplicado: {result.start_day:%d/%m/%Y} a {result.end_day:%d/%m/%Y}"
            statistics_total.value = format_duration(result.total_seconds)
            statistics_average.value = format_duration(result.average_seconds)
            statistics_days.value = f"Média dividida por {result.day_count} {'dia' if result.day_count == 1 else 'dias'}, incluindo dias sem estudo."
            statistics_stamp.value = f"Consultado em {result.as_of.astimezone():%d/%m/%Y às %H:%M:%S}."
        history_locked = locked or history_loading or delete_dialog is not None
        timing_editable = editing_session is None or editing_session.source == "manual"
        history_nav_button.disabled = history_loading or manual_saving or delete_dialog is not None
        if delete_dialog:
            delete_dialog.actions[0].disabled = delete_busy
            delete_dialog.actions[1].disabled = locked or delete_busy
        manual_open_button.disabled = history_locked or not subject_options
        editing_subject_removed = (editing_session is not None
                                   and editing_session.subject_id not in dict(subject_options))
        manual_save_button.disabled = locked or manual_saving or not subject_options or editing_subject_removed
        manual_cancel_button.disabled = manual_saving
        manual_save_button.content = "Salvar alterações" if editing_session else "Salvar registro manual"
        manual_title.value = "Editar sessão" if editing_session else "Adicionar sessão manual"
        manual_timing_row.visible = timing_editable
        manual_hint.value = (
            "Informe apenas o tempo estudado. Cada registro representa um período contínuo, sem pausas."
            if timing_editable else
            "Os horários e as pausas registrados pelo cronômetro serão preservados."
        )
        for field in (manual_subject, manual_study_type, manual_custom_type, manual_topic,
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

    def invalidate_streak_cache() -> None:
        nonlocal streak_base, streak_zone_key, month_dirty, statistics_dirty
        streak_base = None
        streak_zone_key = None
        month_dirty = True
        statistics_dirty = True

    def applied_statistics_period() -> tuple[date, date]:
        if statistics_kind == "custom" and statistics_data is not None:
            return statistics_data.start_day, statistics_data.end_day
        return statistics_period(statistics_kind, datetime.now(UTC).astimezone().date())

    def statistics_subject_rows(result: PeriodStatistics) -> list[ft.Control]:
        """Monta a lista apenas quando chega uma nova consulta do período."""
        if not result.subjects:
            return [ft.Text("Nenhum estudo neste período.")]
        ids_by_name: dict[str, list[str]] = {}
        for item in result.subjects:
            ids_by_name.setdefault(item.subject_name, []).append(item.subject_id)
        rows = []
        for item in result.subjects:
            label = item.subject_name
            duplicate_name = len(ids_by_name[label]) > 1
            if duplicate_name:
                others = [key for key in ids_by_name[label] if key != item.subject_id]
                prefix_length = 8
                while any(other.startswith(item.subject_id[:prefix_length]) for other in others):
                    prefix_length += 1
                label += f" ({item.subject_id[:prefix_length]})"
            rows.append(ft.Row(
                [ft.Text(label, expand=True,
                         tooltip=f"Identificador da matéria: {item.subject_id}" if duplicate_name else None),
                 ft.Text(format_duration(item.total_seconds))],
                data=item.subject_id, spacing=12,
            ))
        return rows

    async def read_statistics_view(kind: str, start: date, end: date) -> None:
        nonlocal statistics_request, statistics_loading, statistics_data, statistics_kind, statistics_dirty
        request = object()
        statistics_request = request
        statistics_loading = True
        render()
        try:
            result = await asyncio.to_thread(read_period_statistics, start, end, at=datetime.now(UTC))
            if statistics_request is request and connected:
                rows = statistics_subject_rows(result)
                statistics_data, statistics_kind = result, kind
                statistics_subjects.controls = rows
                statistics_dirty = False
                statistics_error.value = ""
        except (sqlite3.Error, OSError, ValueError, OverflowError):
            if statistics_request is request:
                statistics_error.value = "Não foi possível consultar as estatísticas. Os resultados anteriores foram mantidos."
        finally:
            if statistics_request is request:
                statistics_request = None
            statistics_loading = False
            render()

    async def load_statistics(kind: str, start: date, end: date) -> None:
        nonlocal busy
        if busy or not ready or statistics_loading or delete_dialog is not None:
            return
        busy = True
        render()
        async with state_lock:
            try:
                await read_statistics_view(kind, start, end)
            finally:
                busy = False
                render()

    async def apply_statistics(event) -> None:
        if busy or not ready or delete_dialog is not None:
            return
        try:
            kind = statistics_choice.value
            start, end = statistics_period(kind, datetime.now(UTC).astimezone().date(),
                                           statistics_start.value or "", statistics_end.value or "")
        except ValueError as error:
            statistics_error.value = str(error)
            render()
            return
        await load_statistics(kind, start, end)

    async def refresh_statistics(event=None) -> None:
        await load_statistics(statistics_kind, *applied_statistics_period())

    def month_day_button(item: CalendarDay, displayed: MonthlyCalendar) -> ft.Button:
        target = item.plan.effective_seconds
        if item.plan.is_rest_day:
            status, background = "Descanso", ft.Colors.BLUE_900
        elif item.day > displayed.today:
            status, background = ("Planejado" if target is not None else "Sem meta"), ft.Colors.GREY_800
        elif target is not None and item.study_seconds >= target:
            status, background = "Meta atingida", ft.Colors.GREEN_900
        elif target is not None:
            status = "Em andamento" if item.day == displayed.today else "Abaixo da meta"
            background = ft.Colors.GREY_800 if item.day == displayed.today else ft.Colors.ORANGE_900
        else:
            status, background = ("Estudado" if item.study_seconds else "Sem meta"), ft.Colors.GREY_800

        async def select_day(event) -> None:
            if (busy or not ready or not connected or not calendar_panel.visible
                    or month_data is not displayed or delete_dialog is not None):
                return
            if date_editing:
                if date_plan is not None and date_plan.day == item.day:
                    return
                plan_message.value = "Cancele o planejamento em edição antes de abrir outro dia."
                render()
                return
            plan_date.value = item.day.strftime("%d/%m/%Y")
            await query_date_plan()

        goal_text = format_duration(target) if target is not None else "sem meta"
        day_label = f"{item.day.day} *" if item.day == displayed.today else str(item.day.day)
        return ft.Button(
            content=ft.Column(
                [ft.Text(day_label, size=13, weight=ft.FontWeight.BOLD),
                 ft.Text(status, size=10, max_lines=2, text_align=ft.TextAlign.CENTER),
                 ft.Text(format_duration(item.study_seconds), size=10, max_lines=1)],
                spacing=2, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            data=item.day.isoformat(), expand=1, height=86, on_click=select_day,
            tooltip=f"{item.day:%d/%m/%Y} · {status}. Estudo: {format_duration(item.study_seconds)}. Meta: {goal_text}.",
            style=ft.ButtonStyle(
                padding=3, bgcolor=background, color=ft.Colors.WHITE,
                shape=ft.RoundedRectangleBorder(radius=6),
                side=ft.BorderSide(width=2 if item.day == displayed.today else 0,
                                   color=ft.Colors.PRIMARY),
            ),
        )

    def install_month(result: MonthlyCalendar) -> None:
        nonlocal month_data, month_dirty
        rows = []
        for week in calendar.Calendar(firstweekday=calendar.MONDAY).monthdayscalendar(result.year, result.month):
            rows.append(ft.Row(
                [month_day_button(result.days[number - 1], result) if number else ft.Container(expand=1, height=86)
                 for number in week], spacing=4,
            ))
        month_grid.controls = rows
        month_data, month_dirty = result, False
        month_title.value = f"{MONTH_NAMES[result.month - 1]} de {result.year}"
        month_error.value = ""

    async def read_month_view(year: int, month: int) -> None:
        nonlocal month_request, month_loading
        request = object()
        month_request = request
        month_loading = True
        render()
        try:
            result = await asyncio.to_thread(read_monthly_calendar, year, month, at=datetime.now(UTC))
            if month_request is request and connected:
                install_month(result)
        except (sqlite3.Error, OSError, ValueError):
            if month_request is request:
                month_error.value = "Não foi possível carregar o mês. A visualização anterior foi mantida. Tente Atualizar mês."
        finally:
            if month_request is request:
                month_request = None
            month_loading = False
            render()

    async def load_month(year: int, month: int) -> None:
        nonlocal busy
        if (busy or not ready or month_loading or delete_dialog is not None
                or not 1 <= year <= 9999 or not 1 <= month <= 12):
            return
        busy = True
        render()
        async with state_lock:
            try:
                await read_month_view(year, month)
            finally:
                busy = False
                render()

    async def change_month(offset: int) -> None:
        if month_data is None:
            return
        index = month_data.year * 12 + month_data.month - 1 + offset
        year, month = divmod(index, 12)
        await load_month(year, month + 1)

    async def previous_month(event) -> None:
        await change_month(-1)

    async def next_month(event) -> None:
        await change_month(1)

    async def current_month(event) -> None:
        if today:
            await load_month(today.year, today.month)

    async def refresh_month(event) -> None:
        if month_data:
            await load_month(month_data.year, month_data.month)
        else:
            await current_month(event)

    async def refresh_streak_summary(event=None) -> None:
        nonlocal busy, ready
        if busy:
            return
        busy = True
        render()
        async with state_lock:
            try:
                await refresh_state(reload_streak=True)
            except (sqlite3.Error, OSError, ValueError):
                ready = False
                message.value = "Não foi possível atualizar a ofensiva. Tente novamente."
            finally:
                busy = False
                render()

    def fill_date_editor(event=None) -> None:
        nonlocal date_editing, date_original_text, date_original_seconds
        if busy or not ready or date_plan is None or delete_dialog is not None:
            return
        try:
            if parse_plan_date(plan_date.value or "") != date_plan.day:
                return
        except ValueError:
            return
        override = date_plan.override
        plan_mode.value = "rest" if override and override.is_rest_day else "custom" if override else "default"
        date_original_seconds = (override.duration_seconds if override and not override.is_rest_day else
                                 date_plan.default_goal.duration_seconds if date_plan.default_goal else None)
        date_original_text = format_duration(date_original_seconds)[:-3] if date_original_seconds else ""
        plan_duration.value = date_original_text
        plan_message.value = ""
        date_editing = True
        render()

    async def query_date_plan(event=None) -> None:
        nonlocal busy, ready, date_loading, date_request, date_plan
        if busy or not ready or date_loading or date_saving or delete_dialog is not None:
            return
        try:
            selected_day = parse_plan_date(plan_date.value or "")
        except ValueError as error:
            plan_message.value = str(error)
            render()
            return
        request = object()
        date_request = request
        busy = date_loading = True
        result = None
        render()
        async with state_lock:
            try:
                result = await asyncio.to_thread(read_daily_plan, selected_day)
            except (sqlite3.Error, OSError, ValueError):
                plan_message.value = "Não foi possível consultar o planejamento. Tente novamente."
            finally:
                current_request = date_request is request
                if current_request:
                    date_request = None
                busy = date_loading = False
                render()
        if result is not None and current_request and connected:
            date_plan = result
            plan_date.value = selected_day.strftime("%d/%m/%Y")
            fill_date_editor()
            if month_data is None or (month_data.year, month_data.month) != (selected_day.year, selected_day.month):
                await load_month(selected_day.year, selected_day.month)

    def cancel_date_editor(event) -> None:
        nonlocal date_editing, date_request
        if not date_saving and not delete_busy:
            date_request = None
            date_editing = False
            plan_message.value = ""
            render()

    async def save_date_plan(event) -> None:
        nonlocal busy, ready, date_saving, date_editing, date_plan
        if (busy or not ready or date_saving or history_loading or not date_editing or date_plan is None
                or delete_dialog is not None):
            return
        selected = date_plan
        try:
            if parse_plan_date(plan_date.value or "") != selected.day:
                raise ValueError("A data foi alterada. Consulte a data informada antes de salvar.")
            mode = plan_mode.value
            if mode not in ("default", "custom", "rest"):
                raise ValueError("Escolha o planejamento da data.")
            seconds = None
            if mode == "custom":
                seconds = (date_original_seconds
                           if date_original_seconds and plan_duration.value == date_original_text
                           else parse_study_duration(plan_duration.value or ""))
        except ValueError as error:
            plan_message.value = str(error)
            render()
            return

        if mode == "default" and selected.override is not None:
            def clear_selected(expected: DailyGoalOverride) -> None:
                clear_daily_goal_override(expected.day, expected)

            async def update_cleared_plan() -> None:
                nonlocal date_plan, date_editing
                date_editing = False
                date_plan = None
                plan_message.value = ""
                date_plan = await asyncio.to_thread(read_daily_plan, selected.day)

            show_delete_confirmation(
                "Voltar à meta padrão?",
                [ft.Text(f"Data: {selected.day:%d/%m/%Y}"),
                 ft.Text("A configuração específica desta data será removida."),
                 ft.Text("A data voltará a usar a meta padrão vigente. Seus estudos serão preservados.")],
                clear_selected, selected.override, "Configuração da data removida",
                "Não foi possível remover a configuração. Tente novamente.",
                confirm_label="Usar meta padrão", after_save=update_cleared_plan, reload_history=False,
            )
            return

        busy = date_saving = True
        saved = False
        render()
        async with state_lock:
            try:
                if mode == "default":
                    await asyncio.to_thread(clear_daily_goal_override, selected.day, selected.override)
                else:
                    await asyncio.to_thread(set_daily_goal_override, selected.day, seconds, mode == "rest", selected.override)
                invalidate_streak_cache()
                saved = True
                date_editing = False
                date_plan = None
                message.value = "Planejamento da data salvo."
                date_plan = await asyncio.to_thread(read_daily_plan, selected.day)
                await refresh_state(reload_streak=True)
            except ValueError as error:
                if saved:
                    ready = False
                    message.value = f"Planejamento salvo, mas a tela não pôde ser atualizada: {error}"
                else:
                    plan_message.value = str(error)
            except (sqlite3.Error, OSError):
                if saved:
                    ready = False
                    message.value = "Planejamento salvo, mas a tela não pôde ser atualizada."
                else:
                    plan_message.value = "Não foi possível salvar o planejamento. Tente novamente."
            finally:
                busy = date_saving = False
                render()

    def open_goal_editor(event=None) -> None:
        nonlocal goal_editing, goal_expected, goal_edit_day, goal_original_text
        if busy or not ready or goal_saving or goal_editing or delete_dialog is not None:
            return
        goal_expected, goal_edit_day = daily_goal, today
        goal_original_text = format_duration(daily_goal.duration_seconds)[:-3] if daily_goal else ""
        goal_duration.value = goal_original_text
        goal_message.value = ""
        goal_editing = True
        render()

    def cancel_goal_editor(event) -> None:
        nonlocal goal_editing
        if not goal_saving:
            goal_editing = False
            render()

    async def save_goal(event) -> None:
        nonlocal busy, ready, goal_saving, goal_editing
        if (busy or not ready or goal_saving or not goal_editing or goal_edit_day is None
                or delete_dialog is not None):
            return
        try:
            seconds = (goal_expected.duration_seconds
                       if goal_expected and goal_duration.value == goal_original_text
                       else parse_study_duration(goal_duration.value or ""))
        except ValueError as error:
            goal_message.value = str(error)
            render()
            return
        busy = goal_saving = True
        saved = False
        render()
        async with state_lock:
            try:
                await asyncio.to_thread(
                    set_default_daily_goal, seconds, goal_expected, goal_edit_day, at=datetime.now(UTC),
                )
                invalidate_streak_cache()
                saved = True
                goal_editing = False
                message.value = "Meta diária salva."
                await refresh_state(reload_streak=True)
            except ValueError as error:
                if saved:
                    ready = False
                    message.value = f"Meta salva, mas a tela não pôde ser atualizada: {error}"
                else:
                    goal_message.value = str(error)
                    try:
                        await refresh_state()
                    except (sqlite3.Error, OSError, ValueError):
                        ready = False
            except (sqlite3.Error, OSError):
                if saved:
                    ready = False
                    message.value = "Meta salva, mas a tela não pôde ser atualizada."
                else:
                    goal_message.value = "Não foi possível salvar a meta. Tente novamente."
            finally:
                busy = goal_saving = False
                render()

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
        nonlocal busy, ready, renaming_subject, rename_saving, statistics_dirty
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
                statistics_dirty = True
                saved = True
                renaming_subject = None
                message.value = f"Matéria renomeada para {new_name}."
                await refresh_state(reload_statistics=True)
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
                invalidate_streak_cache()
                saved = True
                session = result if result.status != "finished" else None
                message.value = success_message
                if result.status == "finished":
                    message.value += f" Tempo de estudo: {format_duration(result.duration_seconds)}."
                await refresh_state(reload_study_types=True, reload_streak=True)
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
        try:
            selected_type = resolve_study_type(study_type.value, custom_study_type.value)
        except ValueError as error:
            message.value = str(error)
            render()
            return
        if mode_choice.value == "pomodoro":
            try:
                options.update(pomodoro_options())
            except ValueError as error:
                message.value = str(error)
                page.update()
                return
        await perform(
            start_session, "Sessão iniciada.",
            subject_choice.value, selected_type, topic.value, **options,
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

    def show_delete_confirmation(title: str, summary: list[ft.Control], action, expected,
                                 completed_text: str, failure_text: str, *,
                                 confirm_label: str = "Excluir definitivamente", after_save=None,
                                 reload_history: bool = True) -> None:
        nonlocal delete_dialog
        if (busy or not ready or history_loading or delete_busy
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
                    await asyncio.to_thread(action, expected)
                    invalidate_streak_cache()
                    deleted = True
                    close_delete_dialog(dialog)
                    message.value = f"{completed_text}."
                    if after_save:
                        await after_save()
                    await refresh_state(reload_study_types=True, reload_streak=True)
                except ValueError as error:
                    if deleted:
                        ready = False
                        message.value = f"{completed_text}, mas a tela não pôde ser atualizada: {error}"
                    elif delete_dialog is dialog:
                        error_message.value = str(error)
                    else:
                        message.value = str(error)
                except (sqlite3.Error, OSError):
                    if deleted:
                        ready = False
                        message.value = f"{completed_text}, mas o total não pôde ser atualizado."
                    elif delete_dialog is dialog:
                        error_message.value = failure_text
                    else:
                        message.value = failure_text
                finally:
                    busy = delete_busy = False
                    render()
            if deleted and reload_history:
                await load_history()

        dialog = ft.AlertDialog(
            modal=True, scrollable=True, title=ft.Text(title),
            content=ft.Column([*summary, error_message], tight=True),
            actions=[ft.TextButton("Cancelar", on_click=cancel_delete),
                     ft.Button(confirm_label, on_click=confirm_delete)],
            on_dismiss=dismissed,
        )
        delete_dialog = dialog
        page.show_dialog(dialog)
        render()

    def request_delete(record: StudySession) -> None:
        if manual_open:
            return
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
        summary.append(ft.Text("A exclusão é permanente e remove esta sessão do Histórico e dos totais."))
        show_delete_confirmation(
            "Excluir sessão?", summary, delete_finished_session, record,
            "Sessão excluída", "Não foi possível excluir a sessão. Tente novamente.",
        )

    async def request_subject_delete(subject_id: str) -> None:
        nonlocal busy, ready, subject_delete_request
        if (busy or not ready or history_loading or renaming_subject is not None
                or delete_dialog is not None or subject_delete_request is not None
                or not connected or not page.app_visible):
            return
        request = object()
        subject_delete_request = request
        preview = None
        busy = True
        render()
        async with state_lock:
            try:
                preview = await asyncio.to_thread(read_subject_deletion_preview, subject_id)
            except ValueError as error:
                message.value = str(error)
            except (sqlite3.Error, OSError):
                ready = False
                message.value = "Não foi possível consultar os estudos da matéria. Tente novamente."
            finally:
                current_request = subject_delete_request is request
                if current_request:
                    subject_delete_request = None
                busy = False
                render()
        if (preview is None or not current_request or not connected or not page.app_visible
                or not subjects_panel.visible):
            return
        summary = [
            ft.Text(preview.name, size=20, weight=ft.FontWeight.BOLD),
            ft.Text(f"Sessões que serão excluídas: {len(preview.sessions)}"),
            ft.Text(f"Tempo de estudo que será removido: {format_duration(preview.total_seconds)}"),
            ft.Text("A exclusão é permanente: remove a matéria, todos os seus estudos e suas preferências."),
            ft.Text("O Histórico e os totais serão recalculados. Essa ação não pode ser desfeita."),
        ]
        show_delete_confirmation(
            "Excluir matéria e seus estudos?", summary, delete_subject, preview,
            "Matéria e seus estudos excluídos", "Não foi possível excluir a matéria. Tente novamente.",
        )

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
            for record in result.sessions:
                if record.study_type not in recorded_study_types:
                    recorded_study_types.append(record.study_type)
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
        manual_study_type.value = "type:Leitura"
        manual_custom_type.value = ""
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
        manual_subject.value = record.subject_id
        manual_study_type.value = f"type:{record.study_type}"
        manual_custom_type.value = ""
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
        if editing_session and editing_session.subject_id not in dict(subject_options):
            manual_message.value = "A matéria original foi excluída. Cancele a edição e atualize o Histórico."
            render()
            return
        if not manual_subject.value:
            manual_message.value = "Cadastre e escolha uma matéria para a sessão."
            render()
            return
        target = editing_session
        timing_changes = {}
        try:
            selected_type = resolve_study_type(manual_study_type.value, manual_custom_type.value)
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
                        update_finished_session, target, manual_subject.value, selected_type,
                        manual_topic.value, **timing_changes,
                    )
                else:
                    result = await asyncio.to_thread(
                        create_manual_session, manual_subject.value, selected_type,
                        start, seconds, manual_topic.value,
                    )
                invalidate_streak_cache()
                saved = True
                manual_open = False
                editing_session, original_period_texts = None, None
                action = "Alterações salvas" if target else "Registro manual salvo"
                message.value = f"{action}. Tempo de estudo: {format_duration(result.duration_seconds)}."
                await refresh_state(reload_study_types=True, reload_streak=True)
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
    goal_settings_button = ft.TextButton("Configurar meta diária", on_click=lambda event: show_settings(event))
    goal_edit_button = ft.Button("Editar meta diária", on_click=open_goal_editor)
    goal_save_button = ft.Button("Salvar meta diária", on_click=save_goal)
    goal_cancel_button = ft.TextButton("Cancelar meta", on_click=cancel_goal_editor)
    streak_refresh_button = ft.TextButton("Atualizar ofensiva", on_click=refresh_streak_summary)
    plan_query_button = ft.Button("Consultar data", on_click=query_date_plan)
    plan_edit_button = ft.TextButton("Editar planejamento", on_click=fill_date_editor)
    plan_save_button = ft.Button("Salvar planejamento", on_click=save_date_plan)
    plan_cancel_button = ft.TextButton("Cancelar planejamento", on_click=cancel_date_editor)
    month_previous_button = ft.TextButton("Mês anterior", on_click=previous_month)
    month_next_button = ft.TextButton("Próximo mês", on_click=next_month)
    month_current_button = ft.TextButton("Mês atual", on_click=current_month)
    month_refresh_button = ft.TextButton("Atualizar mês", on_click=refresh_month)
    statistics_apply_button = ft.Button("Aplicar período", on_click=apply_statistics)
    statistics_refresh_button = ft.TextButton("Atualizar estatísticas", on_click=refresh_statistics)
    for field in (manual_date, manual_time, manual_duration):
        field.on_change = preview_manual_end
    subject_choice.on_select = choose_subject
    mode_choice.on_select = preset_choice.on_select = choose_mode_or_preset
    study_type.on_select = manual_study_type.on_select = choose_mode_or_preset
    plan_mode.on_select = plan_date.on_change = choose_mode_or_preset
    statistics_choice.on_select = choose_mode_or_preset
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
            goal_summary, goal_progress, goal_remaining, goal_settings_button,
            streak_current, streak_record, streak_hint, streak_refresh_button,
            ft.Text("Sessão em andamento", size=20), home_session, open_session_button,
            ft.Text("Minhas matérias", size=20), home_subjects,
        ],
        expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    timer_panel = ft.Column(
        [
            ft.Text("Sessão de estudo", size=20),
            elapsed, timer_status, study_total, cycle_info, session_details,
            subject_choice, study_type, custom_study_type, topic, mode_choice, preset_choice, custom_panel,
            ft.Row([start_button, pause_button, resume_button, next_button, finish_button], wrap=True),
            timer_note, notification_hint, test_notice_button,
        ],
        expand=True, visible=False, scroll=ft.ScrollMode.AUTO,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    manual_timing_row = ft.Row([manual_date, manual_time, manual_duration], wrap=True)
    manual_panel = ft.Column(
        [
            manual_title, manual_subject, manual_study_type, manual_custom_type, manual_topic,
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
    goal_form = ft.Column(
        [goal_duration,
         ft.Text("A meta salva vale a partir de hoje. As metas dos dias anteriores serão preservadas."),
         ft.Text("Uma meta específica ou descanso na data tem prioridade sobre a meta padrão."),
         goal_message, ft.Row([goal_save_button, goal_cancel_button], wrap=True)],
        visible=False, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    settings_panel = ft.Column(
        [ft.Text("Configurações", size=20), ft.Text("Meta diária padrão", size=18),
         goal_settings_status, goal_edit_button, goal_form],
        expand=True, visible=False, scroll=ft.ScrollMode.AUTO,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    plan_form = ft.Column(
        [plan_mode, plan_duration,
         ft.Text("Este planejamento vale somente para a data consultada. Não altera seus estudos."),
         ft.Row([plan_save_button, plan_cancel_button], wrap=True)],
        visible=False, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    calendar_panel = ft.Column(
        [ft.Text("Calendário", size=20), month_title,
         ft.Row([month_previous_button, month_next_button, month_current_button, month_refresh_button], wrap=True),
         month_status, month_error,
         ft.Row([ft.Container(ft.Text(name, size=11, text_align=ft.TextAlign.CENTER), expand=1)
                 for name in ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")], spacing=4),
         month_grid,
         ft.Text("* Hoje · Verde: meta atingida · Laranja: abaixo da meta · Azul: descanso · Cinza: pendente, sem meta ou futuro.", size=12),
         ft.Row([plan_date, plan_query_button], wrap=True),
         plan_status, plan_message, plan_edit_button, plan_form],
        expand=True, visible=False, scroll=ft.ScrollMode.AUTO,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    statistics_custom_row = ft.Row([statistics_start, statistics_end], wrap=True, visible=False)
    statistics_panel = ft.Column(
        [ft.Text("Estatísticas", size=20), statistics_choice, statistics_custom_row,
         ft.Row([statistics_apply_button, statistics_refresh_button], wrap=True),
         statistics_status, statistics_error, statistics_range,
         ft.Text("Tempo total estudado", size=18), statistics_total,
         ft.Text("Média diária", size=18), statistics_average, statistics_days,
         ft.Text("A média inclui todos os dias do intervalo escolhido, inclusive descanso e datas futuras. "
                 "O estudo é contado somente até o instante da consulta."),
         statistics_stamp,
         ft.Text("Tempo por matéria", size=18), statistics_subjects],
        expand=True, visible=False, scroll=ft.ScrollMode.AUTO,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    def show_subjects(event) -> None:
        statistics_panel.visible = False
        history_panel.visible = settings_panel.visible = calendar_panel.visible = False
        home_panel.visible, subjects_panel.visible, timer_panel.visible = False, True, False
        page.update()

    def show_timer(event) -> None:
        statistics_panel.visible = False
        history_panel.visible = settings_panel.visible = calendar_panel.visible = False
        home_panel.visible, subjects_panel.visible, timer_panel.visible = False, False, True
        page.update()

    async def show_home(event) -> None:
        statistics_panel.visible = False
        history_panel.visible = settings_panel.visible = calendar_panel.visible = False
        home_panel.visible, subjects_panel.visible, timer_panel.visible = True, False, False
        await refresh_streak_summary(event)
        render()

    async def show_history(event) -> None:
        statistics_panel.visible = False
        settings_panel.visible = calendar_panel.visible = False
        home_panel.visible = subjects_panel.visible = timer_panel.visible = False
        history_panel.visible = True
        if manual_open:
            render()
        else:
            await load_history()

    def show_settings(event) -> None:
        statistics_panel.visible = False
        calendar_panel.visible = False
        home_panel.visible = subjects_panel.visible = timer_panel.visible = history_panel.visible = False
        settings_panel.visible = True
        open_goal_editor()
        page.update()

    async def show_calendar(event) -> None:
        statistics_panel.visible = False
        home_panel.visible = subjects_panel.visible = timer_panel.visible = False
        history_panel.visible = settings_panel.visible = False
        calendar_panel.visible = True
        if not plan_date.value and today:
            plan_date.value = today.strftime("%d/%m/%Y")
        await refresh_month(event)
        render()

    async def show_statistics(event) -> None:
        nonlocal statistics_fields_initialized
        home_panel.visible = subjects_panel.visible = timer_panel.visible = False
        history_panel.visible = settings_panel.visible = calendar_panel.visible = False
        statistics_panel.visible = True
        if not statistics_fields_initialized and today:
            statistics_start.value = today.replace(day=1).strftime("%d/%m/%Y")
            statistics_end.value = today.strftime("%d/%m/%Y")
            statistics_fields_initialized = True
        await refresh_statistics(event)
        render()

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
                    ft.TextButton("Configurações", on_click=show_settings),
                    ft.TextButton("Calendário", on_click=show_calendar),
                    ft.TextButton("Estatísticas", on_click=show_statistics),
                ], wrap=True),
                message, home_panel, subjects_panel, timer_panel, history_panel, settings_panel, calendar_panel,
                statistics_panel,
            ],
            expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        expand=True,
    ))
    render()

    busy = True
    render()
    try:
        await asyncio.to_thread(initialize_database)
        await refresh_state(reload_study_types=True, reload_streak=True)
    except ValueError as error:
        message.value = str(error)
        render()
        return
    except (sqlite3.Error, OSError):
        message.value = "Não foi possível abrir o banco local. Verifique o acesso à pasta de dados."
        render()
        return
    finally:
        busy = False
        render()
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
        nonlocal connected, delete_dialog, subject_delete_request, date_request, month_request, statistics_request
        connected = False
        subject_delete_request = None
        date_request = None
        month_request = None
        statistics_request = None
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
