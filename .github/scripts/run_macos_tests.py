"""Run every test module in an isolated process and preserve every exit code."""
from pathlib import Path
import subprocess
import sys
failed = []
for path in sorted(Path.cwd().glob("test_*.py")):
    print("\n=== " + path.stem + " ===", flush=True)
    result = subprocess.run([sys.executable, "-B", "-X", "faulthandler", "-m", "unittest", path.stem, "-v"])
    if result.returncode:
        failed.append((path.stem, result.returncode))
print("Module failures:", failed, flush=True)
sys.exit(bool(failed))
