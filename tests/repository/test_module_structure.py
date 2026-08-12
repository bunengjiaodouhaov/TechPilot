from app.repository.module_structure import PythonModuleStructureService
from app.repository.read_boundary import RepositoryReadBoundary


def test_module_structure_resolves_internal_dependencies_and_top_level_symbols(
    tmp_path,
) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "repository.py").write_text(
        "class UserRepository:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (app_dir / "service.py").write_text(
        "from app.repository import UserRepository\n"
        "\n"
        "class UserService:\n"
        "    def load_user(self):\n"
        "        return UserRepository()\n"
        "\n"
        "def health():\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )

    service = PythonModuleStructureService(
        boundary=RepositoryReadBoundary(tmp_path),
    )
    report = service.inspect_repository(limit=20)

    by_name = {module.module_name: module for module in report.modules}
    service_module = by_name["app.service"]

    assert report.truncated is False
    assert report.parse_error_count == 0
    assert [
        dependency.module_name
        for dependency in service_module.internal_dependencies
    ] == ["app.repository"]
    assert [
        dependency.path
        for dependency in service_module.internal_dependencies
    ] == ["app/repository.py"]
    assert [symbol.name for symbol in service_module.symbols] == [
        "UserService",
        "health",
    ]
    assert "load_user" not in {
        symbol.name for symbol in service_module.symbols
    }


def test_module_structure_reports_parse_errors_and_truncation(tmp_path) -> None:
    (tmp_path / "a.py").write_text(
        "def a():\n    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        "def broken(:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "c.py").write_text(
        "def c():\n    return 3\n",
        encoding="utf-8",
    )

    service = PythonModuleStructureService(
        boundary=RepositoryReadBoundary(tmp_path),
    )

    full = service.inspect_repository(limit=10)
    limited = service.inspect_repository(limit=1)

    assert full.python_file_count == 3
    assert full.module_count == 2
    assert full.parse_error_count == 1
    assert full.truncated is False
    assert limited.python_file_count == 3
    assert limited.module_count == 1
    assert limited.truncated is True
