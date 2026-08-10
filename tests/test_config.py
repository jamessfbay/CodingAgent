from pathlib import Path

import pytest

from coding_agent.config import load_settings


def test_load_example_config():
    path = Path(__file__).parents[1] / "config.example.toml"
    settings = load_settings(path)
    assert settings.github.repository == "unsungb/base-all"
    assert settings.github.auth_user == "unsungb"
    assert settings.github.auth_config_dir == Path(__file__).parents[1] / "state/github-auth/base-all"
    assert settings.repository.base_branch == "main"
    assert settings.automation.agent_name == "CodingAgent"
    assert settings.automation.agent_icon == "🤖"
    assert settings.automation.max_issue_images == 4
    assert settings.automation.max_issue_image_bytes == 15_000_000
    assert settings.validation[0].argv == (
        "/usr/bin/env",
        "-C",
        "api",
        "/var/www/workspace/CodingAgent/.venv/bin/python",
        "-m",
        "pytest",
        "tests",
        "-q",
    )
    assert settings.scheduler.max_parallel_repositories == 2


def test_load_multiple_repositories(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("""
[github]
ready_label = "ready"

[scheduler]
poll_seconds = 15
max_parallel_repositories = 3

[[repositories]]
repository = "org/one"
path = "/srv/one"
worktree_root = "/tmp/one-worktrees"

[[repositories.validation]]
name = "one-tests"
argv = ["python3", "-m", "pytest"]

[[repositories]]
repository = "org/two"
path = "/srv/two"
worktree_root = "/tmp/two-worktrees"

[repositories.github]
ready_label = "two:ready"
max_issues_per_poll = 2
auth_user = "repo-two-bot"
auth_config_dir = "state/github-auth/repo-two"
""")
    settings = load_settings(config)
    assert [item.github.repository for item in settings.repositories] == ["org/one", "org/two"]
    assert settings.repositories[0].github.ready_label == "ready"
    assert settings.repositories[1].github.ready_label == "two:ready"
    assert settings.repositories[1].github.max_issues_per_poll == 2
    assert settings.repositories[1].github.auth_user == "repo-two-bot"
    assert settings.repositories[1].github.auth_config_dir == (
        tmp_path / "state/github-auth/repo-two"
    )
    assert settings.repositories[0].validation[0].name == "one-tests"
    assert settings.repositories[1].validation == ()
    assert settings.scheduler.poll_seconds == 15


def test_load_custom_agent_identity(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('''
[automation]
agent_name = "Fixer"
agent_icon = "🛠️"

[[repositories]]
repository = "org/repo"
path = "/srv/repo"
''')
    settings = load_settings(config)
    assert settings.automation.agent_name == "Fixer"
    assert settings.automation.agent_icon == "🛠️"


def test_load_production_path(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('''
[[repositories]]
repository = "org/repo"
path = "/srv/repo"
production_path = "/srv/production"
production_checkout_path = "/srv/production-checkout"

[[repositories.production_commands]]
name = "build"
argv = ["npm", "run", "build"]
timeout_seconds = 600
''')
    settings = load_settings(config)
    assert settings.repository.production_path == Path("/srv/production")
    assert settings.repository.production_checkout_path == Path("/srv/production-checkout")
    assert settings.repositories[0].production_commands[0].name == "build"
    assert settings.repositories[0].production_commands[0].argv == ("npm", "run", "build")


def test_repositories_require_unique_worktree_roots(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("""
[[repositories]]
repository = "org/one"
path = "/srv/one"
worktree_root = "/tmp/shared"

[[repositories]]
repository = "org/two"
path = "/srv/two"
worktree_root = "/tmp/shared"
""")
    with pytest.raises(ValueError, match="distinct worktree_root"):
        load_settings(config)


def test_legacy_single_repository_config_remains_supported(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("""
[github]
repository = "org/legacy"
poll_seconds = 25

[repository]
path = "/srv/legacy"
worktree_root = "/tmp/legacy-worktrees"

[[validation.commands]]
name = "tests"
argv = ["python3", "-m", "pytest"]
""")
    settings = load_settings(config)
    assert len(settings.repositories) == 1
    assert settings.github.repository == "org/legacy"
    assert settings.validation[0].name == "tests"
    assert settings.scheduler.poll_seconds == 25
