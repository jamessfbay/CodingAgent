from __future__ import annotations

import glob
import logging
import os
import pathlib
import shutil
import subprocess
from collections.abc import Sequence

LOG = logging.getLogger(__name__)


class CommandError(RuntimeError):
    def __init__(self, argv: Sequence[str], returncode: int, output: str):
        self.argv = tuple(argv)
        self.returncode = returncode
        self.output = output
        super().__init__(f"Command failed ({returncode}): {shlex_join(argv)}\n{tail(output)}")


def shlex_join(argv: Sequence[str]) -> str:
    import shlex

    return shlex.join(str(arg) for arg in argv)


def tail(value: str, limit: int = 4000) -> str:
    return value if len(value) <= limit else value[-limit:]


def resolve_executable(value: str) -> str | None:
    if os.sep in value:
        path = pathlib.Path(value).expanduser()
        return str(path.resolve()) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(value)


def expand_argv_globs(argv: Sequence[str], cwd: pathlib.Path) -> list[str]:
    expanded: list[str] = []
    for arg in argv:
        if any(char in arg for char in "*?["):
            matches = sorted(glob.glob(str(cwd / arg)))
            expanded.extend(os.path.relpath(item, cwd) for item in matches)
        else:
            expanded.append(arg)
    return expanded


def run(
    argv: Sequence[str],
    *,
    cwd: pathlib.Path,
    timeout: int = 300,
    input_text: str | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    LOG.debug("run: %s (cwd=%s)", shlex_join(argv), cwd)
    result = subprocess.run(
        list(argv), cwd=cwd, input=input_text, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout, env=env or os.environ.copy(), check=False,
    )
    if check and result.returncode:
        raise CommandError(argv, result.returncode, result.stdout)
    return result
