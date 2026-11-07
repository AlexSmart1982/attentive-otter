"""Verify the exact application inputs recorded for this release snapshot."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
info = json.loads((root / "build-info.json").read_text(encoding="utf-8"))
for relative, expected in info["application_source_sha256"].items():
    with (root / relative).open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise SystemExit("Released source differs: " + relative)
print(f"Verified {len(info['application_source_sha256'])} released application inputs.")
