from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys

from .config import load_settings
from .service import CodingAgentService


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="GitHub Issue to Codex PR orchestrator")
    result.add_argument(
        "--config", default=os.environ.get("CODING_AGENT_CONFIG", "config.toml"),
        help="Path to TOML config (default: config.toml)",
    )
    result.add_argument("--verbose", action="store_true")
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check GitHub, Git, Codex, and repository configuration")
    sub.add_parser("bootstrap", help="Run checks and create GitHub labels")
    once = sub.add_parser("once", help="Process ready issues once")
    once.add_argument("--issue", type=int, help="Process a specific issue number")
    once.add_argument(
        "--repository", help="Limit processing to one configured owner/repository",
    )
    patch_issue = sub.add_parser(
        "patch-issue", help="Turn one reviewed Issue into a validated candidate pull request",
    )
    patch_issue.add_argument("--issue", type=int, required=True, help="GitHub Issue number")
    patch_issue.add_argument(
        "--repository", help="Limit processing to one configured owner/repository",
    )
    review = sub.add_parser(
        "review-pr", help="Run a read-only Codex review of a pull request",
    )
    review.add_argument("--pr", type=int, required=True, help="GitHub pull request number")
    review.add_argument(
        "--repository", help="Limit processing to one configured owner/repository",
    )
    review.add_argument(
        "--publish", action="store_true", help="Post the generated report as a PR comment",
    )
    review.add_argument(
        "--json", action="store_true", help="Print the schema-validated JSON result",
    )
    diagnose = sub.add_parser(
        "diagnose-ci", help="Run a read-only diagnosis of a failed GitHub Actions run",
    )
    diagnose.add_argument("--run-id", type=int, help="GitHub Actions run database ID")
    diagnose.add_argument(
        "--pr", type=int,
        help="PR context; if --run-id is omitted, use its latest failed run",
    )
    diagnose.add_argument(
        "--repository", help="Limit processing to one configured owner/repository",
    )
    diagnose.add_argument(
        "--publish", action="store_true", help="Post the diagnosis as a PR comment",
    )
    diagnose.add_argument(
        "--json", action="store_true", help="Print the schema-validated JSON result",
    )
    update = sub.add_parser(
        "update", help="Fast-forward a production checkout and copy tracked files",
    )
    update.add_argument(
        "--repository", help="Update one configured owner/repository",
    )
    sub.add_parser("watch", help="Continuously poll for ready issues")
    sub.add_parser("history", help="Show recent local run state")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = load_settings(pathlib.Path(args.config))
        service = CodingAgentService(settings)
        if args.command == "doctor":
            failures = service.doctor()
            if failures:
                for failure in failures:
                    logging.error(failure)
                return 1
            logging.info("All checks passed")
        elif args.command == "bootstrap":
            service.bootstrap()
            logging.info("Bootstrap complete")
        elif args.command == "once":
            count = service.run_once(args.issue, args.repository)
            logging.info("Processed %d issue(s)", count)
        elif args.command == "patch-issue":
            count = service.run_once(args.issue, args.repository)
            logging.info("Processed %d issue(s)", count)
        elif args.command == "review-pr":
            print(service.review_pr(
                args.pr, args.repository, publish=args.publish, json_output=args.json,
            ))
        elif args.command == "diagnose-ci":
            print(service.diagnose_ci(
                args.run_id, args.repository, pr_number=args.pr, publish=args.publish,
                json_output=args.json,
            ))
        elif args.command == "update":
            commit = service.update_production(args.repository)
            logging.info("Production files updated to %s", commit)
        elif args.command == "watch":
            service.watch()
        elif args.command == "history":
            for row in service.state.recent():
                print("\t".join(str(value or "") for value in row))
        return 0
    except Exception as exc:
        logging.exception("CodingAgent failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
