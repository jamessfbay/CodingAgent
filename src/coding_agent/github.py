from __future__ import annotations

import json
import pathlib
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


class GitHub:
    def __init__(self, config: GitHubConfig, cwd: pathlib.Path):
        self.config = config
        self.cwd = cwd

    def _gh(self, *args: str, timeout: int = 120, check: bool = True) -> str:
        result = run(
            [self.config.gh_executable, *args, "--repo", self.config.repository],
            cwd=self.cwd, timeout=timeout, check=check,
        )
        return result.stdout.strip()

    def auth_status(self) -> None:
        run([self.config.gh_executable, "auth", "status"], cwd=self.cwd, timeout=60)

    def auth_token(self) -> str:
        result = run([self.config.gh_executable, "auth", "token"], cwd=self.cwd, timeout=60)
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
