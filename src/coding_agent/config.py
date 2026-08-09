from __future__ import annotations

import dataclasses
import os
import pathlib
import tomllib
from typing import Any


@dataclasses.dataclass(frozen=True)
class GitHubConfig:
    repository: str
    ready_label: str = "agent:ready"
    working_label: str = "agent:working"
    pr_label: str = "agent:pr-open"
    failed_label: str = "agent:failed"
    poll_seconds: int = 60
    max_issues_per_poll: int = 1
    gh_executable: str = "gh"


@dataclasses.dataclass(frozen=True)
class RepositoryConfig:
    path: pathlib.Path
    base_branch: str = "main"
    branch_prefix: str = "codex/issue-"
    worktree_root: pathlib.Path = pathlib.Path("/tmp/coding-agent-worktrees")
    production_path: pathlib.Path | None = None
    production_checkout_path: pathlib.Path | None = None
    git_user_name: str = "Coding Agent"
    git_user_email: str = "coding-agent@localhost"


@dataclasses.dataclass(frozen=True)
class CodexConfig:
    executable: str = "codex"
    model: str = ""
    timeout_seconds: int = 3600
    sandbox: str = "workspace-write"
    approval_policy: str = "never"


@dataclasses.dataclass(frozen=True)
class AutomationConfig:
    agent_name: str = "CodingAgent"
    agent_icon: str = "🤖"
    create_draft_pr: bool = False
    enable_auto_merge: bool = False
    merge_method: str = "squash"
    delete_branch_after_merge: bool = True
    allowed_issue_authors: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()
    max_issue_images: int = 4
    max_issue_image_bytes: int = 15_000_000


@dataclasses.dataclass(frozen=True)
class ValidationCommand:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: int = 1200
    expand_globs: bool = False


@dataclasses.dataclass(frozen=True)
class RepositoryTarget:
    github: GitHubConfig
    repository: RepositoryConfig
    validation: tuple[ValidationCommand, ...]


@dataclasses.dataclass(frozen=True)
class SchedulerConfig:
    poll_seconds: int = 60
    max_parallel_repositories: int = 2


@dataclasses.dataclass(frozen=True)
class Settings:
    repositories: tuple[RepositoryTarget, ...]
    codex: CodexConfig
    automation: AutomationConfig
    scheduler: SchedulerConfig
    config_path: pathlib.Path

    # Keep the original single-repository API available for integrations and tests.
    @property
    def github(self) -> GitHubConfig:
        return self.repositories[0].github

    @property
    def repository(self) -> RepositoryConfig:
        return self.repositories[0].repository

    @property
    def validation(self) -> tuple[ValidationCommand, ...]:
        return self.repositories[0].validation


def _required(section: dict[str, Any], key: str, section_name: str) -> Any:
    value = section.get(key)
    if value in (None, ""):
        raise ValueError(f"Missing required config: [{section_name}].{key}")
    return value


def _validation_commands(raw: list[dict[str, Any]]) -> tuple[ValidationCommand, ...]:
    return tuple(
        ValidationCommand(
            name=str(_required(item, "name", "validation.commands")),
            argv=tuple(str(arg) for arg in _required(item, "argv", "validation.commands")),
            timeout_seconds=max(10, int(item.get("timeout_seconds", 1200))),
            expand_globs=bool(item.get("expand_globs", False)),
        )
        for item in raw
    )


def _github_config(raw: dict[str, Any], defaults: dict[str, Any]) -> GitHubConfig:
    merged = {**defaults, **raw}
    return GitHubConfig(
        repository=str(_required(merged, "repository", "repositories")),
        ready_label=str(merged.get("ready_label", "agent:ready")),
        working_label=str(merged.get("working_label", "agent:working")),
        pr_label=str(merged.get("pr_label", "agent:pr-open")),
        failed_label=str(merged.get("failed_label", "agent:failed")),
        poll_seconds=max(10, int(merged.get("poll_seconds", 60))),
        max_issues_per_poll=max(1, int(merged.get("max_issues_per_poll", 1))),
        gh_executable=str(merged.get("gh_executable", "gh")),
    )


