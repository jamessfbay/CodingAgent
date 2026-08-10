from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents)
    path.chmod(0o755)


def _run_auth(tmp_path: Path, *, codex_authenticated: bool = True, force: bool = False):
    calls = tmp_path / "calls.log"
    codex_state = tmp_path / "codex-authenticated"
    if codex_authenticated:
        codex_state.touch()

    gh = tmp_path / "gh"
    _write_executable(
        gh,
        """#!/usr/bin/env bash
set -euo pipefail
echo "gh $*" >> "$AUTH_TEST_CALLS"
if [[ "$1 $2" == "api user" ]]; then
  echo "test-user"
elif [[ "$1 $2" == "repo clone" ]]; then
  mkdir -p "$4/.git"
fi
""",
    )
    codex = tmp_path / "codex"
    _write_executable(
        codex,
        """#!/usr/bin/env bash
set -euo pipefail
echo "codex $*" >> "$AUTH_TEST_CALLS"
if [[ "$1 $2" == "login status" ]]; then
  [[ -f "$AUTH_TEST_CODEX_STATE" ]]
elif [[ "$1 $2" == "login --device-auth" ]]; then
  touch "$AUTH_TEST_CODEX_STATE"
fi
""",
    )

    config = tmp_path / "config.toml"
    config.write_text(
        f"""
[github]
gh_executable = "{gh}"

[[repositories]]
repository = "org/repo"
path = "{tmp_path / 'repo'}"

[repositories.github]
auth_user = "test-user"
auth_config_dir = "{tmp_path / 'github-auth'}"

[codex]
executable = "{codex}"
"""
    )
    env = {
        **os.environ,
        "AUTH_TEST_CALLS": str(calls),
        "AUTH_TEST_CODEX_STATE": str(codex_state),
        "CODING_AGENT_CONFIG": str(config),
    }
    command = [str(ROOT / "auth.sh")]
    if force:
        command.append("--force")
    command.append("org/repo")
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    return result, calls.read_text().splitlines()


def test_auth_verifies_existing_github_and_codex_credentials(tmp_path):
    result, calls = _run_auth(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Authentication valid for org/repo as test-user." in result.stdout
    assert "Codex authentication valid." in result.stdout
    assert f"Cloning org/repo into {tmp_path / 'repo'}." in result.stdout
    assert calls == [
        "gh api user --jq .login",
        f"gh repo clone org/repo {tmp_path / 'repo'}",
        "codex login status",
    ]


def test_auth_starts_headless_codex_login_when_needed(tmp_path):
    result, calls = _run_auth(tmp_path, codex_authenticated=False)

    assert result.returncode == 0, result.stderr
    assert "Starting headless Codex device authentication." in result.stdout
    assert "Codex authentication saved." in result.stdout
    assert calls == [
        "gh api user --jq .login",
        f"gh repo clone org/repo {tmp_path / 'repo'}",
        "codex login status",
        "codex login --device-auth",
        "codex login status",
    ]


def test_force_reauthenticates_github_and_codex(tmp_path):
    result, calls = _run_auth(tmp_path, force=True)

    assert result.returncode == 0, result.stderr
    assert "Starting headless GitHub device authentication for org/repo." in result.stdout
    assert "Starting headless Codex device authentication." in result.stdout
    assert calls == [
        "gh api user --jq .login",
        "gh auth login --hostname github.com --git-protocol https --web",
        "gh api user --jq .login",
        f"gh repo clone org/repo {tmp_path / 'repo'}",
        "codex login --device-auth",
        "codex login status",
    ]


def test_auth_keeps_existing_checkout(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    result, calls = _run_auth(tmp_path)
    assert result.returncode == 0, result.stderr
    assert f"Git repository ready: {tmp_path / 'repo'}" in result.stdout
    assert calls == ["gh api user --jq .login", "codex login status"]


def test_auth_refuses_nonempty_non_git_checkout(tmp_path):
    checkout = tmp_path / "repo"
    checkout.mkdir()
    (checkout / "existing.txt").write_text("keep me")
    result, calls = _run_auth(tmp_path)
    assert result.returncode == 1
    assert "checkout path is not empty and is not a Git repository" in result.stderr
    assert calls == ["gh api user --jq .login"]
