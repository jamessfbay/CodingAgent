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
