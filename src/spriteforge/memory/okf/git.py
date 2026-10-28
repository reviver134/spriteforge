import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

from spriteforge.core.security import redact


def git(root: Path, *args: str) -> str:
    command = [
        "git",
        "-c",
        f"safe.directory={root.as_posix()}",
        "-c",
        "user.name=SpriteForge",
        "-c",
        "user.email=spriteforge@localhost",
        "-c",
        "core.hooksPath=/dev/null",
        "-C",
        str(root),
        *args,
    ]
    result = subprocess.run(
        command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode:
        raise RuntimeError(redact(result.stderr.strip()))
    return result.stdout.strip()


def init(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        git(root, "init", ".")


@contextmanager
def write_lock(path: Path):
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def commit(root: Path, paths: list[str], message: str) -> None:
    git(root, "add", "--", *paths)
    if git(root, "diff", "--cached", "--name-only"):
        git(root, "commit", "-m", message, "--only", "--", *paths)
