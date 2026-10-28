"""Gera o APK no Windows/Flet 1.0.5, tratando caminhos com acentos."""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ABIS = ("armeabi-v7a", "arm64-v8a", "x86_64")


def run_with_log(command: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    """Mostra a saída enquanto guarda uma cópia para reconhecer o erro conhecido."""
    lines = []
    with subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    ) as process:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        return process.wait(), "".join(lines)


def escape_properties(text: str) -> str:
    # Java Properties aceita \uXXXX; JSON também trata pares substitutos Unicode.
    return "".join(
        json.dumps(char, ensure_ascii=True)[1:-1] if ord(char) > 127 else char
        for char in text
    )


def prepare_build_root() -> Path:
    """Copia somente o código para um caminho ASCII, mantendo os caches do build."""
    if str(ROOT).isascii():
        return ROOT
    public = os.environ.get("PUBLIC")
    if not public or not Path(public).is_absolute():
        raise RuntimeError("PUBLIC não aponta para uma pasta pública absoluta do Windows.")
    staging = Path(public) / "Documents" / "Streakly-Android-PoC"
    if staging.is_symlink() or staging.is_junction():
        raise RuntimeError("A pasta de compilação não pode ser um link simbólico.")
    staging = staging.resolve()
    if not str(staging).isascii():
        raise RuntimeError("A pasta pública de compilação precisa ter um caminho sem acentos.")
    marker = staging / ".source-root"
    if staging.exists():
        if marker.is_symlink() or not marker.is_file() or marker.read_text(encoding="utf-8") != str(ROOT):
            raise RuntimeError("A pasta de compilação já existe sem o marcador correto; nada será sobrescrito.")
    else:
        staging.mkdir(parents=True)
        marker.write_text(str(ROOT), encoding="utf-8")

    app = staging / "app"
    if app.is_symlink() or app.is_junction() or (app.exists() and not app.is_dir()):
        raise RuntimeError("A pasta app de compilação não é um diretório comum.")
    app.mkdir(exist_ok=True)
    sources = ("main.py", "storage.py", "timer.py", "notifications.py", "pyproject.toml")
    for name in sources:
        target = app / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise RuntimeError(f"Destino inesperado para {name}; cópia cancelada.")
    for name in sources:
        shutil.copy2(ROOT / name, app / name)
    print(f"Compilando a cópia do código em: {app}")
    return app


def publish_apk(apk: Path) -> None:
    """Entrega o APK no repositório, sem copiar bancos nem ferramentas."""
    destination = ROOT / "build" / "apk" / apk.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if apk.resolve() != destination.resolve():
        shutil.copy2(apk, destination)
    print(f"APK disponível em: {destination}")


def main() -> int:
    # O PowerShell pode usar uma codificação antiga mesmo com logs em UTF-8.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if os.name != "nt":
        raise RuntimeError("Este helper é específico para Windows.")
    if version("flet") != "1.0.5" or version("flet-cli") != "1.0.5":
        raise RuntimeError("Este helper exige Flet e Flet CLI 1.0.5.")

    build_root = prepare_build_root()
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    public = os.environ.get("PUBLIC")
    if public:
        sdk = Path(public) / "Documents" / "Streakly-Android-PoC" / "android-sdk"
        if (sdk / "platform-tools" / "adb.exe").is_file():
            env["ANDROID_HOME"] = str(sdk)
    # Plugins com código nativo também precisam de um cache sem acentos.
    env["PUB_CACHE"] = str(build_root / "build" / "pub-cache")
    flutter_project = build_root / "build" / "flutter"
    if (flutter_project / "pubspec.yaml").is_file():
        from flet.version import flutter_version
        from flet_cli.utils.flutter import get_flutter_dir

        flutter = Path(get_flutter_dir(flutter_version)) / "bin" / "flutter.bat"
        if flutter.is_file():
            code, _ = run_with_log(
                [str(flutter), "pub", "get", "--suppress-analytics"],
                flutter_project, env,
            )
            if code != 0:
                return code
    cli = Path(sys.executable).with_name("flet.exe")
    code, log = run_with_log(
        [str(cli), "build", "apk", "--yes", "--no-rich-output"], build_root, env
    )
    if code == 0:
        apks = list((build_root / "build" / "apk").glob("*.apk"))
        if len(apks) != 1:
            raise RuntimeError("O Flet não gerou exatamente um APK universal de teste.")
        publish_apk(apks[0])
        return 0
    # O Flet pode quebrar a mensagem de erro em várias linhas no terminal.
    normalized_log = " ".join(log.split())
    if "Included build" not in normalized_log or "does not exist" not in normalized_log:
        return code

    build = build_root / "build"
    android = build / "flutter" / "android"
    properties = android / "local.properties"
    if not properties.is_file():
        return code
    text = properties.read_text(encoding="utf-8")
    sdk_line = next((line for line in text.splitlines() if line.startswith("flutter.sdk=")), "")
    sdk = sdk_line.partition("=")[2].replace("\\\\", "\\").replace("\\:", ":")
    if not any(ord(char) > 127 for char in sdk) or not Path(sdk).is_dir():
        return code

    # O Flet precisa ter preparado o código e as dependências antes do Gradle.
    app = build / "python-app"
    site_packages = build / "site-packages"
    for module in ("main", "storage", "timer", "notifications"):
        if not any((app / f"{module}.{suffix}").is_file() for suffix in ("py", "pyc")):
            raise RuntimeError(f"O Flet não preparou o módulo {module}; fallback cancelado.")
    if not all((site_packages / abi / "flet").is_dir() for abi in ABIS):
        raise RuntimeError("Dependências Android incompletas; fallback cancelado.")

    config = tomllib.loads((build_root / "pyproject.toml").read_text(encoding="utf-8"))
    python_version = (build / ".python-version").read_text(encoding="utf-8").strip()
    from flet_cli.utils.android_sdk import AndroidSDK
    from flet_cli.utils.jdk import install_jdk

    android_home = env.get("ANDROID_HOME") or AndroidSDK.android_home_dir()
    if android_home is None:
        raise RuntimeError("O Android SDK preparado pelo Flet não foi encontrado.")
    env.update(
        JAVA_HOME=install_jdk(print),
        ANDROID_HOME=str(android_home),
        SERIOUS_PYTHON_VERSION=python_version,
        SERIOUS_PYTHON_APP=str(app),
        SERIOUS_PYTHON_SITE_PACKAGES=str(site_packages),
        SERIOUS_PYTHON_BUNDLE_ID=config["tool"]["flet"]["bundle_id"],
    )
    print("Aplicando escapes Java apenas no arquivo gerado local.properties.")
    properties.write_text(escape_properties(text), encoding="ascii")
    code, _ = run_with_log(
        [
            str(android / "gradlew.bat"), ":app:assembleRelease",
            "-Ptarget-platform=android-arm,android-arm64,android-x64",
            "-Pbase-application-name=android.app.Application",
            "-Ptarget=lib/main.dart", "-Pdart-obfuscation=false",
            "-Ptrack-widget-creation=true", "-Ptree-shake-icons=true",
        ],
        android, env,
    )
    if code != 0:
        return code

    outputs = build / "flutter" / "build" / "app" / "outputs"
    candidates = (
        outputs / "flutter-apk" / "app-release.apk",
        outputs / "apk" / "release" / "app-release.apk",
    )
    apk = next((path for path in candidates if path.is_file()), None)
    if apk is None:
        raise RuntimeError("O Gradle terminou, mas o APK não foi encontrado.")
    publish_apk(apk)
    return 0


if __name__ == "__main__":
    sys.exit(main())
