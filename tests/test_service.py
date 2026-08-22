import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from coding_agent.commands import expand_argv_globs
from coding_agent.config import load_settings
from coding_agent.github import (
    GitHub, Issue, PullRequest, PullRequestStatus, WorkflowRun,
)
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


def test_production_commands_run_after_sync_in_order(monkeypatch, tmp_path):
    production_path = tmp_path / "production"
    production_path.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(f'''
[[repositories]]
repository = "org/repo"
path = "/srv/repo"
production_path = "{production_path}"

[[repositories.production_commands]]
name = "build"
argv = ["npm", "run", "build"]

[[repositories.production_commands]]
name = "restart"
argv = ["systemctl", "restart", "example.service"]
''')
    worker = CodingAgentService(load_settings(config)).workers[0]
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), kwargs["cwd"]))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("coding_agent.service.run", fake_run)

    assert worker._run_production_commands(production_path) == ("build", "restart")
    assert calls == [
        (("npm", "run", "build"), production_path),
        (("systemctl", "restart", "example.service"), production_path),
    ]


def test_manual_update_uses_checkout_and_copies_tracked_files(monkeypatch, tmp_path):
    production_path = tmp_path / "production"
    checkout_path = tmp_path / "production-checkout"
    production_path.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(f'''
[[repositories]]
repository = "org/repo"
path = "/srv/repo"
base_branch = "main"
production_path = "{production_path}"
production_checkout_path = "{checkout_path}"
''')
    worker = CodingAgentService(load_settings(config)).workers[0]
    calls = []

    monkeypatch.setattr(
        worker, "_ensure_production_checkout",
        lambda path: calls.append(("ensure", path)),
    )

    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), kwargs["cwd"]))
        return SimpleNamespace(stdout="old123\n")

    monkeypatch.setattr("coding_agent.service.run", fake_run)
    monkeypatch.setattr(
        worker, "_update_production_checkout",
        lambda path: calls.append(("update", path)) or "new123",
    )
    monkeypatch.setattr(
        worker, "_copy_tracked_files",
        lambda checkout, production, old, new: calls.append(
            ("copy", checkout, production, old, new)
        ),
    )

    assert worker.update_production() == "new123"
    assert calls == [
        ("ensure", checkout_path),
        (("git", "rev-parse", "HEAD"), checkout_path),
        (("git", "checkout", "main"), checkout_path),
        ("update", checkout_path),
        ("copy", checkout_path, production_path, "old123", "new123"),
    ]


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


def test_automatic_poll_skips_issue_claimed_after_listing(monkeypatch, caplog):
    worker = CodingAgentService(settings()).workers[0]
    listed_issue = Issue(
        number=17,
        title="Claim race",
        body="",
        url="https://github.com/unsungb/base-all/issues/17",
        author="alice",
        labels=(worker.target.github.ready_label,),
    )
    claimed_issue = Issue(
        number=17,
        title="Claim race",
        body="",
        url="https://github.com/unsungb/base-all/issues/17",
        author="alice",
        labels=(worker.target.github.ready_label, worker.target.github.working_label),
    )
    monkeypatch.setattr(worker, "sync_production_updates", lambda: 0)
    monkeypatch.setattr(worker.github, "list_ready", lambda: [listed_issue])
    monkeypatch.setattr(worker.github, "get_issue", lambda _number: claimed_issue)

    with caplog.at_level("INFO", logger="coding_agent.service"):
        assert worker.run_once() == 0

    assert "issue #17 is already claimed; skipping" in caplog.text


def test_specific_claimed_issue_still_reports_error(monkeypatch):
    worker = CodingAgentService(settings()).workers[0]
    claimed_issue = Issue(
        number=17,
        title="Claimed issue",
        body="",
        url="https://github.com/unsungb/base-all/issues/17",
        author="alice",
        labels=(worker.target.github.working_label,),
    )
    monkeypatch.setattr(worker, "sync_production_updates", lambda: 0)
    monkeypatch.setattr(worker.github, "get_issue", lambda _number: claimed_issue)

    with pytest.raises(AgentError, match="Issue #17 is already claimed"):
        worker.run_once(17)


def test_prepare_worktree_removes_stale_local_branch(monkeypatch, tmp_path):
    repository_path = tmp_path / "repository"
    worktree_root = tmp_path / "worktrees"
    repository_path.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(f'''
[[repositories]]
repository = "org/repo"
path = "{repository_path}"
worktree_root = "{worktree_root}"
''')
    worker = CodingAgentService(load_settings(config)).workers[0]
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(tuple(argv))
        stdout = "* codex/issue-30-fix\n" if tuple(argv) == (
            "git", "branch", "--list", "codex/issue-30-fix",
        ) else ""
        return SimpleNamespace(stdout=stdout, returncode=0)

    monkeypatch.setattr("coding_agent.service.run", fake_run)
    worker._prepare_worktree("codex/issue-30-fix", worktree_root / "issue-30")

    prune_index = calls.index(("git", "worktree", "prune"))
    delete_index = calls.index(("git", "branch", "-D", "codex/issue-30-fix"))
    add_index = calls.index((
        "git", "worktree", "add", "-b", "codex/issue-30-fix",
        str(worktree_root / "issue-30"), "origin/main",
    ))
    assert prune_index < delete_index < add_index


