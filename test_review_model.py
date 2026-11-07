import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from review_model import ReviewSession


class ReviewModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v2_decisions_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.originals = self.root / "photos"
        self.originals.mkdir()
        self.files = []
        for i in range(2):
            path = self.originals / f"{i}.jpg"
            path.write_bytes(b"image content " + bytes([i]))
            self.files.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.document = self.root / "review.json"
        self.document.write_text(json.dumps({"source_root": str(self.originals), "files": self.files,
                                            "scan_report": str(self.root / "report.json")}), encoding="utf-8")
        self.session = ReviewSession(self.document)

    def test_choices_round_trip_without_changing_originals(self):
        self.session.choose(self.files[0]["path"], "keep")
        self.session.choose(self.files[1]["path"], "remove")
        self.session.export(self.root / "plan.json")
        restored = ReviewSession(self.document)
        restored.load(self.root / "plan.json")
        self.assertEqual(restored.summary(), {"keep": 1, "remove": 1, "undecided": 0})
        for row in self.files:
            self.assertEqual(hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest(), row["sha256"])

    def test_plan_cannot_overwrite_originals_or_review(self):
        for path in [self.originals / "plan.json", self.document, self.root / "report.json"]:
            with self.assertRaises(ValueError):
                self.session.export(path)

    def test_invalid_plan_is_rejected_without_partial_choices(self):
        bad = {"files": [dict(self.files[0], decision="keep"),
                         dict(self.files[1], decision="remove", sha256="changed")]}
        plan = self.root / "bad.json"
        plan.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.session.load(plan)
        self.assertEqual(self.session.summary()["undecided"], 2)


if __name__ == "__main__":
    unittest.main()
