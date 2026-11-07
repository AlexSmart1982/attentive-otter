"""Build matching portable application and transferable source folders on Windows."""

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from release_materials import prepare_materials

ROOT = Path(__file__).resolve().parent
RELEASE_NAME = "Attentive-Otter-1.0-BETA"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_files():
    """Include the local import closure, all tests, and original artwork sources."""
    modules = {p.stem: p for pattern in ("*.py", "*.pyw") for p in ROOT.glob(pattern)}
    pending = {"desktop", "build_windows"} | {p.stem for p in ROOT.glob("test_*.py")}
    included = set()
    while pending:
        name = pending.pop()
        if name in included or name not in modules:
            continue
        included.add(name)
        tree = ast.parse(modules[name].read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                pending.update(item.name.split(".")[0] for item in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                pending.add(node.module.split(".")[0])
    paths = [modules[name] for name in included]
    paths += [ROOT/name for name in ("PhotoComparison.spec", "LICENSE", "COPYRIGHT.txt",
              "appearance_profile.json", "review_annotations.json", "requirements.txt",
              "requirements-build.txt", "start_app.cmd")]
    for folder in ("assets", "artwork"):
        paths += [p for p in (ROOT/folder).rglob("*") if p.is_file()
                  and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    return sorted(set(paths))


def manifest(folder):
    return {p.relative_to(folder).as_posix(): digest(p)
            for p in sorted(folder.rglob("*")) if p.is_file()
            and p.name != "SHA256SUMS.json" and "__pycache__" not in p.parts}


def verify_executable(folder, report):
    environment = {k: v for k, v in os.environ.items() if k not in
                   ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_PLUGIN_PATH",
                    "QT_QPA_PLATFORM_PLUGIN_PATH")}
    system_root = Path(os.environ.get("SystemRoot", "C:/Windows"))
    environment.update(PATH=str(system_root/"System32") + os.pathsep + str(system_root),
                       QT_QPA_PLATFORM="offscreen")
    subprocess.run([str(folder/"Attentive_Otter.exe"), "--verify-deployment", str(report)],
                   cwd=folder, env=environment, timeout=120, check=True)
    result = json.loads(report.read_text(encoding="utf-8"))
    if not result.get("ok") or not result.get("frozen") or result.get("console"):
        raise RuntimeError("Executable verification failed: " + str(report))
    result.pop("user_data", None)
    write_json(report, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"release")
    options = parser.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("Use 64-bit Python on Windows")
    release = options.output.resolve()
    release.mkdir(parents=True, exist_ok=True)
    app_target = release/(RELEASE_NAME + "-Windows-x64")
    source_target = release/(RELEASE_NAME + "-Source")
    if app_target.exists() or source_target.exists():
        raise SystemExit("Output already exists; choose a new --output directory (nothing overwritten)")

    with tempfile.TemporaryDirectory(prefix="clone_sifter_build_") as temporary:
        stage = Path(temporary)
        source = stage/"source"
        source.mkdir()
        originals = {}
        for path in source_files():
            relative = path.relative_to(ROOT)
            destination = source/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            originals[relative.as_posix()] = digest(path)
        git_head = None
        git_dirty = None
        if (ROOT/".git").exists():
            git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            git_dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
        versions = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}
        (source/"requirements-lock.txt").write_text("\n".join(
            f"{name}=={version}" for name, version in sorted(versions.items(), key=lambda x: x[0].lower()))
            + "\n", encoding="utf-8")
        prepare_materials(source/"third_party")
        (source/"README.txt").write_text(SOURCE_README, encoding="utf-8-sig")
        build_info = {"app": "Attentive Otter", "version": "1.0 BETA", "platform": "Windows x64",
                      "ui_variant": "center_keep_three_actions",
                      "built_at": datetime.now(timezone.utc).isoformat(),
                      "git_base_commit": git_head, "working_copy_has_changes": git_dirty,
                      "python": platform.python_version(), "packages": versions,
                      "application_source_sha256": originals}
        # Build from the copied sources, not from a separately changing working directory.
        subprocess.run([sys.executable, "-B", "-m", "PyInstaller", "--noconfirm",
                        "--workpath", str(stage/"build"), "--distpath", str(stage/"dist"),
                        str(source/"PhotoComparison.spec")], cwd=source,
                       stderr=subprocess.STDOUT, check=True)
        app = stage/"dist"/"AttentiveOtter"
        for name in ("LICENSE", "COPYRIGHT.txt"):
            shutil.copy2(source/name, app/name)
        shutil.copytree(source/"third_party"/"licenses", app/"third_party"/"licenses")
        shutil.copy2(source/"third_party"/"README.txt", app/"third_party"/"README.txt")
        shutil.copy2(source/"third_party"/"sources.json", app/"third_party"/"sources.json")
        (app/"Как_запустить.txt").write_text(APP_README, encoding="utf-8-sig")
        checks = source/"verification"
        checks.mkdir()
        build_info["verification"] = verify_executable(app, checks/"deployment-check.json")
        relocated = stage/"Проверка переноса"/"Attentive Otter"
        shutil.copytree(app, relocated)
        verify_executable(relocated, checks/"relocation-check.json")
        tests = subprocess.run([sys.executable, "-B", "-X", "utf8", "-m", "unittest", "discover", "-v"],
                               cwd=source, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", timeout=300)
        (checks/"tests.log").write_text(tests.stdout, encoding="utf-8")
        print(tests.stdout[-1000:], flush=True)
        tests.check_returncode()
        if any(digest(source/name) != value for name, value in originals.items()):
            raise RuntimeError("Source snapshot changed during build/verification")
        write_json(source/"build-info.json", build_info)
        write_json(app/"build-info.json", build_info)
        shutil.copytree(checks, app/"verification")
        write_json(source/"SHA256SUMS.json", manifest(source))
        write_json(app/"SHA256SUMS.json", manifest(app))
        shutil.copytree(source, source_target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(app, app_target)
        for folder in (source_target, app_target):
            if manifest(folder) != json.loads((folder/"SHA256SUMS.json").read_text(encoding="utf-8")):
                raise RuntimeError("Release copy checksum verification failed: " + str(folder))
        print(json.dumps({"application": str(app_target), "source": str(source_target),
                          "verified": True}, ensure_ascii=False), flush=True)


APP_README = """Attentive Otter 1.0 BETA — тестовая версия для Windows x64

Запустите «Attentive_Otter.exe». Устанавливать Python не нужно.
Переносите всю папку приложения целиком; _internal содержит необходимые библиотеки.
Выберите папки с изображениями и нажмите «Найти похожие».

В обеих карточках поле «Основание» справа от пути объясняет рекомендацию для активной группы:
решающее правило, значение и источник даты. Подробности — при наведении на строку.
Даты Windows относятся к файлу и не устанавливают дату съёмки. При равенстве
включённых критериев приложение не назначает единственного победителя.
Внизу каждой карточки три кнопки в одну строку. «Перенести» находится посередине,
«Оставить» — ближе к центру окна, «Удалить» — с внешнего края.
«Сохранить прогресс сравнения» в первый раз предлагает выбрать файл, затем пишет
в этот же файл. На кнопке видны «Сохраняю…», затем «Сохранено» и время записи.
Подтверждение не сдвигает фотографии. Сохранение и обычное закрытие не обходят
заново все пути фотографий; проверки перед настоящим применением сохранены.

До «Применить изменения» решения остаются намерениями. При применении выполняются
настоящие перемещения и замены. В настройках выберите, куда отправлять удаляемые
файлы: в корзину Windows (по умолчанию) или в указанную папку. Старые файлы при
замене отправляются туда же. При совпадении имён в выбранной папке добавляется
числовой суффикс; существующие файлы не перезаписываются.
Сохранённое сравнение использует абсолютные пути к фотографиям.
Настройки и кеш: %LOCALAPPDATA%\\ComparsionV2
Логи ошибок запуска: %LOCALAPPDATA%\\ComparsionV2\\logs

© 2026 Скрябин Александр, alexsmart@yandex.ru
GNU GPLv3 (GPL-3.0-only). Бесплатное использование, включая коммерческое.
Изменение и распространение — на условиях GPLv3. Текст: LICENSE, COPYRIGHT.txt.
Программа предоставляется без гарантий в пределах, допускаемых законом.

При передаче этой сборки передавайте также соседнюю папку
Attentive-Otter-1.0-BETA-Source: в ней соответствующий код, инструкции сборки,
исходники сторонних компонентов и их лицензии. См. third_party/README.txt.
"""

SOURCE_README = """Attentive Otter 1.0 BETA — соответствующие исходники Windows x64 сборки

© 2026 Скрябин Александр, alexsmart@yandex.ru
Собственный код приложения: GNU GPLv3 (GPL-3.0-only), см. COPYRIGHT.txt и LICENSE.
Сторонние компоненты сохраняют свои лицензии; см. third_party/README.txt.

Здесь полный используемый приложением код, ресурсы, исходники графики, тесты,
скрипты сборки и сжатые исходники сторонних компонентов. Фотографии пользователей,
сохранённые сравнения, кеш, история Git и внутренняя память проекта не включены.
build-info.json фиксирует SHA256 каждого исходного файла, версии и базовый Git
commit. Наличие изменений рабочей копии указано отдельно: commit не описывает
весь передаваемый код. SHA256SUMS.json позволяет проверить весь комплект.

ЗАПУСК ИЗ ИСХОДНИКОВ (Windows 10/11 x64, CPython 3.13.8 x64)
Откройте PowerShell в этой папке. Пример команд:
  py -3.13 -m venv .venv
  .\\.venv\\Scripts\\python.exe -m pip install -r requirements-lock.txt
  .\\.venv\\Scripts\\pythonw.exe -B desktop.pyw
Можно использовать start_app.cmd после создания .venv.
Для установки Python и пакетов нужен интернет. Python и зависимости также
предоставлены исходными архивами в third_party/sources; они не нужны для запуска EXE.

ПРОВЕРКА
  .\\.venv\\Scripts\\python.exe -B -m unittest discover -v
Тесты создают временные данные. Отчёты этой сборки: verification/.

СБОРКА
  .\\.venv\\Scripts\\python.exe -B build_windows.py
Скрипт создаёт две папки внутри release и проверяет EXE и перенесённую копию.
Существующие папки не перезаписывает; другой путь: --output C:\\Temp\\new-release
Для загрузки исходных архивов зависимостей нужен интернет; кеш находится
в %TEMP%\\clone-sifter-source-cache. Комплект собран PyInstaller 6.16.0.
Для одной только сборки EXE из имеющихся материалов:
  .\\.venv\\Scripts\\python.exe -m PyInstaller --noconfirm PhotoComparison.spec
Результат: dist\\AttentiveOtter\\Attentive_Otter.exe; переносится вся папка dist\\AttentiveOtter.
Полная упаковка с документацией и соответствующими исходниками: build_windows.py.

Нативные сторонние библиотеки использованы из официальных Python wheels.
Их исходники и upstream-инструкции приложены в third_party; пересборка этих
библиотек требует отдельных компиляторов и инструментов из upstream-инструкций.
Побайтовая воспроизводимость EXE не заявляется (даты и инструменты влияют на сборку).

При передаче готового EXE передавайте эту папку вместе с папкой приложения.
Публикация исходников на общедоступном сайте для такой передачи не требуется.
"""


if __name__ == "__main__":
    main()
