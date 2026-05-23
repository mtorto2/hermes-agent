from pathlib import Path

from hermes_cli.repo_status import (
    RepoStatus,
    RepoStatusOptions,
    format_repo_status_report,
    format_swiftbar_menu,
    parse_branch_line,
    parse_porcelain_status,
    parse_repo_status_args,
)


def test_parse_branch_line_with_ahead_and_behind():
    assert parse_branch_line("## dev...origin/dev [ahead 2, behind 1]") == (
        "dev",
        "origin/dev",
        2,
        1,
    )


def test_parse_porcelain_status_counts_file_states():
    status = parse_porcelain_status(
        Path("/tmp/example"),
        "\n".join(
            [
                "## main...origin/main [ahead 1]",
                " M app.py",
                "A  new.py",
                "?? scratch.txt",
                "UU conflicted.txt",
            ]
        ),
    )

    assert status.branch == "main"
    assert status.upstream == "origin/main"
    assert status.ahead == 1
    assert status.unstaged == 1
    assert status.staged == 1
    assert status.untracked == 1
    assert status.conflicted == 1
    assert status.needs_attention


def test_parse_repo_status_args_accepts_all_and_paths():
    options = parse_repo_status_args("all --paths --root ~/dev --depth 2 --limit 10")

    assert options.include_clean is True
    assert options.show_paths is True
    assert options.roots == (Path("~/dev").expanduser(),)
    assert options.max_depth == 2
    assert options.limit == 10
    assert options.errors == ()


def test_format_report_hides_clean_repositories_by_default():
    statuses = [
        RepoStatus(Path("/tmp/clean"), branch="main", upstream="origin/main"),
        RepoStatus(Path("/tmp/dirty"), branch="dev", unstaged=2),
    ]

    report = format_repo_status_report(statuses, options=RepoStatusOptions())

    assert "2 repositories scanned" in report
    assert "- dirty: dev - 2 unstaged" in report
    assert "clean: main" not in report
    assert "1 clean repositories hidden" in report


def test_format_report_can_show_clean_repositories():
    statuses = [RepoStatus(Path("/tmp/clean"), branch="main", upstream="origin/main")]

    report = format_repo_status_report(
        statuses,
        options=RepoStatusOptions(include_clean=True),
    )

    assert "- clean: main in sync - clean" in report


def test_swiftbar_menu_marks_attention_count(monkeypatch):
    from hermes_cli import repo_status

    monkeypatch.setattr(
        repo_status,
        "collect_repo_statuses",
        lambda _options: [
            RepoStatus(Path("/tmp/dirty"), branch="dev", unstaged=1),
            RepoStatus(Path("/tmp/clean"), branch="main"),
        ],
    )

    menu = format_swiftbar_menu("")

    assert menu.splitlines()[0] == "Git: 1"
    assert "dirty: dev - 1 unstaged | color=orange" in menu
    assert "clean: main - clean | color=green" in menu
