"""Persistência SQLite da prova de conceito Windows/Android."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from uuid import uuid4

from timer import (
    PomodoroState, SessionEvent, StudySession, TimerSettings,
    as_utc, elapsed_seconds, local_day_window, pomodoro_remaining_seconds,
    session_study_seconds_between,
)


SCHEMA_VERSION = 7

NAMED_PRESETS = {
    "25/5": (1500, 300, 900, 4),
    "50/10": (3000, 600, 900, 4),
}


@dataclass(frozen=True)
class DefaultDailyGoal:
    id: str
    effective_from: date
    duration_seconds: int


@dataclass(frozen=True)
class DailyGoalOverride:
    id: str
    day: date
    duration_seconds: int | None
    is_rest_day: bool


@dataclass(frozen=True)
class DailyPlan:
    day: date
    default_goal: DefaultDailyGoal | None
    override: DailyGoalOverride | None

    @property
    def is_rest_day(self) -> bool:
        return self.override is not None and self.override.is_rest_day

    @property
    def effective_seconds(self) -> int | None:
        if self.is_rest_day:
            return None
        if self.override is not None:
            return self.override.duration_seconds
        return self.default_goal.duration_seconds if self.default_goal else None


@dataclass(frozen=True)
class StudyOverview:
    subjects: tuple[tuple[str, str], ...]
    active_session: StudySession | None
    day: date
    total_seconds: float
    goal: DefaultDailyGoal | None = None
    goal_override: DailyGoalOverride | None = None


@dataclass(frozen=True)
class HistoryPage:
    sessions: tuple[StudySession, ...]
    offset: int
    has_more: bool


@dataclass(frozen=True)
class SubjectDeletionPreview:
    id: str
    name: str
    sessions: tuple[StudySession, ...]

    @property
    def total_seconds(self) -> float:
        return sum((session.duration_seconds for session in self.sessions), 0.0)


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
            version = 3

        if version == 3:
            long_break = "long_break_seconds INTEGER NOT NULL DEFAULT 900 CHECK (long_break_seconds > 0)"
            cycles = "cycles_until_long_break INTEGER NOT NULL DEFAULT 4 CHECK (cycles_until_long_break > 0)"
            preset = "preset TEXT NOT NULL DEFAULT '25/5' CHECK (preset IN ('25/5', '50/10', 'custom'))"
            new_columns = {
                "subject_timer_settings": (long_break, cycles, preset),
                "pomodoro_state": (
                    long_break, cycles,
                    "completed_focuses INTEGER NOT NULL DEFAULT 0 CHECK (completed_focuses >= 0)",
                    "is_long_break INTEGER NOT NULL DEFAULT 0 CHECK (is_long_break IN (0, 1))",
                    preset,
                ),
            }
            for table, columns in new_columns.items():
                for definition in columns:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
                connection.execute(
                    f"""
                    UPDATE {table} SET preset = CASE
                        WHEN focus_seconds = 1500 AND break_seconds = 300 THEN '25/5'
                        WHEN focus_seconds = 3000 AND break_seconds = 600 THEN '50/10'
                        ELSE 'custom' END
                    """
                )
            # Retira o progresso já gravado do foco atual antes de contar ciclos.
            # Pausas manuais também geram eventos pause, portanto não são contadas.
            connection.execute(
                """
                UPDATE pomodoro_state SET completed_focuses = MAX(0, CAST(ROUND((
                    (SELECT duration_seconds FROM sessions WHERE id = session_id)
                    - CASE WHEN phase = 'focus'
                        THEN focus_seconds - remaining_seconds ELSE 0 END
                ) / focus_seconds) AS INTEGER))
                """
            )
            connection.execute("PRAGMA user_version = 4")
            version = 4

        if version == 4:
            connection.execute(
                """
                ALTER TABLE sessions ADD COLUMN source TEXT NOT NULL DEFAULT 'timer'
                CHECK (source IN ('timer', 'manual'))
                """
            )
            connection.execute("PRAGMA user_version = 5")
            version = 5

        if version == 5:
            connection.execute(
                """
                CREATE TABLE daily_goal_defaults (
                    id TEXT PRIMARY KEY NOT NULL,
                    effective_from TEXT NOT NULL UNIQUE,
                    duration_seconds INTEGER NOT NULL CHECK (duration_seconds > 0)
                )
                """
            )
            connection.execute("PRAGMA user_version = 6")
            version = 6

        if version == 6:
            connection.execute(
                """
                CREATE TABLE daily_goal_overrides (
                    id TEXT PRIMARY KEY NOT NULL,
                    day TEXT NOT NULL UNIQUE,
                    duration_seconds INTEGER,
                    is_rest_day INTEGER NOT NULL CHECK (is_rest_day IN (0, 1)),
                    CHECK ((is_rest_day = 1 AND duration_seconds IS NULL)
                        OR (is_rest_day = 0 AND duration_seconds IS NOT NULL
                            AND duration_seconds > 0))
                )
                """
            )
            connection.execute("PRAGMA user_version = 7")


def _read_default_daily_goal(connection: sqlite3.Connection, day: date) -> DefaultDailyGoal | None:
    row = connection.execute(
        """
        SELECT id, effective_from, duration_seconds FROM daily_goal_defaults
        WHERE effective_from <= ? ORDER BY effective_from DESC LIMIT 1
        """,
        (day.isoformat(),),
    ).fetchone()
    return DefaultDailyGoal(
        id=row["id"], effective_from=date.fromisoformat(row["effective_from"]),
        duration_seconds=row["duration_seconds"],
    ) if row else None


def read_default_daily_goal(day: date) -> DefaultDailyGoal | None:
    """Consulta a meta vigente na data, sem criar metas para dias não observados."""
    if type(day) is not date:
        raise ValueError("Informe uma data válida para consultar a meta diária.")
    with _connection() as connection:
        return _read_default_daily_goal(connection, day)


def set_default_daily_goal(
    duration_seconds: int,
    expected: DefaultDailyGoal | None,
    expected_day: date,
    *,
    at: datetime | None = None,
    tz: tzinfo | None = None,
) -> DefaultDailyGoal:
    """Define a vigência de hoje sem reescrever metas de dias anteriores."""
    if type(duration_seconds) is not int or not 0 < duration_seconds <= 2**63 - 1:
        raise ValueError("A meta diária deve ser uma duração inteira positiva válida.")
    if type(expected_day) is not date:
        raise ValueError("Atualize a configuração antes de salvar a meta diária.")
    if expected is not None and not isinstance(expected, DefaultDailyGoal):
        raise ValueError("Atualize a configuração antes de salvar a meta diária.")
    moment = as_utc(at if at is not None else datetime.now(UTC))
    day = moment.astimezone(tz).date()
    if expected_day != day:
        raise ValueError("O dia mudou. Cancele e abra novamente a configuração da meta diária.")
    with _connection(write=True) as connection:
        if connection.execute(
            "SELECT 1 FROM daily_goal_defaults WHERE effective_from > ? LIMIT 1",
            (day.isoformat(),),
        ).fetchone():
            raise ValueError("Confira a data e o fuso do dispositivo: há uma meta com vigência posterior a hoje.")
        current = _read_default_daily_goal(connection, day)
        if current != expected:
            raise ValueError("A meta diária mudou desde a leitura. Cancele e abra novamente a configuração.")
        if current and current.duration_seconds == duration_seconds:
            return current
        if current and current.effective_from == day:
            connection.execute(
                "UPDATE daily_goal_defaults SET duration_seconds = ? WHERE id = ?",
                (duration_seconds, current.id),
            )
            return replace(current, duration_seconds=duration_seconds)
        result = DefaultDailyGoal(str(uuid4()), day, duration_seconds)
        connection.execute(
            "INSERT INTO daily_goal_defaults (id, effective_from, duration_seconds) VALUES (?, ?, ?)",
            (result.id, result.effective_from.isoformat(), result.duration_seconds),
        )
        return result


def _read_daily_goal_override(connection: sqlite3.Connection, day: date) -> DailyGoalOverride | None:
    row = connection.execute(
        "SELECT id, day, duration_seconds, is_rest_day FROM daily_goal_overrides WHERE day = ?",
        (day.isoformat(),),
    ).fetchone()
    return DailyGoalOverride(
        id=row["id"], day=date.fromisoformat(row["day"]),
        duration_seconds=row["duration_seconds"], is_rest_day=bool(row["is_rest_day"]),
    ) if row else None


def read_daily_goal_override(day: date) -> DailyGoalOverride | None:
    """Consulta somente a exceção da data, sem alterar o cronômetro."""
    if type(day) is not date:
        raise ValueError("Informe uma data válida para consultar o planejamento.")
    with _connection() as connection:
        return _read_daily_goal_override(connection, day)


def read_daily_plan(day: date) -> DailyPlan:
    """Lê a meta padrão e a exceção da data na mesma visão do banco."""
    if type(day) is not date:
        raise ValueError("Informe uma data válida para consultar o planejamento.")
    with _connection() as connection:
        return DailyPlan(
            day=day, default_goal=_read_default_daily_goal(connection, day),
            override=_read_daily_goal_override(connection, day),
        )


def _validate_daily_goal_override_expected(day: date, expected: DailyGoalOverride | None) -> None:
    if type(day) is not date:
        raise ValueError("Informe uma data válida para configurar o planejamento.")
    if expected is not None and not isinstance(expected, DailyGoalOverride):
        raise ValueError("Cancele e consulte novamente a data antes de salvar o planejamento.")
    if expected is not None and expected.day != day:
        raise ValueError("Consulte a data escolhida antes de salvar seu planejamento.")


def set_daily_goal_override(
    day: date,
    duration_seconds: int | None,
    is_rest_day: bool,
    expected: DailyGoalOverride | None,
) -> DailyGoalOverride:
    """Define uma meta específica ou descanso, preservando a meta padrão e os estudos."""
    _validate_daily_goal_override_expected(day, expected)
    if type(is_rest_day) is not bool:
        raise ValueError("Escolha entre meta específica e dia de descanso.")
    if is_rest_day:
        if duration_seconds is not None:
            raise ValueError("Um dia de descanso não deve ter uma meta de estudo.")
    elif type(duration_seconds) is not int or not 0 < duration_seconds <= 2**63 - 1:
        raise ValueError("A meta da data deve ser uma duração inteira positiva válida.")
    with _connection(write=True) as connection:
        current = _read_daily_goal_override(connection, day)
        if current != expected:
            raise ValueError("O planejamento da data mudou desde a leitura. Cancele e consulte novamente.")
        if current is not None:
            if current.duration_seconds == duration_seconds and current.is_rest_day == is_rest_day:
                return current
            connection.execute(
                "UPDATE daily_goal_overrides SET duration_seconds = ?, is_rest_day = ? WHERE id = ?",
                (duration_seconds, int(is_rest_day), current.id),
            )
            return replace(current, duration_seconds=duration_seconds, is_rest_day=is_rest_day)
        result = DailyGoalOverride(str(uuid4()), day, duration_seconds, is_rest_day)
        connection.execute(
            "INSERT INTO daily_goal_overrides (id, day, duration_seconds, is_rest_day) VALUES (?, ?, ?, ?)",
            (result.id, result.day.isoformat(), result.duration_seconds, int(result.is_rest_day)),
        )
        return result


def clear_daily_goal_override(day: date, expected: DailyGoalOverride | None) -> None:
    """Remove somente a exceção confirmada; a data volta a usar sua meta padrão."""
    _validate_daily_goal_override_expected(day, expected)
    with _connection(write=True) as connection:
        current = _read_daily_goal_override(connection, day)
        if current != expected:
            raise ValueError("O planejamento da data mudou desde a leitura. Cancele e consulte novamente.")
        if current is not None:
            connection.execute("DELETE FROM daily_goal_overrides WHERE id = ?", (current.id,))


def add_subject(name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("O nome da matéria não pode estar vazio.")
    with _connection(write=True) as connection:
        connection.execute(
            "INSERT INTO subjects (id, name) VALUES (?, ?)",
            (str(uuid4()), name),
        )


def rename_subject(subject_id: str, expected_name: str, new_name: str) -> None:
    """Renomeia pelo UUID, recusando sobrescrever uma alteração concorrente."""
    if not isinstance(subject_id, str) or not subject_id:
        raise ValueError("Escolha uma matéria cadastrada.")
    if not isinstance(expected_name, str) or not expected_name:
        raise ValueError("Atualize a lista de matérias antes de renomear.")
    if not isinstance(new_name, str) or not new_name.strip():
        raise ValueError("O nome da matéria não pode estar vazio.")
    name = new_name.strip()
    with _connection(write=True) as connection:
        row = connection.execute(
            "SELECT name FROM subjects WHERE id = ?", (subject_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Escolha uma matéria cadastrada.")
        if row["name"] != expected_name:
            raise ValueError("A matéria foi alterada desde a leitura. Atualize a lista antes de renomear.")
        if row["name"] == name:
            return
        connection.execute("UPDATE subjects SET name = ? WHERE id = ?", (name, subject_id))


def _read_subject_deletion_preview(
    connection: sqlite3.Connection, subject_id: str,
) -> SubjectDeletionPreview:
    subject = connection.execute(
        "SELECT id, name FROM subjects WHERE id = ?", (subject_id,),
    ).fetchone()
    if subject is None:
        raise ValueError("A matéria não foi encontrada. Atualize a lista de matérias.")
    rows = connection.execute(
        "SELECT id, status FROM sessions WHERE subject_id = ? ORDER BY id", (subject_id,),
    ).fetchall()
    if any(row["status"] != "finished" for row in rows):
        raise ValueError("Finalize a sessão ativa desta matéria antes de excluí-la.")
    return SubjectDeletionPreview(
        id=subject["id"], name=subject["name"],
        sessions=tuple(_read_session(connection, row["id"]) for row in rows),
    )


def read_subject_deletion_preview(subject_id: str) -> SubjectDeletionPreview:
    """Lê os dados da confirmação sem alterar sessões ou consolidar prazos."""
    if not isinstance(subject_id, str) or not subject_id:
        raise ValueError("Escolha uma matéria cadastrada para excluir.")
    with _connection() as connection:
        return _read_subject_deletion_preview(connection, subject_id)


def delete_subject(expected: SubjectDeletionPreview) -> None:
    """Exclui matéria, estudos e preferências juntos após validar a confirmação."""
    if (not isinstance(expected, SubjectDeletionPreview)
            or not isinstance(expected.id, str) or not expected.id):
        raise ValueError("Escolha uma matéria cadastrada para excluir.")
    with _connection(write=True) as connection:
        current = _read_subject_deletion_preview(connection, expected.id)
        if current != expected:
            raise ValueError(
                "A matéria ou seus estudos mudaram desde a leitura. "
                "Cancele e abra novamente a confirmação antes de excluir."
            )
        # Mantém as chaves estrangeiras ativas e remove as dependências primeiro.
        connection.execute(
            "DELETE FROM pomodoro_state WHERE session_id IN "
            "(SELECT id FROM sessions WHERE subject_id = ?)", (current.id,),
        )
        connection.execute(
            "DELETE FROM session_events WHERE session_id IN "
            "(SELECT id FROM sessions WHERE subject_id = ?)", (current.id,),
        )
        connection.execute("DELETE FROM sessions WHERE subject_id = ?", (current.id,))
        connection.execute("DELETE FROM subject_timer_settings WHERE subject_id = ?", (current.id,))
        connection.execute("DELETE FROM subjects WHERE id = ?", (current.id,))


def list_subjects() -> list[str]:
    return [name for _, name in list_subject_options()]


def list_recorded_study_types() -> list[str]:
    """Lista os nomes já usados, sem alterar sessões ou normalizar o histórico."""
    with _connection() as connection:
        rows = connection.execute(
            "SELECT DISTINCT study_type FROM sessions "
            "ORDER BY study_type COLLATE NOCASE, study_type"
        ).fetchall()
    return [row["study_type"] for row in rows]


def list_subject_options() -> list[tuple[str, str]]:
    with _connection() as connection:
        rows = connection.execute(
            "SELECT id, name FROM subjects ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
    return [(row["id"], row["name"]) for row in rows]


def _read_subject_timer_settings(connection: sqlite3.Connection, subject_id: str) -> TimerSettings:
    row = connection.execute(
        """
        SELECT mode, focus_seconds, break_seconds, long_break_seconds,
            cycles_until_long_break, preset
        FROM subject_timer_settings WHERE subject_id = ?
        """,
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
        long_break_seconds=row["long_break_seconds"],
        cycles_until_long_break=row["cycles_until_long_break"],
        completed_focuses=row["completed_focuses"],
        is_long_break=bool(row["is_long_break"]),
        preset=row["preset"],
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
        source=row["source"],
    )


def get_session(session_id: str) -> StudySession:
    with _connection() as connection:
        return _read_session(connection, session_id)


def get_active_session() -> StudySession | None:
    with _connection() as connection:
        return _read_active_session(connection)


def read_session_history(*, limit: int = 25, offset: int = 0) -> HistoryPage:
    """Lê uma página de sessões finalizadas, da mais recente para a mais antiga."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("A quantidade de sessões por página deve estar entre 1 e 100.")
    if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
        raise ValueError("A posição da página deve ser um inteiro não negativo válido.")
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT id FROM sessions WHERE status = 'finished'
            ORDER BY started_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (limit + 1, offset),
        ).fetchall()
        return HistoryPage(
            sessions=tuple(_read_session(connection, row["id"]) for row in rows[:limit]),
            offset=offset,
            has_more=len(rows) > limit,
        )


