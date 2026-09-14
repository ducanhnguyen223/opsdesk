"""One offline verification command, shared by local development and CI."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent
    # Pass runtime essentials, not developer credentials, to test processes.
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "WINDIR", "LANG"}}
    env["PYTHONUNBUFFERED"] = "1"
    commands = [
        [sys.executable, "-m", "pip", "check"],
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        [sys.executable, "evaluate.py"],
        [sys.executable, "demo.py"],
    ]
    for command in commands:
        print("Checking:", " ".join(command[1:]), flush=True)
        subprocess.run(command, cwd=root, env=env, check=True, timeout=180)
    print("Offline backend verification passed. Live model quality is a separate gate.")


if __name__ == "__main__":
    main()
