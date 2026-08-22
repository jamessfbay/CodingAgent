from coding_agent.cli import parser


def test_patch_issue_arguments():
    args = parser().parse_args([
        "patch-issue", "--repository", "org/repo", "--issue", "12",
    ])
    assert args.command == "patch-issue"
    assert args.repository == "org/repo"
    assert args.issue == 12


def test_review_pr_arguments_are_read_only_by_default():
    args = parser().parse_args(["review-pr", "--pr", "8", "--json"])
    assert args.command == "review-pr"
    assert args.pr == 8
    assert args.publish is False
    assert args.json is True


def test_diagnose_ci_can_infer_run_from_pr():
    args = parser().parse_args(["diagnose-ci", "--pr", "8", "--publish"])
    assert args.command == "diagnose-ci"
    assert args.run_id is None
    assert args.pr == 8
    assert args.publish is True
    assert args.json is False
