"""Verified file application with a durable journal and rollback on failure."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sys
import uuid
import psutil

from storage import write_json
from system_trash import recycle_file
from project_lock import project_lock, project_writer
from stable_files import LockedFile, protected_files, protected_file

_ACTIVE = set()


def digest(path, cancel_event=None):
    protected = protected_file(path)
    if protected is not None:
        return protected.digest(cancel_event)
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Применение отменено")
            block = stream.read(1024*1024)
            if not block:
                break
            value.update(block)
    return value.hexdigest()


def move_exclusive(source, destination, expected, on_created=None):
    """Move without replacing another file, including across filesystem volumes."""
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise FileExistsError("Итоговый путь уже занят: "+str(destination))
    protected = protected_file(source)
    if protected is not None:
        protected.move_to(destination, expected, on_created=on_created)
        return
    if os.name == "nt" or sys.platform == "darwin":
        protected = LockedFile(source)
        try:
            protected.move_to(destination, expected, on_created=on_created)
        finally:
            protected.close()
        return
    if digest(source) != expected:
        raise OSError("Исходный файл изменился после поиска: " + str(source))
    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError:
        copy_exclusive(source, destination, expected)
    source.unlink()


def copy_exclusive(source, destination, expected):
    source, destination = Path(source), Path(destination)
    if os.name == "nt" or sys.platform == "darwin":
        protected = protected_file(source)
        owned = protected is None
        if owned:
            protected = LockedFile(source)
        target = None
        try:
            target = protected.copy_to(destination, expected)
        finally:
            if target is not None:
                target.close()
            if owned:
                protected.close()
        return
    created = False
    try:
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            created = True
            shutil.copyfileobj(incoming, outgoing, 1024*1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if digest(destination) != expected:
            raise OSError("Проверка копирования не пройдена: "+str(destination))
        shutil.copystat(source, destination)
    except Exception:
        if created:
            destination.unlink()
        raise


def outcome_for_batch(model, batch):
    counts = Counter(a["kind"] for a in batch["actions"])
    remaining = len(model.visible_groups())
    return {"kind": "applied", "title": "Изменения применены" if remaining else "Сравнение завершено",
            "body": f"{'В папку' if batch.get('delete_policy') == 'folder' else 'В корзину'}: {counts['recycle'] + counts['replace']} · замен: {counts['replace']} · перемещений: {counts['move']}",
            "detail": (f"Осталось групп для сравнения: {remaining}. " if remaining else "Групп для сравнения больше нет. ")+
                      "Прогресс сравнения сохранён. Выполненные действия повторно не применяются." +
                      (" Удаляемые файлы перемещены в папку: " + batch["delete_folder"]
                       if batch.get("delete_policy") == "folder" else "")}


def journal_path(filename):
    return Path(str(filename)+".apply.json")


def create_journal(path, journal):
    created = False
    try:
        with path.open("x", encoding="utf-8") as stream:
            created = True
            json.dump(journal, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        if created:
            path.unlink()
        raise


def rollback(journal):
    """Only restore known bytes; an unexpected file is never overwritten."""
    errors = []
    moving = journal["moving"]
    originals = {entry["source"]: entry["sha256"] for entry in journal["removed"]}
    # Free destinations first, so cycles and same-name replacements can be restored.
    for entry in reversed(moving):
        source, held, destination = map(Path, (entry["source"], entry["held"], entry["destination"]))
        try:
            if destination.exists() and not source.exists():
                actual = digest(destination)
                if not entry.get("placing") and actual == originals.get(str(destination)):
                    continue  # This replacement target has not been recycled yet.
                if actual != entry["sha256"]:
                    raise OSError("Итоговый файл изменён другим приложением: "+str(destination))
                if not held.exists():
                    move_exclusive(destination, held, entry["sha256"])
                elif digest(held) == entry["sha256"]:
                    destination.unlink()  # An interrupted hard-link move left both names.
        except Exception as error:
            errors.append(str(error))
    for entry in moving:
        source, held = Path(entry["source"]), Path(entry["held"])
        try:
            if held.exists():
                if digest(held) != entry["sha256"]:
                    raise OSError("Временный файл изменён другим приложением: "+str(held))
                if source.exists():
                    if digest(source) != entry["sha256"]:
                        raise OSError("Исходный путь занят другим файлом: "+str(source))
                    held.unlink()
                else:
                    move_exclusive(held, source, entry["sha256"])
        except Exception as error:
            errors.append(str(error))
    # Folder removals use the same staged/verified move as ordinary moves.
    for entry in reversed(journal["removed"]):
        if not entry.get("destination") or not entry.get("placing"):
            continue
        destination, held = Path(entry["destination"]), Path(entry["held"])
        try:
            if destination.exists():
                target = LockedFile(destination)
                try:
                    # A racing application may have won this name, even with identical bytes.
                    if target.identity() != entry.get("placed_identity"):
                        continue
                    if target.digest() != entry["sha256"]:
                        raise OSError("Файл в папке для удаляемых изменён: " + str(destination))
                    if not held.exists():
                        target.move_to(held, entry["sha256"])
                    elif digest(held) == entry["sha256"]:
                        target.delete()
                finally:
                    target.close()
        except Exception as error:
            errors.append(str(error))
    for entry in journal["removed"]:
        source = Path(entry["source"])
        try:
            if source.exists():
                if digest(source) != entry["sha256"]:
                    raise OSError("Исходный путь занят другим файлом: "+str(source))
                if entry.get("held") and Path(entry["held"]).exists():
                    held = Path(entry["held"])
                    if digest(held) != entry["sha256"]:
                        raise OSError("Временный файл изменён: " + str(held))
                    held.unlink()
                continue
            held = Path(entry["held"]) if entry.get("held") else None
            if held is not None and held.is_file() and digest(held) == entry["sha256"]:
                move_exclusive(held, source, entry["sha256"])
                continue
            backup = Path(entry["backup"]) if entry.get("backup_ready") else None
            if backup is not None and backup.is_file() and digest(backup) == entry["sha256"]:
                copy_exclusive(backup, source, entry["sha256"])
                continue
            recycled = Path(entry.get("recycled") or "")
            if not entry.get("recycled") or not recycled.is_file() or digest(recycled) != entry["sha256"]:
                raise OSError("Восстановите файл из системной корзины: "+str(source))
            # Keep the recycled copy until the original has been verified and restored.
            copy_exclusive(recycled, source, entry["sha256"])
        except Exception as error:
            errors.append(str(error))
    if errors:
        raise OSError("Не удалось полностью восстановить файлы:\n"+"\n".join(errors))


def recover_project(filename):
    """Complete or reverse an interrupted application before loading its project."""
    with project_lock(filename):
        _recover_project_locked(filename)


def cleanup_transaction(journal):
    for entry in journal["removed"]:
        if entry.get("backup"):
            backup = Path(entry["backup"])
            if backup.exists():
                if digest(backup) != entry["sha256"]:
                    if entry.get("backup_ready"):
                        raise OSError("Восстановительная копия изменена: " + str(backup))
                else:
                    backup.unlink()
        if entry.get("held"):
            folder = Path(entry["held"]).parent
            # New journals use a separate directory so the bin retains the file name.
            if folder.name == ".comparison-" + journal["id"] and folder.exists():
                folder.rmdir()


def _recover_project_locked(filename):
    pending = journal_path(filename)
    if not pending.exists():
        return
    journal = json.loads(pending.read_text(encoding="utf-8"))
    if journal.get("kind") != "comparison-file-transaction" or Path(journal["project"]).resolve() != Path(filename).resolve():
        raise ValueError("Неверный журнал файловых операций: "+str(pending))
    saved = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
    from action_plan import ActionPlan
    baseline = ActionPlan(saved["review"], saved.get("review_path"))
    baseline.restore_state(journal["before_state"])
    expected_removed = baseline.recycles | baseline.replacements.keys()
    expected_moving = {p for p in baseline.files if p not in baseline.inactive
                       and Path(baseline.effective_path(p)).resolve() != Path(baseline.physical_path(p)).resolve()}
    if ({e["id"] for e in journal["removed"]} != expected_removed
            or {e["id"] for e in journal["moving"]} != expected_moving):
        raise ValueError("Состав журнала не соответствует сохранённым решениям")
    for entry in journal["removed"]+journal["moving"]:
        path = entry["id"]
        if entry["source"] != baseline.physical_path(path) or entry["sha256"] != baseline.files[path]["sha256"]:
            raise ValueError("Пути журнала не соответствуют сохранённому сравнению")
        if "held" in entry:
            source = Path(entry["source"])
            if entry in journal["removed"]:
                held = source.parent / (".comparison-" + journal["id"]) / source.name
                backup = source.with_name("."+source.name+".comparison-"+journal["id"]+".backup")
                if entry.get("backup") != str(backup):
                    raise ValueError("Неверный путь восстановительной копии")
                if baseline.delete_mode == "folder":
                    destination = Path(entry.get("destination", ""))
                    valid_name = (destination.name == source.name or re.fullmatch(
                        re.escape(source.stem) + r" \([1-9][0-9]*\)" + re.escape(source.suffix), destination.name))
                    forbidden = {Path(baseline.physical_path(p)).resolve() for p in baseline.files}
                    forbidden.update(Path(baseline.effective_path(p)).resolve() for p in baseline.files if p not in baseline.inactive)
                    if (not destination.is_absolute() or destination.parent.resolve() != Path(baseline.delete_folder).resolve()
                            or not valid_name or destination.resolve() in forbidden):
                        raise ValueError("Неверный путь для удаляемого файла в журнале")
                    identity = entry.get("placed_identity")
                    if entry.get("placing") and (not isinstance(identity, list) or len(identity) != 3
                            or any(type(value) is not int or value < 0 for value in identity)):
                        raise ValueError("Неверная идентичность перемещённого файла в журнале")
                elif entry.get("destination"):
                    raise ValueError("Папка удаления не соответствует сохранённому сравнению")
            else:
                held = source.with_name("."+source.name+".comparison-"+journal["id"]+".hold")
                if entry["destination"] != baseline.effective_path(path):
                    raise ValueError("Неверный итоговый путь в журнале")
            if entry["held"] != str(held):
                raise ValueError("Неверный путь временного файла в журнале")
    folder_targets = [Path(e["destination"]).resolve() for e in journal["removed"] if e.get("destination")]
    if len(set(folder_targets)) != len(folder_targets):
        raise ValueError("Несколько удаляемых файлов направлены в один путь журнала")
    if not any(b.get("id") == journal["id"] for b in saved["state"].get("applied_batches", [])):
        owner = journal.get("owner", {})
        active = str(pending.resolve()) in _ACTIVE
        if owner.get("pid") and owner["pid"] != os.getpid():
            try:
                active = abs(psutil.Process(owner["pid"]).create_time()-owner["created"]) < .01
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                active = True
        if active:
            raise OSError("Это сравнение сейчас применяется другим экземпляром приложения")
        rollback(journal)
    cleanup_transaction(journal)
    pending.unlink()


def apply_files(model, filename, view=None, progress=None, cancel_event=None, recycler=None):
    filename = model.validate_save_path(filename)
    with project_lock(filename):
        from project import check_project_version
        check_project_version(model, filename)
        return _apply_files_locked(model, filename, view, progress, cancel_event, recycler)


def _apply_files_locked(model, filename, view, progress, cancel_event, recycler):
    from project import save_project
    if journal_path(filename).exists():
        raise OSError("Сначала загрузите сохранённое сравнение для восстановления прерванного применения")
    model._validate(check_disk=True)
    plan = model.build()
    actions = plan["actions"]
    if not actions:
        return None
    removed_ids = model.recycles | model.replacements.keys()
    removal_destinations = {a["path"]: a["destination"] for a in actions if a["kind"] == "recycle"}
    removal_destinations.update({a["target"]: a["old_file_destination"] for a in actions if a["kind"] == "replace"})
    if removed_ids and model.delete_mode == "folder" and not Path(model.delete_folder).is_dir():
        raise OSError("Папка для удаляемых файлов недоступна: " + model.delete_folder)
    final_paths = {p: model.effective_path(p) for p in model.files if p not in model.inactive}
    affected = removed_ids | model.moves.keys() | set(model.replacements.values())
    transaction = uuid.uuid4().hex
    moving, removed = [], []
    pending = journal_path(filename)
    snapshot = model._snapshot()
    history, redo = deepcopy(model.history), deepcopy(model.redo_history)
    journal = None
    done = 0
    recycle = recycler or recycle_file
    def tick():
        nonlocal done
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Применение отменено")
        done += 1
        if progress:
            progress(done, total)
    _ACTIVE.add(str(pending.resolve()))
    try:
        with protected_files() as guards:
            for index, path in enumerate(sorted(affected), 1):
                source = Path(model.physical_path(path))
                guard = LockedFile(source)
                guards.append(guard)
                if guard.digest(cancel_event) != model.files[path]["sha256"]:
                    raise OSError("Исходный файл изменился после поиска: "+str(source))
                entry = {"id": path, "source": str(source), "sha256": model.files[path]["sha256"]}
                if path in removed_ids:
                    entry.update(held=str(source.parent/(".comparison-"+transaction)/source.name),
                                 backup=str(source.with_name("."+source.name+".comparison-"+transaction+".backup")))
                    if model.delete_mode == "folder":
                        entry["destination"] = removal_destinations[path]
                    removed.append(entry)
                else:
                    destination = Path(final_paths[path])
                    if source.resolve() != destination.resolve():
                        if not destination.parent.is_dir():
                            raise OSError("Папка назначения недоступна: "+str(destination.parent))
                        entry.update(destination=str(destination), held=str(source.with_name("."+source.name+".comparison-"+transaction+".hold")))
                        moving.append(entry)
                if progress:
                    progress(index, len(affected))
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Применение отменено")
            # The OS lock covers preflight, baseline, journal and checkpoint.
            with project_writer(filename):
                save_project(model, filename, view)
            journal = {"kind": "comparison-file-transaction", "id": transaction, "project": str(filename),
                       "owner": {"pid": os.getpid(), "created": psutil.Process().create_time()},
                       "before_state": model.export_state(), "moving": moving, "removed": removed}
            create_journal(pending, journal)
            total = len(moving)*2+len(removed)*2
            for entry in moving+removed:
                tick()
                if entry in removed:
                    Path(entry["held"]).parent.mkdir(exist_ok=True)
                move_exclusive(entry["source"], entry["held"], entry["sha256"])
            for entry in removed:
                guard = protected_file(entry["held"])
                guards.append(guard.copy_to(entry["backup"], entry["sha256"]))
                entry["backup_ready"] = True
                write_json(pending, journal)  # Durable recovery bytes before the shell action.
                tick()
                if model.delete_mode == "folder":
                    entry["placing"] = True
                    entry["placed_identity"] = guard.identity()
                    write_json(pending, journal)
                    def record_copy_identity(target):
                        entry["placed_identity"] = target.identity()
                        write_json(pending, journal)
                    move_exclusive(entry["held"], entry["destination"], entry["sha256"],
                                   on_created=record_copy_identity)
                else:
                    guard.allow_recycle()
                    entry["recycled"] = str(recycle(entry["held"]))
                write_json(pending, journal)
                guard.close()
            for entry in moving:
                tick()
                entry["placing"] = True
                write_json(pending, journal)
                move_exclusive(entry["held"], entry["destination"], entry["sha256"])
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Применение отменено")
            model.committed_recycles.update(removed_ids)
            model.committed_paths = {p: d for p, d in final_paths.items() if d != p}
            model.physical_paths = {p: d for p, d in model.physical_paths.items() if p in model.committed_recycles}
            model.physical_paths.update({p: d for p, d in final_paths.items()
                                         if d != model.files[p].get("physical_path", p)})
            if model.delete_mode == "folder":
                model.physical_paths.update(removal_destinations)
            batch = {"id": transaction, "at": datetime.now(timezone.utc).isoformat(),
                     "mode": "applied", "execution_enabled": True, "actions": actions,
                     "delete_policy": model.delete_mode, "delete_folder": model.delete_folder}
            model.applied_batches.append(batch)
            model.recycles.clear()
            model.moves.clear()
            model.replacements.clear()
            model.history.clear()
            model.redo_history.clear()
            saved_view = dict(view or {}, page="outcome", outcome=outcome_for_batch(model, batch))
            with project_writer(filename):
                save_project(model, filename, saved_view)
    except Exception as error:
        model._restore(snapshot)
        model.history, model.redo_history = history, redo
        try:
            if journal is not None and pending.exists():
                rollback(journal)
                cleanup_transaction(journal)
                pending.unlink()
        except Exception as recovery_error:
            raise OSError(f"{error}\n{recovery_error}\nЖурнал восстановления: {pending}") from error
        raise
    finally:
        _ACTIVE.discard(str(pending.resolve()))
    # A committed project is authoritative even if journal cleanup is interrupted.
    try:
        cleanup_transaction(journal)
        pending.unlink()
    except OSError:
        pass
    return batch
