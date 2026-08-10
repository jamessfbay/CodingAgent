# JOX CodingAgent

`CodingAgent` is a Python service that watches one or more GitHub repositories and turns reviewed Issues into tested pull requests by orchestrating the locally authenticated Codex CLI. It does not send production credentials to Codex. Optionally, after a PR is merged, it can update a dedicated checkout and copy tracked files into a local production directory without building or restarting services.

## Flow

1. A maintainer reviews an Issue and adds `agent:ready`.
2. The daemon claims it with `agent:working` and removes `agent:ready`.
3. A fresh Git worktree is created from `origin/main`.
4. GitHub-uploaded images in the Issue body or comments are downloaded to a temporary directory and attached to `codex exec`.
5. `codex exec` implements the smallest fix and regression tests.
6. Protected paths are rejected and configured validation commands run.
7. The daemon commits, pushes, and opens a PR linked to the Issue.
8. GitHub Actions runs API, frontend, and CodingAgent CI.
9. Optional GitHub auto-merge waits for branch protection.
10. A successful `main` CI run may deploy through the protected `production` environment and a dedicated self-hosted runner.

## Install

Requirements: Python 3.11+, Git, GitHub CLI (`gh`), and Codex CLI. Create the environment without external runtime packages:

```bash
cd /var/www/workspace/CodingAgent
python3 -m venv .venv
.venv/bin/python -m pip install -e . pytest
cp config.example.toml config.toml
```

Edit `config.toml`. If `gh` is not on `PATH`, set `github.gh_executable` to its absolute path. Keep `enable_auto_merge = false` until branch protection and CI are configured.

Then authenticate the service user once with the bundled headless login helper:

```bash
./auth.sh
```

The name and icon used in Issue comments and PR summaries can be customized under
`[automation]` (set `agent_icon = ""` to omit the icon):

```toml
[automation]
agent_name = "CodingAgent"
agent_icon = "🤖"
```

## Multiple repositories

Shared GitHub labels, credentials, Codex settings, and automation policy are configured once. Add one `[[repositories]]` entry for every local checkout:

```toml
[github]
ready_label = "agent:ready"
gh_executable = "gh"

[scheduler]
poll_seconds = 60
max_parallel_repositories = 2

[[repositories]]
repository = "unsungb/base-all"
path = "/var/www/workspace/base-all"
base_branch = "main"
worktree_root = "/tmp/base-all-coding-agent"

[[repositories.validation]]
name = "tests"
argv = ["/var/www/workspace/base-all/.venv/bin/python", "-m", "pytest", "-q"]

[[repositories]]
repository = "your-org/another-repo"
path = "/var/www/workspace/another-repo"
base_branch = "main"
worktree_root = "/tmp/another-repo-coding-agent"

[repositories.github]
ready_label = "agent:approved" # optional override for this repository

[[repositories.validation]]
name = "tests"
argv = ["npm", "test"]
```

Each repository must have a unique `path` and `worktree_root`, and its `origin` must match the configured `owner/repository`. Issues within one repository run serially to avoid Git locks; different repositories run concurrently up to `scheduler.max_parallel_repositories`. The old single-repository `[repository]` configuration remains supported.

### Per-repository GitHub authentication

Each repository can select a stored GitHub CLI account without changing the
globally active account:

```toml
[[repositories]]
repository = "unsungb/base-all"
path = "/var/www/workspace/base-all"

[repositories.github]
auth_user = "unsungb"
auth_config_dir = "state/github-auth/base-all"
```

Authenticate or verify a repository on a headless server with the interactive
project selector. The helper first verifies that repository's isolated GitHub
credentials, then verifies the shared Codex CLI credentials:

```bash
./auth.sh
```

It can also be selected directly by number or repository name:

```bash
./auth.sh --list
./auth.sh unsungb/base-all
./auth.sh --force unsungb/base-all
```

After GitHub authentication, the helper clones a missing repository into its
configured `path`. The recommended layout is
`CodingAgent/state/repositories/<owner>/<repository>`; `state/` is ignored by Git.
An existing Git checkout is retained, while a non-empty non-Git directory is
rejected to avoid overwriting files.

When authentication is needed, the script displays the GitHub or Codex device URL
and one-time code. Open the URL on another device and complete the login. GitHub
credentials are saved under that repository's `auth_config_dir`, which should
remain inside the ignored `state/` directory. Codex credentials use the Codex
CLI's normal credential store. `--force` starts both login flows again when valid
credentials already exist.

