from app.repository.read_boundary import RepositoryReadBoundary
from app.repository.structure_index import PythonRepositoryStructureIndex


def test_rebuild_creates_queryable_module_and_call_snapshot(tmp_path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "repository.py").write_text(
        "class UserRepository:\n"
        "    def get_user(self):\n"
        "        return None\n",
        encoding="utf-8",
    )
    (app_dir / "service.py").write_text(
        "from app.repository import UserRepository\n"
        "\n"
        "class UserService:\n"
        "    def load_user(self):\n"
        "        return UserRepository().get_user()\n",
        encoding="utf-8",
    )

    index = PythonRepositoryStructureIndex(
        boundary=RepositoryReadBoundary(tmp_path),
    )
    build = index.rebuild()

    modules = index.search_modules(
        query="UserService UserRepository dependency",
        limit=5,
    )
    calls = index.search_calls(query="load_user", limit=5)

    assert build.python_file_count == 3
    assert build.parse_error_count == 0
    assert modules.modules[0].path == "app/service.py"
    assert [
        item.module_name
        for item in modules.modules[0].internal_dependencies
    ] == ["app.repository"]
    assert [(item.caller, item.callee) for item in calls.clues] == [
        ("UserService.load_user", "UserRepository"),
        ("UserService.load_user", "UserRepository().get_user"),
    ]


def test_query_time_uses_snapshot_until_explicit_rebuild(tmp_path) -> None:
    path = tmp_path / "service.py"
    path.write_text(
        "class OldService:\n"
        "    def run(self):\n"
        "        old_call()\n",
        encoding="utf-8",
    )
    index = PythonRepositoryStructureIndex(
        boundary=RepositoryReadBoundary(tmp_path),
    )
    index.rebuild()

    path.write_text(
        "class NewService:\n"
        "    def run(self):\n"
        "        new_call()\n",
        encoding="utf-8",
    )

    assert index.search_modules(query="OldService", limit=5).modules[0].path == "service.py"
    assert index.search_calls(query="old_call", limit=5).match_count == 1
    assert index.search_calls(query="new_call", limit=5).match_count == 0

    index.rebuild()

    assert index.search_modules(query="NewService", limit=5).modules[0].path == "service.py"
    assert index.search_calls(query="new_call", limit=5).match_count == 1


def test_module_search_ranks_query_related_module_before_enumeration_order(tmp_path) -> None:
    (tmp_path / "a_noise.py").write_text(
        "class Noise:\n    pass\n",
        encoding="utf-8",
    )
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "code_evidence.py").write_text(
        "class CodeEvidence:\n    pass\n",
        encoding="utf-8",
    )
    (package / "evidence_pack.py").write_text(
        "from app.code_evidence import CodeEvidence\n"
        "class EvidencePack:\n    pass\n",
        encoding="utf-8",
    )

    index = PythonRepositoryStructureIndex(
        boundary=RepositoryReadBoundary(tmp_path),
    )
    index.rebuild()
    report = index.search_modules(
        query="which module connects EvidencePack to CodeEvidence",
        limit=2,
    )

    assert report.modules[0].path == "app/evidence_pack.py"
    assert report.truncated is False


def test_structure_index_reports_parse_error_once_at_build_time(tmp_path) -> None:
    (tmp_path / "good.py").write_text(
        "def good():\n    ok()\n",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text(
        "def broken(:\n    pass\n",
        encoding="utf-8",
    )

    index = PythonRepositoryStructureIndex(
        boundary=RepositoryReadBoundary(tmp_path),
    )
    build = index.rebuild()
    module_report = index.search_modules(query="good", limit=5)
    call_report = index.search_calls(query="good", limit=5)

    assert build.parse_error_count == 1
    assert module_report.parse_error_count == 1
    assert call_report.parse_error_count == 1


def test_named_structure_query_does_not_fall_back_to_full_module_enumeration(tmp_path) -> None:
    for index_value in range(20):
        (tmp_path / f"module_{index_value}.py").write_text(
            f"class Service{index_value}:\n    pass\n",
            encoding="utf-8",
        )

    index = PythonRepositoryStructureIndex(
        boundary=RepositoryReadBoundary(tmp_path),
    )
    index.rebuild()

    report = index.search_modules(
        query="DefinitelyMissingSymbol",
        limit=5,
    )

    assert report.modules == ()
    assert report.truncated is False
