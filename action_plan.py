"""Global file intentions and a virtual comparison queue. No file execution."""

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

from storage import write_json
from system_trash import TRASH_DESTINATION


class ActionPlan:
    def __init__(self, document, document_path=None):
        self.document_path = Path(document_path or ("embedded-review.json" if isinstance(document, dict) else document)).resolve()
        self.document = deepcopy(document) if isinstance(document, dict) else json.loads(self.document_path.read_text(encoding="utf-8-sig"))
        self.files = {r["path"]: r for r in self.document["files"]}
        if len(self.files) != len(self.document["files"]):
            raise ValueError("A file must occur once in the review")
        self.groups = {}
        for path, row in self.files.items():
            self.groups.setdefault(str(row["group_id"]), []).append(path)
        self.recycles, self.kept, self.replacements = set(), set(), {}
        self.references = {}
        self.seen = set()
        self.moves = {}
        self.committed_recycles, self.committed_paths = set(), {}
        self.physical_paths = {}
        self.applied_batches = []
        self.history = deque(maxlen=50)
        self.redo_history = deque(maxlen=50)
        self.delete_mode, self.delete_folder = "recycle_bin", ""
        self._intent_paths = {}
        self._removal_preview = None

    def configure_deletion(self, mode, folder=""):
        if mode not in ("recycle_bin", "folder") or not isinstance(folder, str):
            raise ValueError("Некорректный способ удаления")
        if mode == "folder" and (not folder or not Path(folder).is_absolute()):
            raise ValueError("Выберите абсолютный путь к папке для удаляемых файлов")
        self.delete_mode = mode
        self.delete_folder = str(Path(folder).resolve()) if folder else ""
        self._removal_preview = None

    @property
    def deletion_label(self):
        return "В папку" if self.delete_mode == "folder" else "В корзину"

    @property
    def deletion_description(self):
        return "в папку: " + self.delete_folder if self.delete_mode == "folder" else TRASH_DESTINATION

    def removal_destinations(self, check_disk=True):
        removed = self.recycles | self.replacements.keys()
        if self.delete_mode == "recycle_bin":
            return {path: "recycle_bin" for path in removed}
        if not removed:
            return {}
        if not check_disk:
            return self._preview_removals(removed)
        root = Path(self.delete_folder)
        inactive = self.inactive
        reserved = {Path(self.physical_path(p)).resolve() for p in self.files}
        targets = {s: t for t, s in self.replacements.items()}
        reserved.update(Path(self.destination(targets[p], p) if p in targets else
                             self.moves.get(p, self.base_path(p))).resolve()
                        for p in self.files if p not in inactive)
        result, counters = {}, {}
        for path in sorted(removed):
            name = Path(self.physical_path(path)).name
            candidate, index = root/name, counters.get(name, 0)
            while candidate.resolve() in reserved or candidate.exists():
                index += 1
                candidate = root/f"{Path(name).stem} ({index}){Path(name).suffix}"
            counters[name] = index
            reserved.add(candidate.resolve())
            result[path] = str(candidate)
        return result

    def _preview_removals(self, removed):
        # UI destinations are estimates, never the execution contract. The strict
        # allocator above is rerun by build()/preflight before touching any file.
        key = (self.delete_folder, frozenset(removed), tuple(self.replacements.items()),
               tuple(self.moves.items()), frozenset(self.committed_recycles),
               tuple(self.committed_paths.items()), tuple(self.physical_paths.items()))
        if self._removal_preview is not None and self._removal_preview[0] == key:
            return self._removal_preview[1].copy()
        root = Path(self.delete_folder)
        inactive = self.inactive
        targets = {s: t for t, s in self.replacements.items()}
        reserved = {Path(self.physical_path(p)) for p in self.files}
        reserved.update(Path(self.destination(targets[p], p) if p in targets else
                             self.moves.get(p, self.base_path(p)))
                        for p in self.files if p not in inactive)
        try:
            with os.scandir(root) as entries:
                reserved.update(root/entry.name for entry in entries)
        except OSError:
            pass  # An unavailable folder is reported by strict apply preflight.
        result, counters = {}, {}
        for path in sorted(removed):
            name = Path(self.physical_path(path)).name
            candidate, index = root/name, counters.get(name, 0)
            while candidate in reserved:
                index += 1
                candidate = root/f"{Path(name).stem} ({index}){Path(name).suffix}"
            counters[name] = index
            reserved.add(candidate)
            result[path] = str(candidate)
        self._removal_preview = key, result
        return result.copy()

    @property
    def inactive(self):
        return self.recycles | self.replacements.keys() | self.committed_recycles

    def visible_groups(self):
        return [g for g, members in self.groups.items() if len(set(members)-self.committed_recycles) > 1]

    def physical_path(self, path):
        return self.physical_paths.get(path, self.files[path].get("physical_path", path))

    def base_path(self, path):
        return self.committed_paths.get(path, path)

    def effective_path(self, path):
        if path in self.committed_recycles:
            return self.physical_path(path)
        target = next((t for t, s in self.replacements.items() if s == path), None)
        return self.destination(target, path) if target is not None else self.moves.get(path, self.base_path(path))

    def live(self, group):
        inactive = self.inactive
        return [p for p in self.groups[str(group)] if p not in inactive]

    def next_pair(self, group, initialize=True):
        group = str(group)
        live = self.live(group)
        if not live:
            return None, None
        reference = self.references.get(group)
        if reference not in live:
            reference = live[0]
            if initialize:
                self.references[group] = reference
        candidate = next((p for p in live if p != reference
                          and frozenset((reference, p)) not in self.seen), None)
        return reference, candidate

    def set_reference(self, path, record=False):
        if path not in self.files or path in self.inactive:
            raise ValueError("Choose an active file")
        snapshot = self._snapshot() if record else None
        self.references[str(self.files[path]["group_id"])] = path
        if record:
            self._record(snapshot)

    def destination(self, target, source):
        row = self.files[source]
        suffix = Path(source).suffix
        actual = row.get("detected_format")
        correct = {"HEIF": ".heic", "JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png"}
        allowed = {"HEIF": {".heic", ".heif"}, "JPEG": {".jpg", ".jpeg"},
                   "MPO": {".jpg", ".jpeg"}, "PNG": {".png"}}
        if actual in allowed and suffix.lower() not in allowed[actual]:
            suffix = correct[actual]
        return str(Path(self.moves.get(target, self.base_path(target))).with_suffix(suffix))

    def _snapshot(self):
        return (self.recycles.copy(), self.kept.copy(), self.replacements.copy(),
                self.references.copy(), self.seen.copy(), self.moves.copy(),
                self.committed_recycles.copy(), self.committed_paths.copy(), deepcopy(self.applied_batches), self.physical_paths.copy())

    def _restore(self, snapshot):
        (self.recycles, self.kept, self.replacements, self.references, self.seen, self.moves,
         self.committed_recycles, self.committed_paths, self.applied_batches, self.physical_paths) = snapshot
        self._intent_paths.clear()
        self._removal_preview = None

    def _record(self, snapshot):
        if snapshot != self._snapshot():
            self.history.append(snapshot)
            self.redo_history.clear()

    def _validate(self, check_disk=False):
        if self.delete_mode not in ("recycle_bin", "folder") or (self.delete_mode == "folder" and
                (not self.delete_folder or not Path(self.delete_folder).is_absolute())):
            raise ValueError("Некорректная папка или способ удаления")
        known = self.files.keys()
        inactive = self.inactive
        if any(not Path(p).is_absolute() for p in self.physical_paths.values()):
            raise ValueError("Физическое расположение файла должно быть абсолютным")
        vacated = ({Path(self.physical_path(p)).resolve() for p in self.recycles | self.replacements.keys()}
                   if check_disk else set())
        if not (self.recycles | self.kept | self.committed_recycles | self.moves.keys()
                | self.committed_paths.keys() | self.physical_paths.keys() | self.replacements.keys() | set(self.replacements.values())) <= known:
            raise ValueError("В сравнении есть неизвестные файлы")
        if self.recycles & (self.replacements.keys() | self.committed_recycles):
            raise ValueError("Противоречивые решения по файлу")
        if self.kept & inactive:
            raise ValueError("Файл одновременно оставляется и убирается")
        used_sources = set()
        for target, source in self.replacements.items():
            if (target == source or target in self.committed_recycles or source in inactive
                    or source in used_sources or self.files[target]["group_id"] != self.files[source]["group_id"]):
                raise ValueError("Источник замены уже убирается или используется для другой замены")
            used_sources.add(source)
        if self.moves.keys() & (self.recycles | self.committed_recycles | used_sources):
            raise ValueError("Удаляемый файл или источник замены не может иметь отдельное перемещение")
        destinations = set()
        replacement_targets = {s: t for t, s in self.replacements.items()}
        for path in self.files:
            if path in inactive:
                continue
            target = replacement_targets.get(path)
            destination = self.destination(target, path) if target is not None else self.moves.get(path, self.base_path(path))
            dest = Path(destination)
            if not dest.is_absolute():
                raise ValueError("Итоговый путь должен быть абсолютным")
            if check_disk:
                dest = dest.resolve()
            if dest in destinations:
                raise ValueError(f"Несколько файлов направлены в один путь: {dest}")
            destinations.add(dest)
            if check_disk and dest.exists():
                allowed = {Path(self.physical_path(path)).resolve(), Path(self.base_path(path)).resolve()}
                if path in replacement_targets:
                    allowed.add(Path(self.physical_path(replacement_targets[path])).resolve())
                    allowed.add(Path(self.base_path(replacement_targets[path])).resolve())
                if dest not in allowed and dest not in vacated:
                    raise ValueError(f"Итоговый путь уже занят: {dest}")
        if any(len(pair) != 2 or not pair <= known
               or len({str(self.files[p]["group_id"]) for p in pair}) != 1 for pair in self.seen):
            raise ValueError("Некорректные просмотренные пары")
        if any(g not in self.groups or p not in self.groups[g] or p in inactive for g, p in self.references.items()):
            raise ValueError("Некорректный опорный файл")

    def _validate_intentions(self, before):
        """Validate decisions without restatting every unchanged archive file.

        New/changed moves still reject occupied destinations immediately. Canonical
        paths of other planned moves are cached only for intent-time comparisons;
        _validate(check_disk=True) never uses this cache at execution time.
        """
        self._validate()
        old_replacements, old_moves = before[2], before[5]
        changed = {p for p in old_moves.keys() | self.moves.keys()
                   if old_moves.get(p) != self.moves.get(p)}
        for target in old_replacements.keys() | self.replacements.keys():
            old, new = old_replacements.get(target), self.replacements.get(target)
            if old != new or target in changed:
                changed.update(p for p in (old, new) if p is not None)
        if not changed:
            return
        inactive = self.inactive
        targets = {s: t for t, s in self.replacements.items()}
        planned = (self.moves.keys() | targets.keys()) - inactive
        canonical = {}
        def resolve(destination):
            if destination not in self._intent_paths:
                self._intent_paths[destination] = Path(destination).resolve()
            return self._intent_paths[destination]
        for path in planned:
            destination = self.effective_path(path)
            # Refresh edited destinations; other entries are just a UI cache.
            if path in changed:
                self._intent_paths.pop(destination, None)
            dest = resolve(destination)
            if dest in canonical and canonical[dest] != path:
                raise ValueError(f"Несколько файлов направлены в один путь: {dest}")
            canonical[dest] = path
        vacated = None
        for path in changed - inactive:
            dest = resolve(self.effective_path(path))
            if not dest.exists():
                continue
            allowed = {Path(self.physical_path(path)).resolve(), Path(self.base_path(path)).resolve()}
            if path in targets:
                target = targets[path]
                allowed.update((Path(self.physical_path(target)).resolve(), Path(self.base_path(target)).resolve()))
            if dest in allowed:
                continue
            if vacated is None:
                vacated = {Path(self.physical_path(p)).resolve()
                           for p in self.recycles | self.replacements.keys()}
            if dest not in vacated:
                raise ValueError(f"Итоговый путь уже занят: {dest}")

    def choose(self, a, b, action):
        if (a == b or a not in self.files or b not in self.files
                or a in self.inactive or b in self.inactive
                or self.files[a]["group_id"] != self.files[b]["group_id"]):
            raise ValueError("Выберите два активных файла одной группы")
        if action not in ("keep_both", "remove_a", "remove_b", "replace_a", "replace_b", "remove_both"):
            raise ValueError("Unknown action")
        snapshot = self._snapshot()
        try:
            group = str(self.files[a]["group_id"])
            if action == "keep_both":
                self.kept.update((a, b))
            elif action == "remove_both":
                if {a, b} & set(self.replacements.values()):
                    raise ValueError("Файл используется для замены. Сначала отмените эту замену "
                                     "или выберите «Удалить всю группу».")
                self.kept.difference_update((a, b))
                self.moves.pop(a, None)
                self.moves.pop(b, None)
                self.recycles.update((a, b))
                self.references.pop(group, None)
            else:
                loser, winner = (a, b) if action.endswith("a") else (b, a)
                incoming = [target for target, source in self.replacements.items() if source == loser]
                for target in incoming:
                    self.replacements[target] = winner
                self.kept.discard(loser)
                self.kept.add(winner)
                if action.startswith("replace") and not incoming:
                    self.replacements[loser] = winner
                    self.moves.pop(winner, None)
                else:
                    # If the old winner supplied a replacement, retain the sorted
                    # destination and discard this intermediate candidate.
                    self.recycles.add(loser)
                    self.moves.pop(loser, None)
                    if incoming:
                        self.moves.pop(winner, None)
                self.references[group] = winner
            self.seen.add(frozenset((a, b)))
            self._validate_intentions(snapshot)
        except Exception:
            self._restore(snapshot)
            raise
        self._record(snapshot)

    def keep_only(self, group, selected):
        """Keep checked active files, recycle other active members as one undo step."""
        group, selected = str(group), set(selected)
        live = set(self.live(group))
        if not selected or not selected <= live:
            raise ValueError("Отметьте хотя бы один активный файл, который нужно оставить")
        snapshot = self._snapshot()
        try:
            reference = self.references.get(group)
            if reference not in selected:
                reference = next(p for p in self.groups[group] if p in selected)
            removed = live - selected
            for target, source in self.replacements.items():
                if source in removed:
                    self.replacements[target] = reference
            self.kept.difference_update(removed)
            self.kept.update(selected)
            self.recycles.update(removed)
            for path in removed:
                self.moves.pop(path, None)
            for path in self.replacements.values():
                self.moves.pop(path, None)
            self.references[group] = reference
            self.seen.update(frozenset((reference, p)) for p in selected if p != reference)
            self._validate_intentions(snapshot)
        except Exception:
            self._restore(snapshot)
            raise
        self._record(snapshot)

    def discard_group(self, group):
        """An unwanted group supersedes all earlier intentions for its members."""
        group = str(group)
        members = set(self.groups[group])-self.committed_recycles
        snapshot = self._snapshot()
        self.replacements = {t: s for t, s in self.replacements.items() if t not in members}
        self.kept.difference_update(members)
        self.recycles.update(members)
        self.moves = {p: d for p, d in self.moves.items() if p not in members}
        self.references.pop(group, None)
        self._record(snapshot)

    def remove_selected(self, group, selected):
        """Recycle only checked files; leave other files' decisions unchanged."""
        group, selected = str(group), set(selected)
        if not selected or not selected <= set(self.live(group)):
            raise ValueError("Отметьте активные файлы для удаления")
        if selected & set(self.replacements.values()):
            raise ValueError("Выбранный файл используется для замены. Сначала отмените эту замену "
                             "или удалите всю группу.")
        snapshot = self._snapshot()
        try:
            self.kept.difference_update(selected)
            self.recycles.update(selected)
            for path in selected:
                self.moves.pop(path, None)
            if self.references.get(group) in selected:
                self.references.pop(group)
            self._validate_intentions(snapshot)
        except Exception:
            self._restore(snapshot)
            raise
        self._record(snapshot)

    def move_files(self, group, selected, folder):
        selected, folder = set(selected), Path(folder).resolve()
        if not selected or not selected <= set(self.live(group)) or not folder.is_dir():
            raise ValueError("Выберите активные файлы и существующую папку назначения")
        snapshot = self._snapshot()
        try:
            for path in selected:
                owner = next((t for t, s in self.replacements.items() if s == path), path)
                destination = str(folder/Path(self.effective_path(path)).name)
                if Path(destination).resolve() == Path(self.base_path(owner)).resolve():
                    self.moves.pop(owner, None)
                else:
                    self.moves[owner] = destination
            self._validate_intentions(snapshot)
        except Exception:
            self._restore(snapshot)
            raise
        self._record(snapshot)

    def cancel_moves(self, selected):
        snapshot = self._snapshot()
        try:
            for path in selected:
                owner = next((t for t, s in self.replacements.items() if s == path), path)
                self.moves.pop(owner, None)
            self._validate_intentions(snapshot)
        except Exception:
            self._restore(snapshot)
            raise
        self._record(snapshot)

    def reset_group(self, group):
        members = set(self.groups[str(group)])
        snapshot = self._snapshot()
        self.recycles.difference_update(members)
        self.kept.difference_update(members)
        self.replacements = {t: s for t, s in self.replacements.items() if t not in members}
        self.moves = {p: d for p, d in self.moves.items() if p not in members}
        self.seen = {pair for pair in self.seen if not pair & members}
        self.references.pop(str(group), None)
        self._record(snapshot)

    def undo(self):
        if self.history:
            self.redo_history.append(self._snapshot())
            self._restore(self.history.pop())
            return True
        return False

    def redo(self):
        if self.redo_history:
            self.history.append(self._snapshot())
            self._restore(self.redo_history.pop())
            return True
        return False

    def status(self, path):
        if path in self.committed_recycles:
            return "applied"
        if path in self.recycles:
            return "recycle"
        if path in self.replacements:
            return "replaced"
        if path in self.replacements.values():
            return "replacement_source"
        if path in self.moves:
            return "move"
        return "keep" if path in self.kept else "undecided"

    def build(self, check_disk=True):
        self._validate()
        actions = []
        removals = self.removal_destinations(check_disk=check_disk)
        for path in sorted(self.recycles):
            actions.append({"kind": "recycle", "path": path,
                            "expected_sha256": self.files[path]["sha256"], "destination": removals[path]})
        for target, source in sorted(self.replacements.items()):
            destination = self.destination(target, source)
            actions.append({"kind": "replace", "target": target, "source": source,
                            "destination": destination, "old_file_destination": removals[target],
                            "source_disposition": "already_at_destination" if destination == source else "move",
                            "expected_target_sha256": self.files[target]["sha256"],
                            "expected_source_sha256": self.files[source]["sha256"]})
        for path in sorted(self.moves):
            if path not in self.replacements:
                actions.append({"kind": "move", "path": path, "source": self.physical_path(path),
                                "destination": self.effective_path(path), "expected_sha256": self.files[path]["sha256"]})
        return {"schema_version": 2, "mode": "plan", "execution_enabled": True,
                "delete_policy": self.delete_mode, "delete_folder": self.delete_folder, "review": str(self.document_path),
                "sources": self.document.get("source_roots") or [self.document["source_root"]],
                "actions": actions, "kept_files": sorted(self.kept - set(self.replacements.values())),
                "files": [{"path": path, "sha256": row["sha256"], "state": self.status(path)}
                          for path, row in self.files.items()],
                "review_state": {"references": self.references.copy(),
                                 "seen_pairs": [sorted(pair) for pair in sorted(self.seen, key=lambda p: sorted(p))]},
                "applied_state": {"removed": sorted(self.committed_recycles), "paths": self.committed_paths.copy(),
                                  "physical_paths": self.physical_paths.copy(), "batches": deepcopy(self.applied_batches)},
                "execution_contract": "Apply verifies source fingerprints, moves without overwriting unrelated files, "
                                      "sends discarded files to the selected bin/folder and saves the completed comparison."}

    def validate_save_path(self, destination, *, check_source_aliases=True):
        """Skip the archive alias walk only when a checkpoint version guard follows."""
        destination = Path(destination).resolve()
        roots = [Path(p).resolve() for p in self.document.get("source_roots") or [self.document["source_root"]]]
        if (destination.suffix.lower() != ".json" or destination == self.document_path
                or destination == Path(self.document["scan_report"]).resolve()
                or any(destination == root or root in destination.parents for root in roots)
                or any(destination == Path(p) for p in self.files)
                or (check_source_aliases and any(destination == Path(p).resolve() for p in self.files))):
            raise ValueError("Сохраните JSON-план вне папок-источников и отдельно от результата поиска")
        return destination

    def export(self, destination):
        destination = self.validate_save_path(destination)
        data = self.build()
        # Keep the old intention-file API readable by earlier tooling.
        data.update(mode="simulation", execution_enabled=False)
        data["simulation_state"] = data.pop("applied_state")
        write_json(destination, data)
        return data

    def load(self, filename):
        data = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
        self.load_plan(data)

    def load_plan(self, data):
        if data.get("schema_version") != 2 or data.get("mode") not in ("simulation", "plan") or data.get("delete_policy") not in ("recycle_bin", "folder"):
            raise ValueError("Unsupported plan")
        fingerprints = {r["path"]: r["sha256"] for r in data["files"]}
        if len(fingerprints) != len(data["files"]) or fingerprints != {p: r["sha256"] for p, r in self.files.items()}:
            raise ValueError("Состав или контрольные суммы файлов плана отличаются")
        proposed = ActionPlan(self.document, self.document_path)
        proposed.configure_deletion(data["delete_policy"], data.get("delete_folder", ""))
        simulation = data.get("applied_state", data.get("simulation_state", {}))
        proposed.committed_recycles = set(simulation.get("removed", []))
        proposed.committed_paths = dict(simulation.get("paths", {}))
        proposed.physical_paths = dict(simulation.get("physical_paths", {}))
        proposed.applied_batches = deepcopy(simulation.get("batches", []))
        proposed.kept = set(data["kept_files"])
        for action in data["actions"]:
            if action["kind"] == "recycle":
                path = action["path"]
                if path not in self.files or path in proposed.recycles:
                    raise ValueError("Invalid recycle action")
                proposed.recycles.add(path)
            elif action["kind"] == "replace":
                target, source = action["target"], action["source"]
                if (target not in self.files or source not in self.files or target == source
                        or target in proposed.replacements or self.files[target]["group_id"] != self.files[source]["group_id"]
                        or Path(action["destination"]).suffix != Path(proposed.destination(target, source)).suffix):
                    raise ValueError("Invalid replacement")
                proposed.replacements[target] = source
                if action["destination"] != proposed.destination(target, source):
                    proposed.moves[target] = str(Path(action["destination"]).with_suffix(Path(proposed.base_path(target)).suffix))
                if Path(action["destination"]) != Path(proposed.destination(target, source)):
                    raise ValueError("Invalid replacement destination")
            elif action["kind"] == "move":
                path = action["path"]
                if path not in self.files or path in proposed.moves:
                    raise ValueError("Invalid move action")
                proposed.moves[path] = action["destination"]
            else:
                raise ValueError("Unknown plan action")
        if not proposed.kept <= self.files.keys() or proposed.recycles & proposed.replacements.keys():
            raise ValueError("Conflicting file decisions")
        proposed.references = data.get("review_state", {}).get("references", {})
        proposed.seen = {frozenset(p) for p in data.get("review_state", {}).get("seen_pairs", [])}
        if any(len(p) != 2 or not p <= self.files.keys()
               or len({str(self.files[f]["group_id"]) for f in p}) != 1 for p in proposed.seen):
            raise ValueError("Invalid reviewed pairs")
        if any(g not in self.groups or p not in self.groups[g] or p in proposed.inactive
               for g, p in proposed.references.items()):
            raise ValueError("Invalid group reference")
        proposed._validate()
        self._restore(proposed._snapshot())
        self.configure_deletion(proposed.delete_mode, proposed.delete_folder)
        self.history.clear()
        self.redo_history.clear()

    def simulate_apply(self, progress=None, cancel_event=None):
        """Commit a virtual checkpoint. NEVER delete, move or copy archive files."""
        self._validate(check_disk=True)
        data = self.build()
        if not data["actions"]:
            return None
        affected = set(self.recycles) | self.replacements.keys() | set(self.replacements.values()) | self.moves.keys()
        for index, path in enumerate(sorted(affected), 1):
            physical = Path(self.physical_path(path))
            digest = hashlib.sha256()
            with physical.open("rb") as stream:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("Проверка плана отменена")
                    chunk = stream.read(1024*1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            if digest.hexdigest() != self.files[path]["sha256"]:
                raise ValueError(f"Исходный файл изменился после поиска: {physical}")
            if progress:
                progress(index, len(affected))
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Проверка плана отменена")
        final_paths = {p: self.effective_path(p) for p in self.files if p not in self.inactive}
        self.committed_recycles.update(self.recycles | self.replacements.keys())
        self.committed_paths = {p: d for p, d in final_paths.items() if d != p}
        batch = {"id": uuid.uuid4().hex, "at": datetime.now(timezone.utc).isoformat(),
                 "mode": "simulation", "execution_enabled": False, "actions": data["actions"]}
        self.applied_batches.append(batch)
        self.recycles.clear()
        self.replacements.clear()
        self.moves.clear()
        self.history.clear()
        self.redo_history.clear()
        return deepcopy(batch)

    def export_state(self, snapshot=None):
        state = snapshot or self._snapshot()
        return {"recycles": sorted(state[0]), "kept": sorted(state[1]), "replacements": state[2].copy(),
                "references": state[3].copy(), "seen": [sorted(p) for p in state[4]], "moves": state[5].copy(),
                "committed_recycles": sorted(state[6]), "committed_paths": state[7].copy(),
                "applied_batches": deepcopy(state[8]), "physical_paths": state[9].copy(),
                "delete_mode": self.delete_mode, "delete_folder": self.delete_folder}

    def restore_state(self, data):
        snapshot = self._snapshot()
        deletion = self.delete_mode, self.delete_folder
        try:
            self.configure_deletion(data.get("delete_mode", "recycle_bin"), data.get("delete_folder", ""))
            self._restore((set(data["recycles"]), set(data["kept"]), dict(data["replacements"]),
                           dict(data["references"]), {frozenset(p) for p in data["seen"]}, dict(data["moves"]),
                           set(data["committed_recycles"]), dict(data["committed_paths"]), deepcopy(data["applied_batches"]),
                           dict(data.get("physical_paths", {}))))
            self._validate()
        except Exception:
            self._restore(snapshot)
            self.delete_mode, self.delete_folder = deletion
            raise

    def prepare_real_execution(self):
        """Return old virtual checkpoints to pending intentions; never replay them silently."""
        if not self.applied_batches or any(b.get("mode") != "simulation" for b in self.applied_batches):
            return False
        destinations = {p: self.effective_path(p) for p in self.files if p not in self.inactive}
        self.recycles.update(self.committed_recycles | self.replacements.keys())
        self.replacements.clear()
        self.moves = {p: d for p, d in destinations.items() if d != self.physical_path(p)}
        self.committed_recycles.clear()
        self.committed_paths.clear()
        self.applied_batches.clear()
        self.history.clear()
        self.redo_history.clear()
        self._validate()
        return True