For service deployments, a dedicated token environment variable can be used
instead. Store only its variable name in TOML, never the token value:

```toml
[repositories.github]
token_env = "BASE_ALL_GITHUB_TOKEN"
```

The selected credential and GitHub CLI configuration are isolated per worker and
are used for GitHub API calls,
Issue images, and authenticated Git fetch/push/clone operations. This remains safe
when repositories run concurrently.

To update a local production checkout after its PR is merged, set
`production_path` on that repository:

```toml
[[repositories]]
repository = "unsungb/base-all"
path = "/var/www/workspace/base-all"
base_branch = "main"
worktree_root = "/tmp/base-all-coding-agent"
production_path = "/srv/jox-production"
production_checkout_path = "/srv/jox-production-checkout"
```

On each polling cycle, merged PRs are detected through GitHub. The production
checkout is created automatically when needed, then updated with `git fetch` and
`git merge --ff-only origin/<base_branch>`. Only Git-tracked files are copied to
`production_path`; untracked runtime files such as `.env.local`, `.secrets`,
`node_modules`, and `.next` are preserved. Files removed from Git between deployed
commits are removed from production. Open PRs remain pending, and closed unmerged
PRs are never synchronized.

## Initialize and run

```bash
.venv/bin/coding-agent doctor
.venv/bin/coding-agent bootstrap
.venv/bin/coding-agent once
.venv/bin/coding-agent once --issue 123
.venv/bin/coding-agent once --repository unsungb/base-all
.venv/bin/coding-agent once --repository unsungb/base-all --issue 123
.venv/bin/coding-agent watch
```

`bootstrap` creates or updates the four workflow labels in every configured repository. An Issue is processed only after a maintainer adds `agent:ready`. On failure it receives `agent:failed`; fix the service problem and re-add `agent:ready` to retry. With multiple repositories, `--issue` must be paired with `--repository` because Issue numbers are repository-local.

## Run continuously

Review `systemd/coding-agent.service`, then install it as root:

```bash
sudo cp systemd/coding-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now coding-agent
sudo journalctl -u coding-agent -f
```

The service uses an isolated worktree for each repository and stores repository-qualified run metadata in `state/runs.sqlite3`. Existing single-repository databases are upgraded automatically. Full Codex JSONL output stays in the process journal; configure normal journal retention and access controls.

## GitHub protection

Before auto-merge, protect `main` and require these checks:

- `api`
- `frontend`
- `coding-agent`

The current frontend intentionally sets `typescript.ignoreBuildErrors = true` and has pre-existing full-project TypeScript errors. CI therefore runs the checked-in Node unit tests but does not claim that full `tsc --noEmit` is clean.

Require at least one human review for security, contract, migration, payment, authentication, and deployment changes. The default protected-path rules prevent the agent from editing workflow files, this orchestrator, and common secret files.

## Production deployment

The deployment workflow is disabled unless the repository variable `ENABLE_PRODUCTION_DEPLOY` equals `true`. It additionally requires:

- a self-hosted runner labeled `jox-production`;
- a protected GitHub Environment named `production` with required reviewers;
- `DEPLOY_RESTART_COMMAND` configured as a repository/environment variable;
- optionally `DEPLOY_HEALTHCHECK_URL` (defaults to `https://api.jox.fun/health`).

The checked-in deployment script only fast-forwards to `origin/main`, builds the frontend, invokes the configured process-manager restart hook, and verifies health. Rollback remains an explicit operator action because database and blockchain changes may not be safely reversible.

The optional per-repository production synchronization only updates tracked files
after GitHub reports the PR as merged. It does not build, migrate, restart services,
or run deployment hooks.

## Security model

- Issue text and comments are treated as untrusted input in the Codex prompt.
- Only GitHub-hosted Issue image URLs are downloaded. Redirect hosts, image signatures, counts, and byte sizes are validated; temporary files are removed after each run.
- Codex runs with `workspace-write`, no interactive approvals, and no production environment injection.
- Validation commands are argv arrays, not shell strings.
- Every configured local `origin` is checked against its GitHub `owner/repository` before processing.
- Production synchronization uses a separate checkout with the expected branch, matching origin, and fast-forward-only updates.
- Only the orchestrator performs GitHub writes.
- Production secrets are available only to the protected deployment job after approval.
- Do not use a GitHub personal token with broader scopes than required, and prefer a dedicated machine account or GitHub App for long-term operation.
