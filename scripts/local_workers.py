"""Manage only this checkout's native workers for the local OrbStack stack.

Run with .venv/bin/python scripts/local_workers.py start|status|stop.
Credentials stay in the ignored .waldo-local.env file, never in process arguments.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import psutil
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".waldo-local"
WORKERS = {"labeler": "celery", "trainer": "training"}


def matching_process(record):
    try:
        process = psutil.Process(record["pid"])
        if process.create_time() != record["created"] or process.cwd() != str(ROOT):
            return None
        if record["hostname"] not in process.cmdline():
            return None
        return process if process.is_running() and process.status() != psutil.STATUS_ZOMBIE else None
    except (psutil.Error, KeyError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "stop"))
    args = parser.parse_args()
    STATE.mkdir(mode=0o700, exist_ok=True)
    env_path = ROOT / ".waldo-local.env"
    if args.action == "start" and not env_path.is_file():
        parser.error("Create .waldo-local.env first; see the local testing documentation.")
    environment = {**os.environ, **{k: v for k, v in dotenv_values(env_path).items() if v is not None}}
    environment["PYTHONUNBUFFERED"] = "1"
    for name, queue in WORKERS.items():
        path = STATE / f"{name}.json"
        record = json.loads(path.read_text()) if path.exists() else {}
        process = matching_process(record) if record else None
        if args.action == "start" and process is None:
            hostname = f"--hostname=waldo-local-{name}@%h"
            command = [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "lib.tasks",
                "worker",
                "--loglevel=info",
                "--concurrency=1",
                "--pool=solo",
                "-Q",
                queue,
                hostname,
            ]
            with (STATE / f"{name}.log").open("ab") as log:
                child = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            process = psutil.Process(child.pid)
            record = {"pid": child.pid, "created": process.create_time(), "hostname": hostname}
            path.write_text(json.dumps(record))
            print(f"{name}: started PID {child.pid}; see {STATE / f'{name}.log'}")
        elif args.action == "stop" and process is not None:
            process.terminate()  # Celery warm shutdown; let active work finish.
            try:
                process.wait(timeout=10)
            except psutil.TimeoutExpired:
                print(f"{name}: shutdown requested; active work may still be finishing")
                continue
            path.unlink(missing_ok=True)
            print(f"{name}: stopped")
        else:
            print(f"{name}: {'running PID ' + str(process.pid) if process else 'stopped'}")


if __name__ == "__main__":
    main()
