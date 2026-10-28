"""Modelos e cálculo do cronômetro, sem depender da interface ou do SQLite."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class SessionEvent:
    kind: str
    occurred_at: datetime


@dataclass(frozen=True)
class TimerSettings:
    mode: str = "progressive"
    focus_seconds: int = 1500
    break_seconds: int = 300
    long_break_seconds: int = 900
    cycles_until_long_break: int = 4
    preset: str = "25/5"


@dataclass(frozen=True)
class PomodoroState:
    phase: str
    state: str
    focus_seconds: int
    break_seconds: int
    remaining_seconds: float
    started_at: datetime | None
    updated_at: datetime
    long_break_seconds: int = 900
    cycles_until_long_break: int = 4
    completed_focuses: int = 0
    is_long_break: bool = False
    preset: str = "25/5"


@dataclass(frozen=True)
class StudySession:
    id: str
    subject_id: str
    subject_name: str
    study_type: str
    topic: str | None
    status: str
    started_at: datetime
    ended_at: datetime | None
    events: tuple[SessionEvent, ...]
    duration_seconds: float
    mode: str = "progressive"
    pomodoro: PomodoroState | None = None


def as_utc(moment: datetime) -> datetime:
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("O horário precisa incluir o fuso horário.")
    return moment.astimezone(UTC)


def elapsed_seconds(events: Iterable[SessionEvent], now: datetime) -> float:
    """Soma os intervalos de estudo, incluindo o intervalo ainda em andamento."""
    now = as_utc(now)
    total = 0.0
    running_since = None
    previous_at = None
    started = False
    finished = False

    for event in events:
        moment = as_utc(event.occurred_at)
        if previous_at is not None and moment < previous_at:
            raise ValueError("Os eventos da sessão estão fora de ordem.")
        if finished:
            raise ValueError("Uma sessão finalizada não pode receber novos eventos.")

        if event.kind == "start":
            if started:
                raise ValueError("A sessão já foi iniciada.")
            started = True
            running_since = moment
        elif not started:
            raise ValueError("O primeiro evento da sessão precisa ser o início.")
        elif event.kind == "resume":
            if running_since is not None:
                raise ValueError("A sessão já está rodando.")
            running_since = moment
        elif event.kind == "pause":
            if running_since is None:
                raise ValueError("A sessão já está pausada.")
            total += (moment - running_since).total_seconds()
            running_since = None
        elif event.kind == "finish":
            if running_since is not None:
                total += (moment - running_since).total_seconds()
            running_since = None
            finished = True
        else:
            raise ValueError("Tipo de evento desconhecido.")
        previous_at = moment

    if running_since is not None:
        # Uma correção do relógio não deve produzir duração negativa na tela.
        total += max(0.0, (now - running_since).total_seconds())
    return total


def format_duration(seconds: float) -> str:
    hours, remaining = divmod(int(max(0.0, seconds)), 3600)
    minutes, seconds = divmod(remaining, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def pomodoro_remaining_seconds(state: PomodoroState, now: datetime) -> float:
    now = as_utc(now)
    remaining = max(0.0, state.remaining_seconds)
    if state.state == "running" and state.started_at is not None:
        spent = max(0.0, (now - as_utc(state.started_at)).total_seconds())
        remaining = max(0.0, remaining - spent)
    return remaining


def session_elapsed_seconds(session: StudySession, now: datetime) -> float:
    """O foco Pomodoro para no prazo, mesmo antes de consolidar isso no banco."""
    now = as_utc(now)
    phase = session.pomodoro
    if (phase is not None and phase.phase == "focus" and phase.state == "running"
            and phase.started_at is not None):
        deadline = as_utc(phase.started_at) + timedelta(seconds=phase.remaining_seconds)
        now = min(now, deadline)
    return elapsed_seconds(session.events, now)
