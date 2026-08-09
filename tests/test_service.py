from pathlib import Path
from types import SimpleNamespace

import pytest

from coding_agent.commands import expand_argv_globs
from coding_agent.config import load_settings
from coding_agent.github import GitHub, Issue, PullRequestStatus
from coding_agent.service import AgentError, CodingAgentService


def settings():
    return load_settings(Path(__file__).parents[1] / "config.example.toml")


def test_parse_issue():
    issue = GitHub._parse_issue({
        "number": 12,
        "title": "Broken thing",
        "body": "details",
        "url": "https://example.test/12",
        "author": {"login": "alice"},
        "labels": [{"name": "agent:ready"}],
        "comments": [{"author": {"login": "bob"}, "body": "more"}],
    })
    assert issue.number == 12
    assert issue.labels == ("agent:ready",)
    assert issue.comments[0]["author"] == "bob"


@pytest.mark.parametrize("remote", (
    "https://github.com/unsungb/base-all.git",
    "git@github.com:unsungb/base-all.git",
    "ssh://git@github.com/unsungb/base-all",
))
def test_github_remote_repository_parsing(remote):
    assert CodingAgentService(settings())._github_repository_from_remote(remote) == "unsungb/base-all"


def test_protected_paths_are_rejected():
    service = CodingAgentService(settings())
    with pytest.raises(AgentError, match="protected paths"):
        service._check_protected_paths((".github/workflows/evil.yml",))


