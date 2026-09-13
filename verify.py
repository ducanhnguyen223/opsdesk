"""One offline verification command, shared by local development and CI."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required for browser-code checks; install it and retry.")
    # Pass runtime essentials, not developer credentials, to test processes.
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "WINDIR", "LANG"}}
    env["PYTHONUNBUFFERED"] = "1"
    commands = [
        [sys.executable, "-m", "pip", "check"],
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        [node, "--check", "web/app.js"],
        [node, "tests/test_web_state.js"],
        [sys.executable, "evaluate.py"],
        [sys.executable, "demo.py"],
    ]
    for command in commands:
        print("Checking:", " ".join(command[1:]), flush=True)
        subprocess.run(command, cwd=root, env=env, check=True, timeout=180)
    print("Offline verification passed. Live model quality and browser UI are separate gates.")


if __name__ == "__main__":
    main()