def test_doctor_reports_blocked_codex_user_namespace(monkeypatch):
    service = CodingAgentService(settings())
    real_exists = Path.exists
    real_read_text = Path.read_text

    def fake_exists(path):
        if str(path) == "/proc/sys/kernel/apparmor_restrict_unprivileged_userns":
            return True
        return real_exists(path)

    def fake_read_text(path, *args, **kwargs):
        if str(path) == "/proc/sys/kernel/apparmor_restrict_unprivileged_userns":
            return "1\n"
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "read_text", fake_read_text)
    monkeypatch.setattr(service.workers[0], "doctor", lambda: [])

    assert any("ExecStartPre" in failure for failure in service.doctor())


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
    assert any(call[-3:] == ("fetch", "origin", "main") for call in calls)
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


def test_structured_codex_is_read_only_and_loads_final_result(monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    config.write_text(f'''
[[repositories]]
repository = "org/repo"
path = "{repository_path}"
''')
    worker = CodingAgentService(load_settings(config)).workers[0]
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = tuple(argv)
        captured["prompt"] = kwargs["input_text"]
        result_path = Path(argv[argv.index("--output-last-message") + 1])
        result_path.write_text('{"summary":"ok"}')
        return SimpleNamespace(stdout='{"type":"turn.completed"}\n')

    monkeypatch.setattr("coding_agent.service.run", fake_run)
    assert worker._run_structured_codex(
        "inspect safely", {"type": "object"}, task_name="test",
    ) == {"summary": "ok"}
    assert captured["argv"][captured["argv"].index("--sandbox") + 1] == "read-only"
    assert captured["argv"][captured["argv"].index("--ask-for-approval") + 1] == "never"
    assert "--output-schema" in captured["argv"]
    assert captured["prompt"] == "inspect safely"


def test_review_pr_generates_report_and_only_publishes_when_requested(monkeypatch):
    worker = CodingAgentService(settings()).workers[0]
    pull = PullRequest(
        number=42, title="Fix race", body="details", url="https://example.test/pr/42",
        author="alice", base_ref_name="main", head_ref_name="fix-race",
        head_ref_oid="abc123",
    )
    published = []
    captured = {}
    monkeypatch.setattr(worker.github, "get_pr", lambda _number: pull)
    monkeypatch.setattr(worker.github, "pr_diff", lambda _number: "diff --git a/a.py b/a.py")

    def fake_codex(prompt, schema, **kwargs):
        captured.update(prompt=prompt, schema=schema, kwargs=kwargs)
        return {
            "verdict": "request_changes",
            "summary": "One regression found.",
            "findings": [{
                "severity": "high", "title": "Race remains", "file": "a.py",
                "line": 12, "detail": "The write is not synchronized.",
                "suggestion": "Protect it with the existing lock.",
            }],
        }

    monkeypatch.setattr(worker, "_run_structured_codex", fake_codex)
    monkeypatch.setattr(
        worker.github, "comment_pr", lambda number, body: published.append((number, body)),
    )

    report = worker.review_pr(42)
    assert "request_changes" in report
    assert "`a.py:12`" in report
    assert published == []
    assert "Work read-only" in captured["prompt"]

    worker.review_pr(42, publish=True)
    assert published[0][0] == 42
    assert "One regression found." in published[0][1]
    assert json.loads(worker.review_pr(42, json_output=True))["verdict"] == "request_changes"


def test_diagnose_ci_uses_latest_failed_pr_run_and_can_publish(monkeypatch):
    worker = CodingAgentService(settings()).workers[0]
    pull = PullRequest(
        number=7, title="Change API", body="", url="https://example.test/pr/7",
        author="alice", base_ref_name="main", head_ref_name="api-change",
        head_ref_oid="def456",
    )
    workflow = WorkflowRun(
        database_id=99, name="CI", display_title="Change API", conclusion="failure",
        status="completed", url="https://example.test/runs/99", head_sha="def456",
    )
    published = []
    captured = {}
    monkeypatch.setattr(worker.github, "get_pr", lambda _number: pull)
    monkeypatch.setattr(worker.github, "latest_failed_run", lambda sha: workflow)
    monkeypatch.setattr(worker.github, "failed_run_logs", lambda _run: "FAILED test_api")

    def fake_codex(prompt, _schema, **_kwargs):
        captured["prompt"] = prompt
        return {
            "classification": "test_failure", "summary": "Assertion failed.",
            "root_causes": ["Expected status changed"],
            "evidence": ["FAILED test_api"],
            "recommended_actions": ["Update the implementation, then rerun test_api"],
            "suspected_files": ["api/routes.py"],
        }

    monkeypatch.setattr(worker, "_run_structured_codex", fake_codex)
    monkeypatch.setattr(
        worker.github, "comment_pr", lambda number, body: published.append((number, body)),
    )

    report = worker.diagnose_ci(pr_number=7, publish=True)
    assert "test_failure" in report
    assert "FAILED test_api" in report
    assert "def456" in captured["prompt"]
    assert published == [(7, report)]


def test_diagnose_ci_publish_requires_pr(monkeypatch):
    worker = CodingAgentService(settings()).workers[0]
    workflow = WorkflowRun(
        database_id=99, name="CI", display_title="Build", conclusion="failure",
        status="completed", url="https://example.test/runs/99", head_sha="abc",
    )
    monkeypatch.setattr(worker.github, "get_workflow_run", lambda _run: workflow)
    monkeypatch.setattr(worker.github, "failed_run_logs", lambda _run: "failed")
    monkeypatch.setattr(worker, "_run_structured_codex", lambda *_args, **_kwargs: {
        "classification": "unknown", "summary": "Unknown.", "root_causes": [],
        "evidence": [], "recommended_actions": [], "suspected_files": [],
    })

    with pytest.raises(AgentError, match="--publish requires --pr"):
        worker.diagnose_ci(99, publish=True)


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
