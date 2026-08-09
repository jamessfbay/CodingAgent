import sqlite3

from coding_agent.state import State


def test_state_migrates_legacy_database_and_records_repository(tmp_path):
    path = tmp_path / "runs.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE runs (
        id INTEGER PRIMARY KEY, issue_number INTEGER NOT NULL, branch TEXT,
        status TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    connection.commit()
    connection.close()

    state = State(path)
    run_id = state.start("org/repo", 3, "codex/issue-3")
    state.finish(run_id, "pr-open", "https://example.test/pr/1")

    row = state.recent(1)[0]
    assert row[:4] == ("org/repo", 3, "codex/issue-3", "pr-open")
