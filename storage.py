"""Persistência SQLite da prova de conceito Windows/Android."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4


def database_path() -> Path:
    # Preserva o banco já usado no Windows, inclusive durante o desenvolvimento.
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Streakly" / "poc.sqlite3"

    # No Android, o Flet fornece uma pasta privada e durável para o aplicativo.
    app_data = os.environ.get("FLET_APP_STORAGE_DATA")
    if app_data:
        return Path(app_data) / "poc.sqlite3"

    raise OSError("O diretório de dados do aplicativo não está disponível.")


def initialize_database() -> None:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS subjects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL CHECK (length(trim(name)) > 0)
                )
                """
            )
            connection.execute("PRAGMA user_version = 1")


def add_subject(name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("O nome da matéria não pode estar vazio.")
    with closing(sqlite3.connect(database_path())) as connection:
        # A transação confirma a gravação ou desfaz em caso de erro.
        with connection:
            connection.execute(
                "INSERT INTO subjects (id, name) VALUES (?, ?)",
                (str(uuid4()), name),
            )


def list_subjects() -> list[str]:
    with closing(sqlite3.connect(database_path())) as connection:
        rows = connection.execute(
            "SELECT name FROM subjects ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
    return [row[0] for row in rows]