def read_study_overview(*, at: datetime | None = None, tz: tzinfo | None = None) -> StudyOverview:
    """Lê matérias, sessão ativa, total e meta de hoje da mesma visão do banco."""
    moment = as_utc(at if at is not None else datetime.now(UTC))
    day, start, end = local_day_window(moment, tz)
    # Consolida um prazo vencido antes de abrir a visão consistente de leitura.
    refresh_active_session(at=moment)
    with _connection() as connection:
        subjects = connection.execute(
            "SELECT id, name FROM subjects ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
        active = _read_active_session(connection)
        candidates = connection.execute(
            """
            SELECT id FROM sessions
            WHERE started_at < ? AND (ended_at IS NULL OR ended_at > ?)
            ORDER BY started_at, id
            """,
            (end.isoformat(), start.isoformat()),
        ).fetchall()
        total = 0.0
        for row in candidates:
            # Reutiliza a mesma sessão ativa que será exibida na interface.
            session = active if active and row["id"] == active.id else _read_session(connection, row["id"])
            total += session_study_seconds_between(session, start, end, moment)
        return StudyOverview(
            subjects=tuple((row["id"], row["name"]) for row in subjects),
            active_session=active, day=day, total_seconds=total,
            goal=_read_default_daily_goal(connection, day),
            goal_override=_read_daily_goal_override(connection, day),
        )


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
             remaining_seconds, started_at, updated_at, long_break_seconds,
             cycles_until_long_break, completed_focuses, is_long_break, preset)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            phase = excluded.phase, state = excluded.state,
            remaining_seconds = excluded.remaining_seconds,
            started_at = excluded.started_at, updated_at = excluded.updated_at,
            completed_focuses = excluded.completed_focuses, is_long_break = excluded.is_long_break
        """,
        (session_id, phase.phase, phase.state, phase.focus_seconds, phase.break_seconds,
         phase.remaining_seconds, phase.started_at.isoformat() if phase.started_at else None,
         phase.updated_at.isoformat(), phase.long_break_seconds, phase.cycles_until_long_break,
         phase.completed_focuses, int(phase.is_long_break), phase.preset),
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


def create_manual_session(
    subject_id: str,
    study_type: str,
    started_at: datetime,
    duration_seconds: int,
    topic: str | None = None,
    *,
    at: datetime | None = None,
) -> StudySession:
    """Registra um período contínuo concluído, sem mudar cronômetros ou preferências."""
    if not isinstance(subject_id, str) or not subject_id:
        raise ValueError("Escolha uma matéria cadastrada.")
    if not isinstance(study_type, str) or not study_type.strip():
        raise ValueError("Escolha o tipo de estudo.")
    if topic is not None and not isinstance(topic, str):
        raise ValueError("O assunto deve ser um texto.")
    if type(duration_seconds) is not int or duration_seconds <= 0:
        raise ValueError("A duração deve ser um número inteiro positivo de segundos.")
    study_type = study_type.strip()
    topic = (topic.strip() or None) if topic is not None else None
    try:
        start = as_utc(started_at)
        moment = as_utc(at if at is not None else datetime.now(UTC))
        end = start + timedelta(seconds=duration_seconds)
    except OverflowError:
        raise ValueError("O período informado está fora do intervalo de datas permitido.") from None
    if end > moment:
        raise ValueError("O período de estudo precisa ter terminado até agora.")

    with _connection(write=True) as connection:
        if not connection.execute("SELECT 1 FROM subjects WHERE id = ?", (subject_id,)).fetchone():
            raise ValueError("Escolha uma matéria cadastrada.")
        candidates = connection.execute(
            """
            SELECT id FROM sessions
            WHERE started_at < ? AND (ended_at IS NULL OR ended_at > ?)
            """,
            (end.isoformat(), start.isoformat()),
        ).fetchall()
        for row in candidates:
            existing = _read_session(connection, row["id"])
            # Pausas e espera não ocupam estudo; um foco ativo para no seu prazo.
            if session_study_seconds_between(existing, start, end, moment) > 0:
                raise ValueError("Já existe tempo de estudo registrado nesse período.")

        session_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO sessions
                (id, subject_id, study_type, topic, mode, status,
                 started_at, ended_at, duration_seconds, source)
            VALUES (?, ?, ?, ?, 'progressive', 'finished', ?, ?, ?, 'manual')
            """,
            (session_id, subject_id, study_type, topic,
             start.isoformat(), end.isoformat(), duration_seconds),
        )
        _insert_event(connection, session_id, 0, SessionEvent("start", start))
        _insert_event(connection, session_id, 1, SessionEvent("finish", end))
        return _read_session(connection, session_id)


