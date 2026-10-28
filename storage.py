"""Persistência SQLite da prova de conceito Windows/Android."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from timer import (
    PomodoroState, SessionEvent, StudySession, TimerSettings,
    as_utc, elapsed_seconds, pomodoro_remaining_seconds,
)


SCHEMA_VERSION = 3


def database_path() -> Path:
    # Preserva o banco já usado no Windows, inclusive durante o desenvolvimento.
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Streakly" / "poc.sqlite3"

    # No Android, o Flet fornece uma pasta privada e durável para o aplicativo.
    app_data = os.environ.get("FLET_APP_STORAGE_DATA")
    if app_data:
        return Path(app_data) / "poc.sqlite3"

    raise OSError("O diretório de dados do aplicativo não está disponível.")


@contextmanager
def _connection(*, write: bool = False) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(database_path())) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            # A leitura vê um estado consistente; a escrita reserva o banco.
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection


def initialize_database() -> None:
    database_path().parent.mkdir(parents=True, exist_ok=True)
    with _connection(write=True) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError("Este banco foi criado por uma versão mais recente do Streakly.")

        if version == 0:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS subjects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL CHECK (length(trim(name)) > 0)
                )
                """
            )
            connection.execute("PRAGMA user_version = 1")
            version = 1

        if version == 1:
            connection.execute(
                """
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY NOT NULL,
                    subject_id TEXT NOT NULL REFERENCES subjects(id),
                    study_type TEXT NOT NULL CHECK (length(trim(study_type)) > 0),
                    topic TEXT,
                    mode TEXT NOT NULL DEFAULT 'progressive',
                    status TEXT NOT NULL CHECK (status IN ('running', 'paused', 'finished')),
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_seconds REAL NOT NULL DEFAULT 0 CHECK (duration_seconds >= 0),
                    CHECK ((status = 'finished' AND ended_at IS NOT NULL)
                        OR (status != 'finished' AND ended_at IS NULL))
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE session_events (
                    id TEXT PRIMARY KEY NOT NULL,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    sequence INTEGER NOT NULL CHECK (sequence >= 0),
                    kind TEXT NOT NULL CHECK (kind IN ('start', 'pause', 'resume', 'finish')),
                    occurred_at TEXT NOT NULL,
                    UNIQUE (session_id, sequence)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX one_active_session ON sessions ((1))
                WHERE status IN ('running', 'paused')
                """
            )
            connection.execute("PRAGMA user_version = 2")
            version = 2

        if version == 2:
            connection.execute(
                """
                CREATE TABLE subject_timer_settings (
                    subject_id TEXT PRIMARY KEY NOT NULL REFERENCES subjects(id),
                    mode TEXT NOT NULL CHECK (mode IN ('progressive', 'pomodoro')),
                    focus_seconds INTEGER NOT NULL CHECK (focus_seconds > 0),
                    break_seconds INTEGER NOT NULL CHECK (break_seconds > 0)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE pomodoro_state (
                    session_id TEXT PRIMARY KEY NOT NULL REFERENCES sessions(id),
                    phase TEXT NOT NULL CHECK (phase IN ('focus', 'break')),
                    state TEXT NOT NULL CHECK (state IN ('running', 'paused', 'ready')),
                    focus_seconds INTEGER NOT NULL CHECK (focus_seconds > 0),
                    break_seconds INTEGER NOT NULL CHECK (break_seconds > 0),
                    remaining_seconds REAL NOT NULL CHECK (remaining_seconds >= 0),
                    started_at TEXT,
                    updated_at TEXT NOT NULL,
                    CHECK ((state = 'running' AND started_at IS NOT NULL)
                        OR (state != 'running' AND started_at IS NULL))
                )
                """
            )
            connection.execute("PRAGMA user_version = 3")


