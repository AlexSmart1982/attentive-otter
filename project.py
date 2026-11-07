"""Self-contained comparison progress with stable file identities."""

from copy import copy, deepcopy
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from action_plan import ActionPlan
from storage import write_json
from project_lock import project_lock, owns_write_lock


def save_project(model, filename, view=None):
    # Do not restat every photo on an ordinary save. The version guard below
    # rejects every existing untracked target (including source-file aliases)
    # and any changed checkpoint under the project lock. Legacy export/apply
    # retain the default exhaustive source-alias validation.
    filename = model.validate_save_path(filename, check_source_aliases=False)
    with nullcontext() if owns_write_lock(filename) else project_lock(filename):
        check_project_version(model, filename)
        if not owns_write_lock(filename) and Path(str(filename)+".apply.json").exists():
            raise OSError("Сначала загрузите сравнение для восстановления прерванного применения")
        data = _save_project(model, filename, view)
        remember_project_version(model, filename)
        return data


def check_project_version(model, filename):
    filename = Path(filename).resolve()
    actual = project_digest(filename) if filename.exists() else None
    expected = getattr(model, "_project_versions", {}).get(str(filename))
    if actual != expected:
        raise OSError("Сохранённое сравнение изменено другим экземпляром. Загрузите его заново: " + str(filename))


def remember_project_version(model, filename):
    if not hasattr(model, "_project_versions"):
        model._project_versions = {}
    model._project_versions[str(Path(filename).resolve())] = project_digest(filename)


def project_digest(filename):
    value = hashlib.sha256()
    with Path(filename).open("rb") as stream:
        while block := stream.read(1024*1024):
            value.update(block)
    return value.hexdigest()


def _save_project(model, filename, view):
    model._validate()
    data = {"kind": "comparsion-project", "schema_version": 1, "mode": "comparison",
            "saved_at": datetime.now(timezone.utc).isoformat(), "review_path": str(model.document_path),
            "review": deepcopy(model.document), "state": model.export_state(),
            "undo": [model.export_state(s) for s in model.history],
            "redo": [model.export_state(s) for s in model.redo_history], "view": deepcopy(view or {})}
    write_json(filename, data)
    return data


def load_project(filename):
    from execution import _recover_project_locked
    with project_lock(filename):
        _recover_project_locked(filename)
        model, view = _load_project(filename)
        remember_project_version(model, filename)
        return model, view


def _load_project(filename):
    data = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
    if data.get("kind") != "comparsion-project" or data.get("schema_version") != 1 or data.get("mode") not in ("simulation", "comparison"):
        raise ValueError("Неподдерживаемый формат сохранённого сравнения")
    model = ActionPlan(data["review"], data.get("review_path"))
    model.restore_state(data["state"])
    for field, stack in (("undo", model.history), ("redo", model.redo_history)):
        for entry in data.get(field, [])[-50:]:
            # Review metadata is immutable here. Avoid parsing/copying all files
            # and pairs again for each of the 50 saved undo snapshots.
            historical = copy(model)
            historical._intent_paths = {}
            historical._removal_preview = None
            historical.restore_state(entry)
            stack.append(historical._snapshot())
    if not isinstance(data.get("view", {}), dict):
        raise ValueError("Некорректное состояние интерфейса")
    return model, data.get("view", {})
