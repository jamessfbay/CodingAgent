from __future__ import annotations

import json
import os
import pathlib
import shlex
from dataclasses import dataclass
from typing import Any

from .commands import run
from .config import GitHubConfig


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    url: str
    author: str
    labels: tuple[str, ...]
    comments: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class PullRequestStatus:
    state: str
    base_ref_name: str


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    body: str
    url: str
    author: str
    base_ref_name: str
    head_ref_name: str
    head_ref_oid: str


@dataclass(frozen=True)
class WorkflowRun:
    database_id: int
    name: str
    display_title: str
    conclusion: str
    status: str
    url: str
    head_sha: str


class GitHub:
    def __init__(self, config: GitHubConfig, cwd: pathlib.Path):
        self.config = config
        self.cwd = cwd
        self._cached_token: str | None = None

    def _configured_token(self) -> str:
        if self._cached_token is not None:
            return self._cached_token
        if self.config.token_env:
            token = os.environ.get(self.config.token_env, "").strip()
            if not token:
                raise RuntimeError(
                    f"GitHub token environment variable {self.config.token_env!r} is unavailable"
                )
            self._cached_token = token
            return token
        if self.config.auth_user:
            result = run(
                [
                    self.config.gh_executable, "auth", "token", "--hostname", "github.com",
                    "--user", self.config.auth_user,
                ],
                cwd=self.cwd,
                timeout=60,
                env=self._base_env(),
            )
            self._cached_token = result.stdout.strip()
            return self._cached_token
        return ""

    def _base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.config.auth_config_dir:
            env["GH_CONFIG_DIR"] = str(self.config.auth_config_dir)
        env["GH_PROMPT_DISABLED"] = "1"
        return env

    def command_env(self) -> dict[str, str]:
        env = self._base_env()
        token = self._configured_token()
        if token:
            env["GH_TOKEN"] = token
        return env

    def git_argv(self, *args: str) -> list[str]:
        helper = f"!{shlex.quote(self.config.gh_executable)} auth git-credential"
        return [
            "git", "-c", "credential.helper=", "-c", f"credential.helper={helper}", *args,
        ]

    def _gh(self, *args: str, timeout: int = 120, check: bool = True) -> str:
        result = run(
            [self.config.gh_executable, *args, "--repo", self.config.repository],
            cwd=self.cwd, timeout=timeout, check=check, env=self.command_env(),
        )
        return result.stdout.strip()

    def auth_status(self) -> None:
        if self.config.auth_user or self.config.token_env:
            result = run(
                [self.config.gh_executable, "api", "user", "--jq", ".login"],
                cwd=self.cwd,
                timeout=60,
                env=self.command_env(),
            )
            login = result.stdout.strip()
            if self.config.auth_user and login.lower() != self.config.auth_user.lower():
                raise RuntimeError(
                    f"GitHub token resolved to {login!r}, expected {self.config.auth_user!r}"
                )
            return
        run(
            [self.config.gh_executable, "auth", "status"],
            cwd=self.cwd,
            timeout=60,
            env=self._base_env(),
        )

    def auth_token(self) -> str:
        token = self._configured_token()
        if token:
            return token
        result = run(
            [self.config.gh_executable, "auth", "token"],
            cwd=self.cwd,
            timeout=60,
            env=self._base_env(),
        )
        return result.stdout.strip()

    def list_ready(self) -> list[Issue]:
        output = self._gh(
            "issue", "list", "--state", "open", "--label", self.config.ready_label,
            "--limit", str(self.config.max_issues_per_poll),
            "--json", "number,title,body,url,author,labels",
        )
        return [self._parse_issue(item) for item in json.loads(output or "[]")]

    def get_issue(self, number: int) -> Issue:
        output = self._gh(
            "issue", "view", str(number),
            "--json", "number,title,body,url,author,labels,comments",
        )
        return self._parse_issue(json.loads(output))

    @staticmethod
    def _parse_issue(item: dict[str, Any]) -> Issue:
        comments = tuple(
            {
                "author": str((comment.get("author") or {}).get("login", "unknown")),
                "body": str(comment.get("body", "")),
            }
            for comment in item.get("comments", [])
        )
        return Issue(
            number=int(item["number"]), title=str(item.get("title", "")),
            body=str(item.get("body", "")), url=str(item.get("url", "")),
            author=str((item.get("author") or {}).get("login", "unknown")),
            labels=tuple(str(label["name"]) for label in item.get("labels", [])),
            comments=comments,
        )

    def labels(self, number: int, *, add: tuple[str, ...] = (), remove: tuple[str, ...] = ()) -> None:
        args = ["issue", "edit", str(number)]
        for label in add:
            args.extend(["--add-label", label])
        for label in remove:
            args.extend(["--remove-label", label])
        self._gh(*args)

    def comment(self, number: int, body: str) -> None:
        self._gh("issue", "comment", str(number), "--body", body[:60000])

    def create_pr(self, branch: str, base_branch: str, issue: Issue, body: str, draft: bool) -> str:
        args = [
            "pr", "create", "--head", branch, "--base", base_branch,
            "--title", f"fix: resolve #{issue.number} {issue.title}"[:250], "--body", body,
        ]
        if draft:
            args.append("--draft")
        return self._gh(*args, timeout=180).splitlines()[-1]

    def get_pr(self, number: int) -> PullRequest:
        output = self._gh(
            "pr", "view", str(number), "--json",
            "number,title,body,url,author,baseRefName,headRefName,headRefOid",
        )
        item = json.loads(output)
        return PullRequest(
            number=int(item["number"]),
            title=str(item.get("title", "")),
            body=str(item.get("body", "")),
            url=str(item.get("url", "")),
            author=str((item.get("author") or {}).get("login", "unknown")),
            base_ref_name=str(item.get("baseRefName", "")),
            head_ref_name=str(item.get("headRefName", "")),
            head_ref_oid=str(item.get("headRefOid", "")),
        )

    def pr_diff(self, number: int) -> str:
        return self._gh("pr", "diff", str(number), timeout=180)

    def comment_pr(self, number: int, body: str) -> None:
        self._gh("pr", "comment", str(number), "--body", body[:60000])

    @staticmethod
    def _parse_workflow_run(item: dict[str, Any]) -> WorkflowRun:
        return WorkflowRun(
            database_id=int(item["databaseId"]),
            name=str(item.get("name", "")),
            display_title=str(item.get("displayTitle", "")),
            conclusion=str(item.get("conclusion", "")),
            status=str(item.get("status", "")),
            url=str(item.get("url", "")),
            head_sha=str(item.get("headSha", "")),
        )

    def get_workflow_run(self, run_id: int) -> WorkflowRun:
        output = self._gh(
            "run", "view", str(run_id), "--json",
            "databaseId,name,displayTitle,conclusion,status,url,headSha",
        )
        return self._parse_workflow_run(json.loads(output))

    def latest_failed_run(self, head_sha: str) -> WorkflowRun:
        output = self._gh(
            "run", "list", "--commit", head_sha, "--status", "failure", "--limit", "1",
            "--json", "databaseId,name,displayTitle,conclusion,status,url,headSha",
        )
        items = json.loads(output or "[]")
        if not items:
            raise RuntimeError(f"No failed workflow run found for commit {head_sha}")
        return self._parse_workflow_run(items[0])

    def failed_run_logs(self, run_id: int) -> str:
        return self._gh("run", "view", str(run_id), "--log-failed", timeout=300)

    def pr_status(self, pr_url: str) -> PullRequestStatus:
        output = self._gh("pr", "view", pr_url, "--json", "state,baseRefName")
        item = json.loads(output)
        return PullRequestStatus(
            state=str(item.get("state", "")).upper(),
            base_ref_name=str(item.get("baseRefName", "")),
        )

    def enable_auto_merge(self, pr_url: str, method: str, delete_branch: bool) -> None:
        args = ["pr", "merge", pr_url, "--auto", f"--{method}"]
        if delete_branch:
            args.append("--delete-branch")
        self._gh(*args, timeout=180)

    def ensure_labels(self) -> None:
        definitions = (
            (self.config.ready_label, "0e8a16", "Approved for CodingAgent processing"),
            (self.config.working_label, "fbca04", "CodingAgent is working"),
            (self.config.pr_label, "1d76db", "CodingAgent opened a pull request"),
            (self.config.failed_label, "d93f0b", "CodingAgent run failed"),
        )
        for name, color, description in definitions:
            self._gh(
                "label", "create", name, "--color", color,
                "--description", description, "--force",
            )
