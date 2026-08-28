"""Unattended runner: keeps a long job alive across crashes and network drops.

Training and the Trujillo download both take hours, and both must survive a
dropped link, a transient Zenodo failure, or a process dying at 3am. This
restarts the job until it genuinely succeeds, with resume so nothing is redone.

    download  -- resumes from the .part file on disk
    screen    -- resumes from last.pt (optimizer state and epoch included)
    segment   -- resumes from last.pt

Backoff is capped and the attempt budget only decrements when a job makes no
progress, so a job that is merely slow keeps going while one that is genuinely
broken eventually stops.

Usage (foreground):
    python -m ml.supervise screen
    python -m ml.supervise download --part 3

Usage (detached, survives this terminal closing):
    powershell -Command "Start-Process -WindowStyle Hidden -FilePath \\
      '..\\.venv\\Scripts\\python.exe' -ArgumentList '-m','ml.supervise','screen'"
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ml.config import REPO_ROOT

SYSTEM = REPO_ROOT / "main_system"
PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
LOGS = REPO_ROOT / "data" / "logs"


def python_exe() -> str:
    return str(PYTHON) if PYTHON.exists() else sys.executable


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def progress_marker(job: str, part: int, name: str) -> Path:
    """The file whose size/mtime proves the job advanced."""
    if job == "download":
        d = REPO_ROOT / "data" / "raw" / "trujillo" / f"part{part}"
        parts = list(d.glob("*.part")) + list(d.glob("*.7z"))
        return parts[0] if parts else d
    return REPO_ROOT / "data" / "runs" / "training" / name / "weights" / "last.pt"


def measure(path: Path) -> tuple:
    try:
        st = path.stat()
        return (st.st_size, st.st_mtime)
    except OSError:
        return (0, 0.0)


def build_command(job: str, part: int, name: str, epochs: int, batch: int,
                  attempt: int) -> list:
    py = python_exe()
    if job == "download":
        return [py, "-u", "-m", "ml.download", "--dataset", "trujillo",
                "--part", str(part)]
    if job == "screen":
        cmd = [py, "-u", "-m", "ml.train_yolo", "--epochs", str(epochs),
               "--batch", str(batch), "--name", name]
        # First attempt starts fresh only if there is nothing to resume.
        if attempt > 0 or progress_marker("screen", part, name).exists():
            cmd.append("--resume")
        return cmd
    if job == "segment":
        cmd = [py, "-u", "-m", "ml.train_unet", "--epochs", str(epochs),
               "--batch-size", str(batch)]
        last = REPO_ROOT / "data" / "runs" / "training" / "unet-r34" / "last.pt"
        if last.exists():
            cmd += ["--resume", str(last)]
        return cmd
    raise ValueError(job)


def supervise(job: str, part: int, name: str, epochs: int, batch: int,
              max_attempts: int, backoff: float) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"{job}_{name if job != 'download' else f'part{part}'}.log"
    marker = progress_marker(job, part, name)

    print(f"supervising '{job}'  log -> {log_path}")
    attempt = 0
    while attempt < max_attempts:
        before = measure(progress_marker(job, part, name))
        cmd = build_command(job, part, name, epochs, batch, attempt)

        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n===== {stamp()} attempt {attempt + 1}/{max_attempts} "
                      f"=====\n{' '.join(cmd)}\n")
            log.flush()
            print(f"[{stamp()}] attempt {attempt + 1}: {' '.join(cmd[-6:])}")
            proc = subprocess.run(cmd, cwd=str(SYSTEM), stdout=log,
                                  stderr=subprocess.STDOUT)

        after = measure(progress_marker(job, part, name))
        if proc.returncode == 0:
            print(f"[{stamp()}] '{job}' completed successfully")
            return 0

        # Only spend an attempt when nothing moved. A lossy network that still
        # transfers bytes, or training that still writes epochs, keeps going.
        if after != before and after[0] > 0:
            print(f"[{stamp()}] exit {proc.returncode} but progress was made "
                  f"({after[0] - before[0]:+d} bytes) -- retrying without "
                  f"spending an attempt")
            attempt = 0
        else:
            attempt += 1
            print(f"[{stamp()}] exit {proc.returncode}, no progress "
                  f"(attempt {attempt}/{max_attempts})")

        wait = min(backoff * max(attempt, 1), 300.0)
        print(f"           retrying in {wait:.0f}s")
        time.sleep(wait)

    print(f"[{stamp()}] giving up on '{job}' after {max_attempts} attempts "
          f"with no progress. Inspect {log_path}")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job", choices=["download", "screen", "segment"])
    ap.add_argument("--part", type=int, default=3)
    ap.add_argument("--name", default="screen")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--max-attempts", type=int, default=50)
    ap.add_argument("--backoff", type=float, default=20.0)
    args = ap.parse_args(argv)
    return supervise(args.job, args.part, args.name, args.epochs, args.batch,
                     args.max_attempts, args.backoff)


if __name__ == "__main__":
    sys.exit(main())