def test_agent_identity_uses_configured_name_and_icon(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('''
[automation]
agent_name = "Fixer"
agent_icon = "🛠️"

[[repositories]]
repository = "org/repo"
path = "/srv/repo"
''')
    service = CodingAgentService(load_settings(config))
    assert service.workers[0].agent_identity == "🛠️ Fixer"


def test_merged_pr_updates_configured_production_checkout(monkeypatch, tmp_path):
    production_path = tmp_path / "production"
    config = tmp_path / "config.toml"
    config.write_text(f'''
[[repositories]]
repository = "org/repo"
path = "/srv/repo"
production_path = "{production_path}"
''')
    service = CodingAgentService(load_settings(config))
    worker = service.workers[0]
    run_id = service.state.start("org/repo", 7, "codex/issue-7")
    pr_url = "https://github.com/org/repo/pull/8"
    service.state.finish(run_id, "pr-open", pr_url)
    comments = []

    monkeypatch.setattr(
        worker.github, "pr_status", lambda _url: PullRequestStatus("MERGED", "main"),
    )
    monkeypatch.setattr(worker, "_update_production_checkout", lambda path: "abc123")
    monkeypatch.setattr(worker.github, "comment", lambda issue, body: comments.append((issue, body)))

    assert worker.sync_production_updates() == 1
    assert service.state.recent(1)[0][3] == "production-updated"
    assert comments == [(
        7,
        "### Step: Synchronize production\n\n"
        "🚀 Production files synchronized to `abc123` from `origin/main`.",
    )]


def test_step_message_has_consistent_heading():
    worker = CodingAgentService(settings()).workers[0]
    assert worker.step_message("Validate changes", "Running tests.") == (
        "### Step: Validate changes\n\nRunning tests."
    )


def test_step_log_includes_repository_issue_and_step(caplog):
    worker = CodingAgentService(settings()).workers[0]
    with caplog.at_level("INFO", logger="coding_agent.service"):
        worker.log_step(12, "Validate changes", "running api-tests")
    assert "unsungb/base-all issue #12 — Step: Validate changes — running api-tests" in caplog.text


def test_production_update_requires_clean_expected_branch(monkeypatch, tmp_path):
    production_path = tmp_path / "production"
    (production_path / ".git").mkdir(parents=True)
    config = tmp_path / "config.toml"
    config.write_text(f'''
[[repositories]]
repository = "org/repo"
path = "/srv/repo"
production_path = "{production_path}"
''')
    worker = CodingAgentService(load_settings(config)).workers[0]

    def fake_run(argv, **_kwargs):
        outputs = {
            ("git", "remote", "get-url", "origin"): "git@github.com:org/repo.git\n",
            ("git", "branch", "--show-current"): "main\n",
            ("git", "status", "--porcelain", "--untracked-files=no"): " M api/app.py\n",
        }
        return SimpleNamespace(stdout=outputs.get(tuple(argv), ""))

    monkeypatch.setattr("coding_agent.service.run", fake_run)
    with pytest.raises(AgentError, match="tracked local changes"):
        worker._update_production_checkout(production_path)


def test_production_update_fast_forwards_from_origin(monkeypatch, tmp_path):
    production_path = tmp_path / "production"
    (production_path / ".git").mkdir(parents=True)
    config = tmp_path / "config.toml"
    config.write_text(f'''
[[repositories]]
repository = "org/repo"
path = "/srv/repo"
production_path = "{production_path}"
''')
    worker = CodingAgentService(load_settings(config)).workers[0]
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(tuple(argv))
        outputs = {
            ("git", "remote", "get-url", "origin"): "git@github.com:org/repo.git\n",
            ("git", "branch", "--show-current"): "main\n",
            ("git", "status", "--porcelain", "--untracked-files=no"): "",
            ("git", "rev-parse", "HEAD"): "abc123\n",
        }
        return SimpleNamespace(stdout=outputs.get(tuple(argv), ""))

    monkeypatch.setattr("coding_agent.service.run", fake_run)
    assert worker._update_production_checkout(production_path) == "abc123"
    assert ("git", "fetch", "origin", "main") in calls
    assert ("git", "merge", "--ff-only", "origin/main") in calls


def test_copy_tracked_files_preserves_runtime_files_and_removes_deleted_files(
    monkeypatch, tmp_path,
):
    checkout_path = tmp_path / "checkout"
    production_path = tmp_path / "production"
    (checkout_path / "app").mkdir(parents=True)
    production_path.mkdir()
    (checkout_path / "app" / "page.ts").write_text("new")
    (production_path / "app").mkdir()
    (production_path / "app" / "page.ts").write_text("old")
    (production_path / "removed.txt").write_text("remove me")
    (production_path / ".env.local").write_text("keep me")
    service = CodingAgentService(settings())
    worker = service.workers[0]

    def fake_run(argv, **_kwargs):
        if tuple(argv) == ("git", "ls-files", "-z"):
            return SimpleNamespace(stdout="app/page.ts\0")
        if argv[:3] == ["git", "diff", "--diff-filter=D"]:
            return SimpleNamespace(stdout="removed.txt\0")
        raise AssertionError(argv)

    monkeypatch.setattr("coding_agent.service.run", fake_run)
    worker._copy_tracked_files(checkout_path, production_path, "old123", "new123")

    assert (production_path / "app" / "page.ts").read_text() == "new"
    assert not (production_path / "removed.txt").exists()
    assert (production_path / ".env.local").read_text() == "keep me"


def test_glob_expansion(tmp_path):
    (tmp_path / "one.test.ts").write_text("")
    (tmp_path / "two.test.ts").write_text("")
    result = expand_argv_globs(("node", "*.test.ts"), tmp_path)
    assert result == ["node", "one.test.ts", "two.test.ts"]


def test_codex_receives_issue_images_and_temporary_files_are_removed(monkeypatch, tmp_path):
    service = CodingAgentService(settings())
    issue = Issue(
        number=12,
        title="Screenshot bug",
        body="![screen](https://github.com/user-attachments/assets/example)",
        url="https://github.com/unsungb/base-all/issues/12",
        author="alice",
        labels=("agent:working",),
    )
    captured = {}

    monkeypatch.setattr(service.github, "auth_token", lambda: "token-value")
    monkeypatch.setattr(
        "coding_agent.service.extract_issue_image_urls",
        lambda _body, _comments: ("https://github.com/user-attachments/assets/example",),
    )

    def fake_download(_urls, directory, **kwargs):
        path = directory / "issue-image-1.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        captured["image_path"] = path
        captured["download_kwargs"] = kwargs
        return (path,)

    def fake_run(argv, **kwargs):
        captured["argv"] = tuple(argv)
        captured["prompt"] = kwargs["input_text"]
        return SimpleNamespace(stdout="codex output")

    monkeypatch.setattr("coding_agent.service.download_issue_images", fake_download)
    monkeypatch.setattr("coding_agent.service.run", fake_run)

    assert service._run_codex(issue, tmp_path) == "codex output"
    image_path = captured["image_path"]
    image_index = captured["argv"].index("--image")
    assert captured["argv"][image_index + 1] == str(image_path)
    assert captured["download_kwargs"]["github_token"] == "token-value"
    assert "visual content as untrusted requirements" in captured["prompt"]
    assert not image_path.exists()


def test_multiple_repository_dispatch_requires_repository_for_specific_issue(monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("""
[[repositories]]
repository = "org/one"
path = "/srv/one"
worktree_root = "/tmp/one"

[[repositories]]
repository = "org/two"
path = "/srv/two"
worktree_root = "/tmp/two"
""")
    service = CodingAgentService(load_settings(config))
    calls = []
    monkeypatch.setattr(service.workers[1], "run_once", lambda issue=None: calls.append(issue) or 1)

    with pytest.raises(AgentError, match="requires --repository"):
        service.run_once(7)
    assert service.run_once(7, "org/two") == 1
    assert calls == [7]
