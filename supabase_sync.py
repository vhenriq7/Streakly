"""Transporte HTTP para a outbox do Supabase, sem persistir credenciais."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from storage import SyncOperation


_ENTITIES = frozenset({
    "subjects",
    "sessions",
    "session_events",
    "subject_timer_settings",
    "pomodoro_state",
    "daily_goal_defaults",
    "daily_goal_overrides",
})
_OPERATIONS = frozenset({"upsert", "delete"})
_MAX_BATCH = 100
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_REQUEST_BYTES = 8 * 1024 * 1024


class SupabaseSyncError(Exception):
    """Falha de transporte ou resposta inválida, sem incluir credenciais."""


@dataclass(frozen=True)
class RemoteOperation:
    sequence: int
    id: str
    entity: str
    entity_id: str
    operation: str
    payload: dict[str, Any]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class SupabaseSyncClient:
    """Acessa o diário remoto com chave pública e token de sessão do usuário."""

    def __init__(self, project_url: str, public_api_key: str, timeout: float = 15.0):
        if not isinstance(project_url, str) or not project_url.strip():
            raise ValueError("Informe a URL do projeto Supabase.")
        if not isinstance(public_api_key, str) or not public_api_key.strip():
            raise ValueError("Informe a chave pública do Supabase.")
        if _is_privileged_key(public_api_key):
            raise ValueError("Use somente uma chave pública; chaves privilegiadas são recusadas.")
        if any(character in public_api_key for character in "\r\n"):
            raise ValueError("A chave pública contém caracteres inválidos.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise ValueError("O tempo limite deve estar entre 0 e 60 segundos.")

        parts = urlsplit(project_url.strip().rstrip("/"))
        local_http = parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            parts.scheme != "https" and not local_http
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise ValueError("A URL deve usar HTTPS e conter somente a origem do projeto.")

        self._base_url = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
        self._public_api_key = public_api_key
        self._timeout = float(timeout)

    def push(self, operations: Sequence[SyncOperation], user_id: str, access_token: str) -> tuple[str, ...]:
        """Insere ou reconhece o lote e confirma que o remoto guarda os mesmos dados."""
        user_id = _uuid_text(user_id, "usuário")
        token = _validate_token(access_token)
        if not isinstance(operations, Sequence) or isinstance(operations, (str, bytes)):
            raise ValueError("Informe uma sequência de operações locais.")
        if not operations or len(operations) > _MAX_BATCH:
            raise ValueError(f"Envie entre 1 e {_MAX_BATCH} operações por lote.")

        rows: list[dict[str, Any]] = []
        for item in operations:
            operation_id = _uuid_text(getattr(item, "id", None), "operação")
            entity = getattr(item, "entity", None)
            entity_id = _uuid_text(getattr(item, "entity_id", None), "entidade")
            action = getattr(item, "operation", None)
            payload = getattr(item, "payload", None)
            if (
                not isinstance(entity, str)
                or entity not in _ENTITIES
                or not isinstance(action, str)
                or action not in _OPERATIONS
                or not isinstance(payload, dict)
            ):
                raise ValueError("A operação local contém entidade, ação ou payload inválido.")
            rows.append({
                "operation_id": operation_id,
                "user_id": user_id,
                "entity": entity,
                "entity_id": entity_id,
                "operation": action,
                "payload": payload,
            })

        query = urlencode({"on_conflict": "user_id,operation_id"})
        inserted = self._request_json(
            "POST",
            f"/rest/v1/sync_operations?{query}",
            token,
            body=rows,
            prefer="resolution=ignore-duplicates,return=representation",
        )
        if not isinstance(inserted, list):
            raise SupabaseSyncError("O Supabase retornou uma confirmação de envio inválida.")

        ids = tuple(row["operation_id"] for row in rows)
        filters = urlencode({
            "select": "sequence,operation_id,entity,entity_id,operation,payload",
            "operation_id": f"in.({','.join(ids)})",
            "order": "sequence.asc",
            "limit": str(_MAX_BATCH),
        })
        confirmed = self._read_operations(
            f"/rest/v1/sync_operations?{filters}", token,
        )
        by_id = {item.id: item for item in confirmed}
        if set(by_id) != set(ids):
            raise SupabaseSyncError("Nem todas as operações do lote foram confirmadas no diário remoto.")
        for row in rows:
            remote = by_id[row["operation_id"]]
            if (
                remote.entity != row["entity"]
                or remote.entity_id != row["entity_id"]
                or remote.operation != row["operation"]
                or remote.payload != row["payload"]
            ):
                raise SupabaseSyncError("Um identificador de operação já existe com outros dados.")
        return ids

    def fetch(self, cursor: int, access_token: str, limit: int = 100) -> tuple[RemoteOperation, ...]:
        """Busca uma página ordenada; RLS do Supabase limita a conta visível."""
        if type(cursor) is not int or cursor < 0:
            raise ValueError("O cursor deve ser um inteiro não negativo.")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("O limite da página deve estar entre 1 e 1000.")
        token = _validate_token(access_token)
        query = urlencode({
            "select": "sequence,operation_id,entity,entity_id,operation,payload",
            "sequence": f"gt.{cursor}",
            "order": "sequence.asc",
            "limit": str(limit),
        })
        operations = self._read_operations(f"/rest/v1/sync_operations?{query}", token)
        if len(operations) > limit or any(item.sequence <= cursor for item in operations):
            raise SupabaseSyncError("O Supabase retornou operações fora dos limites do cursor solicitado.")
        return operations

    def _read_operations(self, path: str, token: str) -> tuple[RemoteOperation, ...]:
        rows = self._request_json("GET", path, token)
        if not isinstance(rows, list):
            raise SupabaseSyncError("O Supabase retornou uma página inválida.")
        operations = tuple(_parse_remote_operation(row) for row in rows)
        if any(left.sequence >= right.sequence for left, right in zip(operations, operations[1:])):
            raise SupabaseSyncError("O Supabase retornou operações fora da ordem do cursor.")
        return operations

    def _request_json(
        self,
        method: str,
        path: str,
        token: str,
        body: object | None = None,
        prefer: str | None = None,
    ) -> Any:
        headers = {
            "apikey": self._public_api_key,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            try:
                data = json.dumps(
                    body, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError):
                raise ValueError("Os dados da operação não podem ser enviados como JSON.") from None
            if len(data) > _MAX_REQUEST_BYTES:
                raise ValueError("O lote excede o limite de tamanho permitido.")
        if prefer:
            headers["Prefer"] = prefer
        request = Request(self._base_url + path, data=data, headers=headers, method=method)
        try:
            with build_opener(_NoRedirect()).open(request, timeout=self._timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise SupabaseSyncError(f"O Supabase respondeu HTTP {response.status}.")
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            status = error.code
            error.close()
            raise SupabaseSyncError(f"O Supabase respondeu HTTP {status}.") from None
        except (URLError, TimeoutError, OSError):
            raise SupabaseSyncError("Não foi possível concluir a requisição ao Supabase.") from None

        if len(raw) > _MAX_RESPONSE_BYTES:
            raise SupabaseSyncError("A resposta do Supabase excedeu o limite permitido.")
        try:
            return json.loads(raw) if raw else []
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SupabaseSyncError("O Supabase retornou JSON inválido.") from None


def _parse_remote_operation(row: object) -> RemoteOperation:
    if not isinstance(row, dict):
        raise SupabaseSyncError("O Supabase retornou uma operação inválida.")
    try:
        sequence = row["sequence"]
        operation_id = _uuid_text(row["operation_id"], "operação remota")
        entity_id = _uuid_text(row["entity_id"], "entidade remota")
        entity = row["entity"]
        action = row["operation"]
        payload = row["payload"]
    except (KeyError, ValueError, TypeError):
        raise SupabaseSyncError("O Supabase retornou uma operação inválida.") from None
    if (
        type(sequence) is not int
        or sequence <= 0
            or not isinstance(entity, str)
        or entity not in _ENTITIES
            or not isinstance(action, str)
        or action not in _OPERATIONS
        or not isinstance(payload, dict)
    ):
        raise SupabaseSyncError("O Supabase retornou uma operação inválida.")
    return RemoteOperation(sequence, operation_id, entity, entity_id, action, payload)


def _uuid_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"O identificador de {label} é inválido.")
    try:
        return str(UUID(value))
    except (ValueError, AttributeError):
        raise ValueError(f"O identificador de {label} é inválido.") from None


def _validate_token(token: object) -> str:
    if not isinstance(token, str) or not token.strip() or any(character in token for character in "\r\n"):
        raise ValueError("O token de sessão está ausente ou inválido.")
    token = token.strip()
    if _is_privileged_key(token):
        raise ValueError("Tokens privilegiados não podem ser usados pelo aplicativo.")
    return token


def _is_privileged_key(value: str) -> bool:
    if value.lower().startswith("sb_secret_") or "service_role" in value.lower():
        return True
    parts = value.split(".")
    if len(parts) != 3:
        return False
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        return False
    return isinstance(claims, dict) and claims.get("role") == "service_role"
