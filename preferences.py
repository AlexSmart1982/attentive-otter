"""Persistent UI preferences and deterministic, advisory metadata ranking."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path

from storage import write_json


RULE_LABELS = {"heic": "Формат HEIC / HEIF", "resolution": "Большее разрешение (число пикселей)",
               "date": "Более ранняя дата", "camera": "Заполненное поле камеры",
               "exif": "Больше заполненных EXIF-тегов"}
DATE_LABELS = {"auto": "Съёмка → создание → изменение",
               "captured_at": "Только дата съёмки", "created_at": "Только создание файла",
               "modified_at": "Только изменение файла"}
THEME_LABELS = {"system": "Системная", "dark": "Тёмная", "light": "Светлая"}
DELETE_MODES = {"recycle_bin": "Корзина Windows", "folder": "Выбранная папка"}
SEARCH_MODES = {"strict": ("Строгий", .97, "Нынешняя граница: копии и самые близкие кадры"),
                "close": ("Близкое сходство", .95, "Больше кадров серии в одной группе"),
                "extended": ("Расширенный", .92, "Шире окрестность; возможны посторонние кадры")}


@dataclass
class Preferences:
    cpu_percent: int = 40
    rules: list = field(default_factory=lambda: [{"key": k, "enabled": True} for k in RULE_LABELS])
    date_source: str = "auto"
    highlight_best: bool = True
    suggest_best: bool = True
    theme: str = "system"
    search_mode: str = "strict"
    delete_mode: str = "recycle_bin"
    delete_folder: str = ""

    @classmethod
    def from_dict(cls, data):
        result = cls()
        for key, choices in (("theme", THEME_LABELS), ("search_mode", SEARCH_MODES), ("delete_mode", DELETE_MODES)):
            if isinstance(data.get(key), str) and data[key] in choices:
                setattr(result, key, data[key])
        folder = data.get("delete_folder")
        if isinstance(folder, str) and folder.strip() and Path(folder).is_absolute():
            result.delete_folder = str(Path(folder).resolve())
        if result.delete_mode == "folder" and not result.delete_folder:
            result.delete_mode = "recycle_bin"
        cpu = data.get("cpu_percent")
        if type(cpu) is int and 5 <= cpu <= 100:
            result.cpu_percent = cpu
        if isinstance(data.get("date_source"), str) and data["date_source"] in DATE_LABELS:
            result.date_source = data["date_source"]
        for key in ("highlight_best", "suggest_best"):
            if type(data.get(key)) is bool:
                setattr(result, key, data[key])
        if isinstance(data.get("rules"), list):
            rules, seen = [], set()
            for rule in data["rules"]:
                if (isinstance(rule, dict) and isinstance(rule.get("key"), str) and rule["key"] in RULE_LABELS
                        and rule["key"] not in seen and type(rule.get("enabled")) is bool):
                    rules.append({"key": rule["key"], "enabled": rule["enabled"]})
                    seen.add(rule["key"])
            rules.extend(r for r in result.rules if r["key"] not in seen)
            result.rules = rules
        return result

    def save(self, path):
        write_json(path, asdict(self))

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            return cls.from_dict(data) if isinstance(data, dict) else cls()
        except (OSError, ValueError):
            return cls()

    @property
    def enabled_rules(self):
        return [r["key"] for r in self.rules if r["enabled"]]


def parse_date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    # EXIF date separators differ from ISO; keep fractional seconds and offsets.
    if len(value) >= 10 and value[4] == ":" and value[7] == ":":
        value = value[:4]+"-"+value[5:7]+"-"+value[8:]
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()
    except ValueError:
        return None


def preferred_date(row, settings):
    keys = ("captured_at", "created_at", "modified_at") if settings.date_source == "auto" else (settings.date_source,)
    for key in keys:
        if parse_date(row.get(key)) is not None:
            return row[key], key
    return None, None


def quality_values(row, settings):
    pixels = (row.get("stored_width") or 0)*(row.get("stored_height") or 0)
    value, _ = preferred_date(row, settings)
    date = parse_date(value)
    detected = str(row.get("detected_format") or "").upper()
    heic = detected in ("HEIF", "HEIC") if detected else Path(row["path"]).suffix.lower() in (".heic", ".heif")
    return {"heic": int(heic), "resolution": pixels if pixels > 0 else None, "date": -date if date is not None else None,
            "camera": int(bool(str(row.get("camera") or "").strip())),
            "exif": exif_completeness(row)}


def exif_completeness(row):
    # Reviews exported before this field existed remain usable.
    return row.get("exif_filled_tags", row.get("exif_tags")) or 0


def recommendations(rows, settings):
    """Ordered rules break ties in preceding rules. Exact ties remain ties."""
    if not rows or not settings.enabled_rules:
        return set()
    scores = {}
    for row in rows:
        values = quality_values(row, settings)
        scores[row["path"]] = tuple(values[k] if values[k] is not None else float("-inf")
                                    for k in settings.enabled_rules)
    best = max(scores.values())
    return {path for path, score in scores.items() if score == best}


def recommendation_reasons(rows, settings, paths=None):
    """Explain the existing group ranking without reading any source files.

    A losing card is compared with a group winner. A winning card is compared
    with the other displayed card, or the closest losing member of the group.
    Thus a pair of non-winning cards never gains a false group recommendation.
    """
    files = {row["path"]: row for row in rows}
    paths = [p for p in (paths if paths is not None else files) if p in files]
    result = {}
    rules = settings.enabled_rules
    if not settings.suggest_best or not rules or len(files) < 2:
        text = ("Рекомендации выключены." if not settings.suggest_best else
                "Нет включённых правил рекомендации." if not rules else
                "Нет других активных файлов для сравнения.")
        short = ("Советы выключены." if not settings.suggest_best else
                 "Нет включённых правил." if not rules else "Других файлов нет.")
        return {p: {"text": text, "short_text": short, "detail": text, "rule": None} for p in paths}
    values = {p: quality_values(row, settings) for p, row in files.items()}
    scores = {p: tuple(values[p][key] if values[p][key] is not None else float("-inf")
                       for key in rules) for p in files}
    best = max(scores.values())
    winners = {p for p in files if scores[p] == best}
    losers = [p for p in files if p not in winners]

    def value_text(path, key, compact=False):
        row = files[path]
        if key == "date":
            value, source = preferred_date(row, settings)
            if source is None:
                return "дата отсутствует"
            normalized = value.strip()
            if normalized[4] == ":":
                normalized = normalized[:4]+"-"+normalized[5:7]+"-"+normalized[8:]
            date = datetime.fromisoformat(normalized).strftime("%d.%m.%Y" if compact else "%d.%m.%Y %H:%M:%S")
            label = {"captured_at": "EXIF", "created_at": "создан, Windows",
                     "modified_at": "изменён, Windows"}[source]
            return label+" · "+date
        if key == "resolution":
            return (f"{row.get('stored_width')}×{row.get('stored_height')}"
                    if values[path][key] is not None else "неизвестно")
        if key == "heic":
            return str(row.get("detected_format") or Path(path).suffix or "неизвестен")
        if key == "camera":
            return str(row.get("camera") or "не указана").strip() or "не указана"
        return f"заполненных тегов: {exif_completeness(row)}"

    labels = {"heic": "формат", "resolution": "разрешение", "date": "дата",
              "camera": "камера", "exif": "EXIF"}
    for path in paths:
        if not losers:
            detail = "Преимуществ по включённым правилам в этой группе нет. Решение за вами."
            if "date" in rules:
                detail += "\nИспользуется: "+value_text(path, "date")+"."
                if settings.date_source == "auto":
                    detail += " При одинаковой дате EXIF даты Windows не разрывают равенство."
            result[path] = {"text": "Равноценны по включённым правилам.",
                            "short_text": "Равноценны по правилам.", "detail": detail, "rule": None}
            continue
        if path in winners:
            reference = next((p for p in paths if p != path and p in losers), None)
            reference = reference or max(losers, key=lambda p: scores[p])
            lead = "Рекомендуем" if len(winners) == 1 else "Один из лучших"
        else:
            reference = next(p for p in files if p in winners)
            lead = "Уступает в группе"
        key = next(k for k in rules if values[path][k] != values[reference][k])
        text = lead+" · "+("" if key == "date" else labels[key]+": ")+value_text(path, key, compact=True)+"."
        detail = ("Решающее правило: "+RULE_LABELS[key]+".\nЭтот файл: "+value_text(path, key)+
                  ".\nСравнение с "+Path(reference).name+": "+value_text(reference, key)+
                  ".\nБолее приоритетные правила дали равенство. Рекомендация относится к активной группе.")
        if key == "date":
            sources = {preferred_date(files[p], settings)[1] for p in (path, reference)}
            if sources - {"captured_at", None}:
                text += " Это не дата съёмки."
                detail += "\nДаты Windows относятся к файлу и не устанавливают дату съёмки."
            if len(sources - {None}) > 1:
                detail += "\nСравниваются разные источники дат; выбор следует настройке, а не доказывает происхождение."
        short_text = ("" if key == "date" else labels[key]+": ")+value_text(path, key, compact=True)
        if key == "date":
            _, source = preferred_date(files[path], settings)
            if source is not None:
                caption = {"captured_at": "EXIF", "created_at": "Создан", "modified_at": "Изменён"}[source]
                short_text = caption+": "+value_text(path, key, compact=True).rsplit(" · ", 1)[-1]
                if source != "captured_at":
                    short_text += "\nWindows ≠ съёмка"
        result[path] = {"text": text, "short_text": short_text, "detail": detail, "rule": key}
    return result


def best_parameters(rows, settings):
    """Highlight advantages only: equal and missing values have no winner."""
    values = {r["path"]: quality_values(r, settings) for r in rows}
    result = {}
    for key in settings.enabled_rules:
        scores = {p: v[key] for p, v in values.items()}
        known = [v for v in scores.values() if v is not None]
        if not known or len(set(scores.values())) < 2:
            result[key] = set()
            continue
        best = max(known)
        result[key] = {p for p, v in scores.items() if v == best and (key not in ("heic", "camera", "exif") or best > 0)}
    return result
