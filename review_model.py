"""Per-file retention plans; no archive file actions."""

from collections import Counter
import json
from pathlib import Path


DECISIONS = ("keep", "remove", "undecided")


class ReviewSession:
    def __init__(self, document):
        self.document_path = Path(document).resolve()
        self.document = json.loads(self.document_path.read_text(encoding="utf-8-sig"))
        self.files = {row["path"]: row for row in self.document["files"]}
        if len(self.files) != len(self.document["files"]):
            raise ValueError("Each file must occur only once in a review")
        self.decisions = {path: row.get("decision", "undecided") for path, row in self.files.items()}
        if not set(self.decisions.values()) <= set(DECISIONS):
            raise ValueError("Invalid initial decision")

    def choose(self, path, decision):
        if path not in self.files or decision not in DECISIONS:
            raise ValueError("Unknown file or decision")
        self.decisions[path] = decision

    def summary(self):
        counts = Counter(self.decisions.values())
        return {decision: counts[decision] for decision in DECISIONS}

    def export(self, destination):
        destination = Path(destination).resolve()
        source_root = Path(self.document["source_root"]).resolve()
        if (destination.suffix.lower() != ".json" or destination == self.document_path
                or destination == source_root or source_root in destination.parents):
            raise ValueError("Save the JSON plan outside the originals, separately from the review")
        protected = {Path(path).resolve() for path in self.files}
        protected.add(Path(self.document["scan_report"]).resolve())
        if destination in protected:
            raise ValueError("The plan cannot overwrite review data or an original")
        data = {"schema_version": 1, "review": str(self.document_path),
                "summary": self.summary(), "files": [
                    {"path": path, "sha256": row["sha256"], "decision": self.decisions[path]}
                    for path, row in self.files.items()]}
        destination.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        return data

    def load(self, plan):
        data = json.loads(Path(plan).read_text(encoding="utf-8-sig"))
        updates = {}
        for row in data["files"]:
            path, decision = row["path"], row["decision"]
            if (path not in self.files or path in updates or decision not in DECISIONS
                    or row["sha256"] != self.files[path]["sha256"]):
                raise ValueError("Plan contains an unknown, repeated, changed or invalid file entry")
            updates[path] = decision
        self.decisions.update(updates)
