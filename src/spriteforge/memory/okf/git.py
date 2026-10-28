import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from spriteforge.core.security import redact


def git(root: Path, *args: str, index: Path | None = None) -> str:
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
        command,
        env={**os.environ, **({"GIT_INDEX_FILE": str(index)} if index else {})},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
    real_index = root / git(root, "rev-parse", "--git-path", "index")
    with tempfile.TemporaryDirectory(dir=real_index.parent) as directory:
        index = Path(directory) / "index"
        if real_index.exists():
            shutil.copyfile(real_index, index)
        git(root, "add", "--", *paths, index=index)
        if git(root, "diff", "--cached", "--name-only", index=index):
            git(root, "commit", "-m", message, "--only", "--", *paths, index=index)
        os.replace(index, real_index)


@contextmanager
def rollback_git(root: Path):
    """Restore the exact index and ref if any part of a write batch fails."""
    index = root / git(root, "rev-parse", "--git-path", "index")
    previous_index = index.read_bytes() if index.exists() else None
    try:
        head = git(root, "rev-parse", "--verify", "HEAD")
    except RuntimeError:
        head = None
    try:
        yield
    except BaseException:
        if head is None:
            git(root, "update-ref", "-d", "HEAD")
        else:
            git(root, "update-ref", "HEAD", head)
        if previous_index is None:
            index.unlink(missing_ok=True)
        else:
            index.write_bytes(previous_index)
        raise
