"""Auto-reloading dev runner. Restarts the bot when .py files change."""

import os
import subprocess
import sys
import time

from watchfiles import watch


def main():
    watch_dir = os.path.dirname(os.path.abspath(__file__)) or "."
    while True:
        print(f"Starting bot... (watching {watch_dir})", flush=True)
        proc = subprocess.Popen([sys.executable, "main.py"], cwd=watch_dir)
        try:
            for changes in watch(watch_dir, ignore_permission_denied=True):
                py_changes = [p for _, p in changes if p.endswith(".py")]
                if not py_changes:
                    continue
                print(f"\nReloading: {py_changes}", flush=True)
                proc.terminate()
                proc.wait(timeout=5)
                break
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait(timeout=5)
            break
        time.sleep(0.5)


if __name__ == "__main__":
    main()
