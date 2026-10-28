"""Avisos de fases Pomodoro e envio opcional pelo Windows, sem dependências extras."""

import base64
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from timer import StudySession


@dataclass(frozen=True)
class PhaseNotice:
    title: str
    body: str


class PhaseAlertTracker:
    """Reconhece novas fases prontas, sem repetir avisos a cada consulta."""

    def __init__(self) -> None:
        self._initialized = False
        self._last_key: tuple[str, str, datetime] | None = None

    def observe(self, session: StudySession | None) -> PhaseNotice | None:
        phase = session.pomodoro if session else None
        key = None
        if (session and session.status != "finished" and session.mode == "pomodoro"
                and phase and phase.state == "ready"):
            key = (session.id, phase.phase, phase.updated_at)

        if not self._initialized:
            # Ao abrir o app, uma fase antiga pronta não vira um novo alerta.
            self._initialized = True
            self._last_key = key
            return None
        if key is None or key == self._last_key:
            return None

        # Marca antes do envio: falhas do sistema não causam tentativas a cada segundo.
        self._last_key = key
        subject = session.subject_name.strip()
        if len(subject) > 80:
            subject = subject[:77] + "…"
        if phase.phase == "break":
            pause = "pausa longa" if phase.is_long_break else "pausa curta"
            body = f"{subject}: foco concluído. Inicie a {pause} quando quiser."
        else:
            body = f"{subject}: pausa concluída. Inicie o próximo foco quando quiser."
        return PhaseNotice("Streakly — Pomodoro", body)


# O código é constante. Nomes de matérias entram apenas como dados JSON pelo stdin.
_WINDOWS_NOTICE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$notice = [Console]::In.ReadToEnd() | ConvertFrom-Json
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$icon = New-Object System.Windows.Forms.NotifyIcon
try {
    $icon.Icon = [System.Drawing.SystemIcons]::Information
    $icon.Text = 'Streakly'
    $icon.Visible = $true
    $icon.ShowBalloonTip(12000, [string]$notice.title, [string]$notice.body,
                        [System.Windows.Forms.ToolTipIcon]::Info)
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    while ($watch.Elapsed.TotalSeconds -lt 12) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 100
    }
}
finally {
    $icon.Visible = $false
    $icon.Dispose()
}
"""


def _powershell_path() -> Path:
    return (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
            / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def windows_notifications_available() -> bool:
    try:
        return sys.platform == "win32" and _powershell_path().is_file()
    except (OSError, ValueError):
        return False


def send_windows_notice(notice: PhaseNotice) -> bool:
    """Envia um aviso; é bloqueante e deve rodar fora do loop da interface."""
    if not windows_notifications_available():
        return False
    encoded = base64.b64encode(_WINDOWS_NOTICE_SCRIPT.encode("utf-16-le")).decode("ascii")
    payload = json.dumps({"title": notice.title, "body": notice.body}, ensure_ascii=True)
    try:
        result = subprocess.run(
            [str(_powershell_path()), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-STA", "-EncodedCommand", encoded],
            input=payload, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20, shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # Sucesso significa que o Windows recebeu a solicitação; a exibição depende dele.
    return result.returncode == 0
