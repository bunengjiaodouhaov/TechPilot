from app.repository.call_relationships import PythonCallRelationshipService
from app.repository.read_boundary import RepositoryReadBoundary


def test_extracts_static_caller_and_callee_clues(tmp_path) -> None:
    (tmp_path / "service.py").write_text(
        "class UserService:\n"
        "    def load_user(self):\n"
        "        record = self.repository.get_user()\n"
        "        return normalize(record)\n",
        encoding="utf-8",
    )

    service = PythonCallRelationshipService(
        boundary=RepositoryReadBoundary(tmp_path),
    )
    report = service.inspect_repository(query="load_user", limit=10)

    assert report.truncated is False
    assert report.parse_error_count == 0
    assert [(clue.caller, clue.callee) for clue in report.clues] == [
        ("UserService.load_user", "self.repository.get_user"),
        ("UserService.load_user", "normalize"),
    ]
    assert [clue.line_start for clue in report.clues] == [3, 4]


def test_can_find_a_call_by_callee_name(tmp_path) -> None:
    (tmp_path / "service.py").write_text(
        "def load_user():\n"
        "    return repository.get_user()\n",
        encoding="utf-8",
    )

    report = PythonCallRelationshipService(
        boundary=RepositoryReadBoundary(tmp_path),
    ).inspect_repository(query="get_user", limit=10)

    assert report.match_count == 1
    assert report.clues[0].caller == "load_user"
    assert report.clues[0].callee == "repository.get_user"


def test_reports_parse_errors_and_truncation(tmp_path) -> None:
    (tmp_path / "a.py").write_text(
        "def load_user():\n"
        "    first()\n"
        "    second()\n",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text(
        "def broken(:\n"
        "    pass\n",
        encoding="utf-8",
    )

    report = PythonCallRelationshipService(
        boundary=RepositoryReadBoundary(tmp_path),
    ).inspect_repository(query="load_user", limit=1)

    assert report.python_file_count == 2
    assert report.parse_error_count == 1
    assert report.match_count == 1
    assert report.truncated is True


def test_skips_module_level_calls_without_a_caller_symbol(tmp_path) -> None:
    (tmp_path / "bootstrap.py").write_text(
        "configure()\n"
        "def run():\n"
        "    execute()\n",
        encoding="utf-8",
    )

    report = PythonCallRelationshipService(
        boundary=RepositoryReadBoundary(tmp_path),
    ).inspect_repository(query="execute", limit=10)

    assert [(clue.caller, clue.callee) for clue in report.clues] == [
        ("run", "execute"),
    ]