def _repository_config(raw: dict[str, Any]) -> RepositoryConfig:
    production_path = str(raw.get("production_path", "")).strip()
    production_checkout_path = str(raw.get("production_checkout_path", "")).strip()
    return RepositoryConfig(
        path=pathlib.Path(_required(raw, "path", "repositories")).expanduser().resolve(),
        base_branch=str(raw.get("base_branch", "main")),
        branch_prefix=str(raw.get("branch_prefix", "codex/issue-")),
        worktree_root=pathlib.Path(raw.get("worktree_root", "/tmp/coding-agent-worktrees")).expanduser().resolve(),
        production_path=pathlib.Path(production_path).expanduser().resolve() if production_path else None,
        production_checkout_path=(
            pathlib.Path(production_checkout_path).expanduser().resolve()
            if production_checkout_path else None
        ),
        git_user_name=str(raw.get("git_user_name", "Coding Agent")),
        git_user_email=str(raw.get("git_user_email", "coding-agent@localhost")),
    )


def load_settings(path: str | os.PathLike[str]) -> Settings:
    config_path = pathlib.Path(path).expanduser().resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    github_raw = raw.get("github", {})
    codex_raw = raw.get("codex", {})
    auto_raw = raw.get("automation", {})
    scheduler_raw = raw.get("scheduler", {})
    codex = CodexConfig(
        executable=str(codex_raw.get("executable", "codex")),
        model=str(codex_raw.get("model", "")),
        timeout_seconds=max(60, int(codex_raw.get("timeout_seconds", 3600))),
        sandbox=str(codex_raw.get("sandbox", "workspace-write")),
        approval_policy=str(codex_raw.get("approval_policy", "never")),
    )
    automation = AutomationConfig(
        agent_name=str(auto_raw.get("agent_name", "CodingAgent")).strip() or "CodingAgent",
        agent_icon=str(auto_raw.get("agent_icon", "🤖")).strip(),
        create_draft_pr=bool(auto_raw.get("create_draft_pr", False)),
        enable_auto_merge=bool(auto_raw.get("enable_auto_merge", False)),
        merge_method=str(auto_raw.get("merge_method", "squash")),
        delete_branch_after_merge=bool(auto_raw.get("delete_branch_after_merge", True)),
        allowed_issue_authors=tuple(str(item) for item in auto_raw.get("allowed_issue_authors", [])),
        protected_paths=tuple(str(item) for item in auto_raw.get("protected_paths", [])),
        max_issue_images=max(0, int(auto_raw.get("max_issue_images", 4))),
        max_issue_image_bytes=max(1, int(auto_raw.get("max_issue_image_bytes", 15_000_000))),
    )
    targets: list[RepositoryTarget] = []
    repositories_raw = raw.get("repositories", [])
    if repositories_raw:
        if not isinstance(repositories_raw, list):
            raise ValueError("[[repositories]] must be an array of tables")
        for item in repositories_raw:
            item = dict(item)
            github_overrides = dict(item.pop("github", {}))
            github_overrides["repository"] = item.pop("repository", github_overrides.get("repository", ""))
            validation_raw = item.pop("validation", [])
            targets.append(RepositoryTarget(
                github=_github_config(github_overrides, github_raw),
                repository=_repository_config(item),
                validation=_validation_commands(validation_raw),
            ))
    else:
        repo_raw = raw.get("repository", {})
        validation_raw = raw.get("validation", {}).get("commands", [])
        targets.append(RepositoryTarget(
            github=_github_config(github_raw, {}),
            repository=_repository_config(repo_raw),
            validation=_validation_commands(validation_raw),
        ))

    names = [target.github.repository for target in targets]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate GitHub repository in [[repositories]]")
    paths = [target.repository.path for target in targets]
    if len(paths) != len(set(paths)):
        raise ValueError("Each [[repositories]] entry must use a distinct local path")
    worktree_roots = [target.repository.worktree_root for target in targets]
    if len(worktree_roots) != len(set(worktree_roots)):
        raise ValueError("Each [[repositories]] entry must use a distinct worktree_root")
    for target in targets:
        repo = target.repository
        if repo.production_checkout_path and not repo.production_path:
            raise ValueError("production_checkout_path requires production_path")
        if (
            repo.production_checkout_path
            and repo.production_path
            and repo.production_checkout_path.is_relative_to(repo.production_path)
        ):
            raise ValueError("production_checkout_path must be outside production_path")

    scheduler = SchedulerConfig(
        poll_seconds=max(10, int(scheduler_raw.get("poll_seconds", github_raw.get("poll_seconds", 60)))),
        max_parallel_repositories=max(1, int(scheduler_raw.get("max_parallel_repositories", 2))),
    )
    return Settings(tuple(targets), codex, automation, scheduler, config_path)
