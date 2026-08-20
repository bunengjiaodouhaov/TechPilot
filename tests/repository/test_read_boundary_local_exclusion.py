from app.repository.read_boundary import RepositoryReadBoundary


def test_local_artifacts_are_not_part_of_repository_runtime_read_scope(tmp_path) -> None:
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    local_dir = tmp_path / ".local" / "day11"
    local_dir.mkdir(parents=True)
    (local_dir / "backup.py").write_text("value = 2\n", encoding="utf-8")

    boundary = RepositoryReadBoundary(tmp_path)

    assert boundary.list_files() == ["app.py"]