def update_finished_session(
    expected: StudySession,
    subject_id: str,
    study_type: str,
    topic: str | None = None,
    *,
    started_at: datetime | None = None,
    duration_seconds: int | None = None,
    at: datetime | None = None,
) -> StudySession:
    """Corrige uma sessão finalizada, recusando alterações sobre uma leitura antiga."""
    if not isinstance(expected, StudySession):
        raise ValueError("Escolha uma sessão finalizada para editar.")
    if not isinstance(subject_id, str) or not subject_id:
        raise ValueError("Escolha uma matéria cadastrada.")
    if not isinstance(study_type, str) or not study_type.strip():
        raise ValueError("Escolha o tipo de estudo.")
    if topic is not None and not isinstance(topic, str):
        raise ValueError("O assunto deve ser um texto.")
    if duration_seconds is not None and (type(duration_seconds) is not int or duration_seconds <= 0):
        raise ValueError("A duração deve ser um número inteiro positivo de segundos.")
    try:
        new_start = as_utc(started_at) if started_at is not None else None
    except OverflowError:
        raise ValueError("O período informado está fora do intervalo de datas permitido.") from None
    timing_requested = started_at is not None or duration_seconds is not None
    study_type = study_type.strip()
    topic = (topic.strip() or None) if topic is not None else None

    with _connection(write=True) as connection:
        current = _read_session(connection, expected.id)
        if current.status != "finished":
            raise ValueError("Somente sessões finalizadas podem ser editadas.")
        if current != expected:
            raise ValueError("A sessão foi alterada desde a leitura. Atualize o Histórico antes de editar.")
        if timing_requested and current.source != "manual":
            raise ValueError("Os horários e as pausas registrados pelo cronômetro devem ser preservados.")
        if not connection.execute("SELECT 1 FROM subjects WHERE id = ?", (subject_id,)).fetchone():
            raise ValueError("Escolha uma matéria cadastrada.")

        start = new_start if new_start is not None else current.started_at
        duration = duration_seconds if duration_seconds is not None else current.duration_seconds
        timing_changed = start != current.started_at or duration != current.duration_seconds
        if timing_changed:
            event_rows = connection.execute(
                "SELECT sequence, kind FROM session_events WHERE session_id = ? ORDER BY sequence",
                (current.id,),
            ).fetchall()
            if (current.source != "manual" or current.mode != "progressive"
                    or current.pomodoro is not None
                    or [(row["sequence"], row["kind"]) for row in event_rows]
                    != [(0, "start"), (1, "finish")]):
                raise ValueError("A estrutura desta sessão não permite alterar o período de estudo.")
            try:
                start = as_utc(start)
                moment = as_utc(at if at is not None else datetime.now(UTC))
                end = start + timedelta(seconds=duration)
            except OverflowError:
                raise ValueError("O período informado está fora do intervalo de datas permitido.") from None
            if end > moment:
                raise ValueError("O período de estudo precisa ter terminado até agora.")
            candidates = connection.execute(
                """
                SELECT id FROM sessions
                WHERE id != ? AND started_at < ? AND (ended_at IS NULL OR ended_at > ?)
                """,
                (current.id, end.isoformat(), start.isoformat()),
            ).fetchall()
            for row in candidates:
                existing = _read_session(connection, row["id"])
                if session_study_seconds_between(existing, start, end, moment) > 0:
                    raise ValueError("Já existe tempo de estudo registrado nesse período.")

            # Mantém os UUIDs dos eventos, alterando apenas os dois instantes.
            connection.execute(
                "UPDATE session_events SET occurred_at = ? WHERE session_id = ? AND sequence = 0",
                (start.isoformat(), current.id),
            )
            connection.execute(
                "UPDATE session_events SET occurred_at = ? WHERE session_id = ? AND sequence = 1",
                (end.isoformat(), current.id),
            )
            connection.execute(
                """
                UPDATE sessions SET subject_id = ?, study_type = ?, topic = ?,
                    started_at = ?, ended_at = ?, duration_seconds = ? WHERE id = ?
                """,
                (subject_id, study_type, topic, start.isoformat(), end.isoformat(), duration, current.id),
            )
        else:
            connection.execute(
                "UPDATE sessions SET subject_id = ?, study_type = ?, topic = ? WHERE id = ?",
                (subject_id, study_type, topic, current.id),
            )
        return _read_session(connection, current.id)


