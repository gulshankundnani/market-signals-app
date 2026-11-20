"""The publish gate: python tools/verify_build.py BUILD_FOLDER. Exits 1 (so the site is not published) if the manifest check or the QA sweep finds a failure."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import manifest  # noqa: E402


def main(folder):
    problems = manifest.verify(folder)
    for problem in problems:
        print("FAIL", problem)
    sweep = subprocess.run([sys.executable, str(Path(__file__).with_name("qa_sweep.py")), folder]).returncode
    return 1 if problems or sweep else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]) if len(sys.argv) == 2 else 2)