def add_subject(name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("O nome da matéria não pode estar vazio.")
    with _connection(write=True) as connection:
        connection.execute(
            "INSERT INTO subjects (id, name) VALUES (?, ?)",
            (str(uuid4()), name),
        )


def list_subjects() -> list[str]:
    return [name for _, name in list_subject_options()]


def list_subject_options() -> list[tuple[str, str]]:
    with _connection() as connection:
        rows = connection.execute(
            "SELECT id, name FROM subjects ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
    return [(row["id"], row["name"]) for row in rows]


def _read_subject_timer_settings(connection: sqlite3.Connection, subject_id: str) -> TimerSettings:
    row = connection.execute(
        "SELECT mode, focus_seconds, break_seconds FROM subject_timer_settings WHERE subject_id = ?",
        (subject_id,),
    ).fetchone()
    return TimerSettings(**dict(row)) if row else TimerSettings()


def get_subject_timer_settings(subject_id: str) -> TimerSettings:
    with _connection() as connection:
        if not connection.execute("SELECT 1 FROM subjects WHERE id = ?", (subject_id,)).fetchone():
            raise ValueError("Escolha uma matéria cadastrada.")
        return _read_subject_timer_settings(connection, subject_id)


def _read_pomodoro(connection: sqlite3.Connection, session_id: str) -> PomodoroState:
    row = connection.execute(
        "SELECT * FROM pomodoro_state WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise ValueError("O estado do Pomodoro não foi encontrado.")
    return PomodoroState(
        phase=row["phase"], state=row["state"],
        focus_seconds=row["focus_seconds"], break_seconds=row["break_seconds"],
        remaining_seconds=row["remaining_seconds"],
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _read_session(connection: sqlite3.Connection, session_id: str) -> StudySession:
    row = connection.execute(
        """
        SELECT sessions.*, subjects.name AS subject_name
        FROM sessions JOIN subjects ON subjects.id = sessions.subject_id
        WHERE sessions.id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Sessão não encontrada.")
    events = connection.execute(
        "SELECT kind, occurred_at FROM session_events WHERE session_id = ? ORDER BY sequence",
        (session_id,),
    ).fetchall()
    return StudySession(
        id=row["id"],
        subject_id=row["subject_id"],
        subject_name=row["subject_name"],
        study_type=row["study_type"],
        topic=row["topic"],
        status=row["status"],
        started_at=datetime.fromisoformat(row["started_at"]),
        ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
        events=tuple(
            SessionEvent(event["kind"], datetime.fromisoformat(event["occurred_at"]))
            for event in events
        ),
        duration_seconds=row["duration_seconds"],
        mode=row["mode"],
        pomodoro=_read_pomodoro(connection, session_id) if row["mode"] == "pomodoro" else None,
    )


def get_session(session_id: str) -> StudySession:
    with _connection() as connection:
        return _read_session(connection, session_id)


def get_active_session() -> StudySession | None:
    with _connection() as connection:
        return _read_active_session(connection)


def _read_active_session(connection: sqlite3.Connection) -> StudySession | None:
    row = connection.execute(
        "SELECT id FROM sessions WHERE status IN ('running', 'paused')"
    ).fetchone()
    return _read_session(connection, row["id"]) if row else None


def _insert_event(
    connection: sqlite3.Connection, session_id: str, sequence: int, event: SessionEvent
) -> None:
    connection.execute(
        """
        INSERT INTO session_events (id, session_id, sequence, kind, occurred_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (str(uuid4()), session_id, sequence, event.kind, event.occurred_at.isoformat()),
    )


def _write_pomodoro(connection: sqlite3.Connection, session_id: str, phase: PomodoroState) -> None:
    connection.execute(
        """
        INSERT INTO pomodoro_state
            (session_id, phase, state, focus_seconds, break_seconds,
             remaining_seconds, started_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            phase = excluded.phase, state = excluded.state,
            remaining_seconds = excluded.remaining_seconds,
            started_at = excluded.started_at, updated_at = excluded.updated_at
        """,
        (session_id, phase.phase, phase.state, phase.focus_seconds, phase.break_seconds,
         phase.remaining_seconds, phase.started_at.isoformat() if phase.started_at else None,
         phase.updated_at.isoformat()),
    )


def _with_event(
    connection: sqlite3.Connection, session: StudySession, kind: str, moment: datetime
) -> StudySession:
    event = SessionEvent(kind, moment)
    _insert_event(connection, session.id, len(session.events), event)
    return replace(session, events=(*session.events, event))


def _save_session_status(
    connection: sqlite3.Connection, session: StudySession, status: str, moment: datetime
) -> StudySession:
    duration = elapsed_seconds(session.events, moment)
    connection.execute(
        "UPDATE sessions SET status = ?, ended_at = ?, duration_seconds = ? WHERE id = ?",
        (status, moment.isoformat() if status == "finished" else None, duration, session.id),
    )
    return _read_session(connection, session.id)


def start_session(
    subject_id: str,
    study_type: str,
    topic: str | None = None,
    *,
    at: datetime | None = None,
    mode: str = "progressive",
    focus_seconds: int = 1500,
    break_seconds: int = 300,
) -> StudySession:
    if not isinstance(study_type, str) or not study_type.strip():
        raise ValueError("Escolha o tipo de estudo.")
    if topic is not None and not isinstance(topic, str):
        raise ValueError("O assunto deve ser um texto.")
    study_type = study_type.strip()
    topic = (topic.strip() or None) if topic is not None else None
    if mode not in ("progressive", "pomodoro"):
        raise ValueError("Escolha um modo de cronômetro válido.")
    if mode == "pomodoro" and any(
        type(value) is not int or value <= 0 for value in (focus_seconds, break_seconds)
    ):
        raise ValueError("Os tempos de foco e pausa devem ser inteiros positivos.")

    with _connection(write=True) as connection:
        if connection.execute(
            "SELECT 1 FROM sessions WHERE status IN ('running', 'paused')"
        ).fetchone():
            raise ValueError("Já existe uma sessão ativa. Retome ou finalize essa sessão.")
        if not connection.execute("SELECT 1 FROM subjects WHERE id = ?", (subject_id,)).fetchone():
            raise ValueError("Escolha uma matéria cadastrada.")
        moment = as_utc(at if at is not None else datetime.now(UTC))
        previous = _read_subject_timer_settings(connection, subject_id)
        settings = TimerSettings(
            mode,
            focus_seconds if mode == "pomodoro" else previous.focus_seconds,
            break_seconds if mode == "pomodoro" else previous.break_seconds,
        )
        session_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO sessions (id, subject_id, study_type, topic, status, started_at, mode)
            VALUES (?, ?, ?, ?, 'running', ?, ?)
            """,
            (session_id, subject_id, study_type, topic, moment.isoformat(), mode),
        )
        _insert_event(connection, session_id, 0, SessionEvent("start", moment))
        connection.execute(
            """
            INSERT INTO subject_timer_settings (subject_id, mode, focus_seconds, break_seconds)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(subject_id) DO UPDATE SET mode = excluded.mode,
                focus_seconds = excluded.focus_seconds, break_seconds = excluded.break_seconds
            """,
            (subject_id, settings.mode, settings.focus_seconds, settings.break_seconds),
        )
        if mode == "pomodoro":
            _write_pomodoro(connection, session_id, PomodoroState(
                "focus", "running", settings.focus_seconds, settings.break_seconds,
                float(settings.focus_seconds), moment, moment,
            ))
        return _read_session(connection, session_id)


def _phase_expired(session: StudySession, moment: datetime) -> bool:
    phase = session.pomodoro
    return (phase is not None and phase.state == "running" and phase.started_at is not None
            and moment >= phase.started_at + timedelta(seconds=phase.remaining_seconds))


def _settle_expired(
    connection: sqlite3.Connection, session: StudySession, moment: datetime
) -> StudySession:
    if not _phase_expired(session, moment):
        return session
    phase = session.pomodoro
    assert phase is not None and phase.started_at is not None
    deadline = phase.started_at + timedelta(seconds=phase.remaining_seconds)
    if phase.phase == "focus":
        session = _with_event(connection, session, "pause", deadline)
        next_phase, remaining = "break", phase.break_seconds
    else:
        next_phase, remaining = "focus", phase.focus_seconds
    _write_pomodoro(connection, session.id, replace(
        phase, phase=next_phase, state="ready", remaining_seconds=float(remaining),
        started_at=None, updated_at=deadline,
    ))
    return _save_session_status(connection, session, "paused", deadline)


def refresh_active_session(*, at: datetime | None = None) -> StudySession | None:
    moment = as_utc(at if at is not None else datetime.now(UTC))
    session = get_active_session()
    if session is None or not _phase_expired(session, moment):
        return session
    with _connection(write=True) as connection:
        # Outra janela pode ter consolidado ou finalizado a sessão nesse intervalo.
        current = _read_active_session(connection)
        return _settle_expired(connection, current, moment) if current else None


def _pomodoro_transition(
    connection: sqlite3.Connection, session: StudySession, kind: str, moment: datetime
) -> StudySession:
    phase = session.pomodoro
    assert phase is not None
    if moment < phase.updated_at:
        raise ValueError("O relógio está anterior à última alteração do Pomodoro.")
    session = _settle_expired(connection, session, moment)
    phase = session.pomodoro
    assert phase is not None

    if kind == "finish":
        session = _with_event(connection, session, "finish", moment)
        next_state = replace(
            phase, state="paused", remaining_seconds=pomodoro_remaining_seconds(phase, moment),
            started_at=None, updated_at=moment,
        )
        status = "finished"
    elif kind == "pause" and phase.state == "running":
        if phase.phase == "focus":
            session = _with_event(connection, session, "pause", moment)
        next_state = replace(
            phase, state="paused", remaining_seconds=pomodoro_remaining_seconds(phase, moment),
            started_at=None, updated_at=moment,
        )
        status = "paused"
    elif kind == "resume" and phase.state == "paused":
        if phase.phase == "focus":
            session = _with_event(connection, session, "resume", moment)
        next_state = replace(phase, state="running", started_at=moment, updated_at=moment)
        status = "running"
    elif kind == "next" and phase.state == "ready":
        if phase.phase == "focus":
            session = _with_event(connection, session, "resume", moment)
        next_state = replace(phase, state="running", started_at=moment, updated_at=moment)
        status = "running"
    else:
        raise ValueError("Essa ação não é válida para a fase atual do Pomodoro.")
    _write_pomodoro(connection, session.id, next_state)
    return _save_session_status(connection, session, status, moment)


def _transition(session_id: str, kind: str, *, at: datetime | None = None) -> StudySession:
    with _connection(write=True) as connection:
        session = _read_session(connection, session_id)
        if session.status == "finished":
            raise ValueError("Essa sessão já foi finalizada.")
        moment = as_utc(at if at is not None else datetime.now(UTC))
        if not session.events or moment < session.events[-1].occurred_at:
            raise ValueError("O relógio está anterior ao último evento da sessão. Confira a hora do dispositivo.")
        if session.mode == "pomodoro":
            return _pomodoro_transition(connection, session, kind, moment)
        if kind == "next":
            raise ValueError("O cronômetro progressivo não tem próxima fase.")
        allowed = {"pause": ("running",), "resume": ("paused",), "finish": ("running", "paused")}
        if session.status not in allowed[kind]:
            raise ValueError("Essa ação não é válida para o estado atual da sessão.")
        session = _with_event(connection, session, kind, moment)
        status = {"pause": "paused", "resume": "running", "finish": "finished"}[kind]
        return _save_session_status(connection, session, status, moment)


def pause_session(session_id: str, *, at: datetime | None = None) -> StudySession:
    return _transition(session_id, "pause", at=at)


def resume_session(session_id: str, *, at: datetime | None = None) -> StudySession:
    return _transition(session_id, "resume", at=at)


def finish_session(session_id: str, *, at: datetime | None = None) -> StudySession:
    return _transition(session_id, "finish", at=at)


def start_next_phase(session_id: str, *, at: datetime | None = None) -> StudySession:
    return _transition(session_id, "next", at=at)