def delete_finished_session(expected: StudySession) -> None:
    """Exclui uma sessão finalizada e seus detalhes, recusando uma leitura antiga."""
    if not isinstance(expected, StudySession):
        raise ValueError("Escolha uma sessão finalizada para excluir.")
    with _connection(write=True) as connection:
        current = _read_session(connection, expected.id)
        if current.status != "finished":
            raise ValueError("Somente sessões finalizadas podem ser excluídas.")
        if current != expected:
            raise ValueError("A sessão foi alterada desde a leitura. Atualize o Histórico antes de excluir.")

        # Remove as dependências antes da sessão, mantendo as chaves estrangeiras ativas.
        connection.execute("DELETE FROM pomodoro_state WHERE session_id = ?", (current.id,))
        connection.execute("DELETE FROM session_events WHERE session_id = ?", (current.id,))
        connection.execute("DELETE FROM sessions WHERE id = ?", (current.id,))


def start_session(
    subject_id: str,
    study_type: str,
    topic: str | None = None,
    *,
    at: datetime | None = None,
    mode: str = "progressive",
    focus_seconds: int = 1500,
    break_seconds: int = 300,
    long_break_seconds: int = 900,
    cycles_until_long_break: int = 4,
    preset: str | None = None,
) -> StudySession:
    if not isinstance(study_type, str) or not study_type.strip():
        raise ValueError("Escolha o tipo de estudo.")
    if topic is not None and not isinstance(topic, str):
        raise ValueError("O assunto deve ser um texto.")
    study_type = study_type.strip()
    topic = (topic.strip() or None) if topic is not None else None
    if mode not in ("progressive", "pomodoro"):
        raise ValueError("Escolha um modo de cronômetro válido.")
    if mode == "pomodoro":
        values = (focus_seconds, break_seconds, long_break_seconds, cycles_until_long_break)
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("Os tempos e a quantidade de ciclos devem ser inteiros positivos.")
        if any(value > 2**63 - 1 for value in values):
            raise ValueError("Os valores informados são altos demais.")
        if preset is None:
            preset = next((name for name, canonical in NAMED_PRESETS.items()
                           if values == canonical), "custom")
        if not isinstance(preset, str) or preset not in (*NAMED_PRESETS, "custom"):
            raise ValueError("Escolha uma configuração Pomodoro válida.")
        if preset in NAMED_PRESETS and values != NAMED_PRESETS[preset]:
            raise ValueError("Os tempos informados não correspondem à configuração escolhida.")

    with _connection(write=True) as connection:
        if connection.execute(
            "SELECT 1 FROM sessions WHERE status IN ('running', 'paused')"
        ).fetchone():
            raise ValueError("Já existe uma sessão ativa. Retome ou finalize essa sessão.")
        if not connection.execute("SELECT 1 FROM subjects WHERE id = ?", (subject_id,)).fetchone():
            raise ValueError("Escolha uma matéria cadastrada.")
        moment = as_utc(at if at is not None else datetime.now(UTC))
        if mode == "pomodoro":
            try:
                moment + timedelta(seconds=max(focus_seconds, break_seconds, long_break_seconds))
            except OverflowError:
                raise ValueError("A duração informada é alta demais.") from None
        previous = _read_subject_timer_settings(connection, subject_id)
        settings = TimerSettings(
            mode,
            focus_seconds if mode == "pomodoro" else previous.focus_seconds,
            break_seconds if mode == "pomodoro" else previous.break_seconds,
            long_break_seconds if mode == "pomodoro" else previous.long_break_seconds,
            cycles_until_long_break if mode == "pomodoro" else previous.cycles_until_long_break,
            preset if mode == "pomodoro" else previous.preset,
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
            INSERT INTO subject_timer_settings
                (subject_id, mode, focus_seconds, break_seconds,
                 long_break_seconds, cycles_until_long_break, preset)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject_id) DO UPDATE SET mode = excluded.mode,
                focus_seconds = excluded.focus_seconds, break_seconds = excluded.break_seconds,
                long_break_seconds = excluded.long_break_seconds,
                cycles_until_long_break = excluded.cycles_until_long_break, preset = excluded.preset
            """,
            (subject_id, settings.mode, settings.focus_seconds, settings.break_seconds,
             settings.long_break_seconds, settings.cycles_until_long_break, settings.preset),
        )
        if mode == "pomodoro":
            _write_pomodoro(connection, session_id, PomodoroState(
                "focus", "running", settings.focus_seconds, settings.break_seconds,
                float(settings.focus_seconds), moment, moment,
                long_break_seconds=settings.long_break_seconds,
                cycles_until_long_break=settings.cycles_until_long_break,
                preset=settings.preset,
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
        completed = phase.completed_focuses + 1
        is_long = completed % phase.cycles_until_long_break == 0
        next_phase = "break"
        remaining = phase.long_break_seconds if is_long else phase.break_seconds
    else:
        completed, is_long = phase.completed_focuses, False
        next_phase, remaining = "focus", phase.focus_seconds
    _write_pomodoro(connection, session.id, replace(
        phase, phase=next_phase, state="ready", remaining_seconds=float(remaining),
        started_at=None, updated_at=deadline,
        completed_focuses=completed, is_long_break=is_long,
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
