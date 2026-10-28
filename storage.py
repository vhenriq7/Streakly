"""Persistência SQLite da prova de conceito Windows/Android."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from timer import SessionEvent, StudySession, as_utc, elapsed_seconds


SCHEMA_VERSION = 2


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
    )


def get_session(session_id: str) -> StudySession:
    with _connection() as connection:
        return _read_session(connection, session_id)


def get_active_session() -> StudySession | None:
    with _connection() as connection:
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


def start_session(
    subject_id: str,
    study_type: str,
    topic: str | None = None,
    *,
    at: datetime | None = None,
) -> StudySession:
    if not isinstance(study_type, str) or not study_type.strip():
        raise ValueError("Escolha o tipo de estudo.")
    if topic is not None and not isinstance(topic, str):
        raise ValueError("O assunto deve ser um texto.")
    study_type = study_type.strip()
    topic = (topic.strip() or None) if topic is not None else None

    with _connection(write=True) as connection:
        if connection.execute(
            "SELECT 1 FROM sessions WHERE status IN ('running', 'paused')"
        ).fetchone():
            raise ValueError("Já existe uma sessão ativa. Retome ou finalize essa sessão.")
        if not connection.execute("SELECT 1 FROM subjects WHERE id = ?", (subject_id,)).fetchone():
            raise ValueError("Escolha uma matéria cadastrada.")
        moment = as_utc(at if at is not None else datetime.now(UTC))
        session_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO sessions (id, subject_id, study_type, topic, status, started_at)
            VALUES (?, ?, ?, ?, 'running', ?)
            """,
            (session_id, subject_id, study_type, topic, moment.isoformat()),
        )
        _insert_event(connection, session_id, 0, SessionEvent("start", moment))
        return _read_session(connection, session_id)


def _transition(session_id: str, kind: str, *, at: datetime | None = None) -> StudySession:
    with _connection(write=True) as connection:
        session = _read_session(connection, session_id)
        allowed = {"pause": ("running",), "resume": ("paused",), "finish": ("running", "paused")}
        if session.status not in allowed[kind]:
            raise ValueError("Essa ação não é válida para o estado atual da sessão.")
        moment = as_utc(at if at is not None else datetime.now(UTC))
        if not session.events or moment < session.events[-1].occurred_at:
            raise ValueError("O relógio está anterior ao último evento da sessão. Confira a hora do dispositivo.")
        event = SessionEvent(kind, moment)
        duration = elapsed_seconds((*session.events, event), moment)
        _insert_event(connection, session_id, len(session.events), event)
        status = {"pause": "paused", "resume": "running", "finish": "finished"}[kind]
        connection.execute(
            "UPDATE sessions SET status = ?, ended_at = ?, duration_seconds = ? WHERE id = ?",
            (status, moment.isoformat() if kind == "finish" else None, duration, session_id),
        )
        return _read_session(connection, session_id)


def pause_session(session_id: str, *, at: datetime | None = None) -> StudySession:
    return _transition(session_id, "pause", at=at)


def resume_session(session_id: str, *, at: datetime | None = None) -> StudySession:
    return _transition(session_id, "resume", at=at)


def finish_session(session_id: str, *, at: datetime | None = None) -> StudySession:
    return _transition(session_id, "finish", at=at)
