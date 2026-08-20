from pathlib import Path

from app.repository.read_boundary import DEFAULT_EXCLUDED_DIRS
from scripts.research_mixed_workload import (
    REALISTIC_EVAL_EXCLUDED_DIRS,
    build_boundary,
)


def test_realistic_noise_profile_policy() -> None:
    assert DEFAULT_EXCLUDED_DIRS <= REALISTIC_EVAL_EXCLUDED_DIRS
    assert "evals" in REALISTIC_EVAL_EXCLUDED_DIRS
    assert "eval" in REALISTIC_EVAL_EXCLUDED_DIRS
    assert ".pytest_cache" in REALISTIC_EVAL_EXCLUDED_DIRS

    assert "scripts" not in REALISTIC_EVAL_EXCLUDED_DIRS
    assert "tests" not in REALISTIC_EVAL_EXCLUDED_DIRS
    assert "docs" not in REALISTIC_EVAL_EXCLUDED_DIRS


def test_build_boundary_uses_realistic_noise_profile(
    tmp_path: Path,
) -> None:
    for directory in ("tests", "docs", "scripts", "evals", ".local"):
        (tmp_path / directory).mkdir()

    (tmp_path / "tests" / "test_noise.py").write_text(
        "natural test noise",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "guide.md").write_text(
        "natural docs noise",
        encoding="utf-8",
    )
    (tmp_path / "scripts" / "maintenance.py").write_text(
        "natural script noise",
        encoding="utf-8",
    )
    (tmp_path / "evals" / "golden.json").write_text(
        "benchmark label leakage",
        encoding="utf-8",
    )
    (tmp_path / ".local" / "probe.py").write_text(
        "current experiment leakage",
        encoding="utf-8",
    )

    files = set(build_boundary(tmp_path).list_files())

    assert "tests/test_noise.py" in files
    assert "docs/guide.md" in files
    assert "scripts/maintenance.py" in files
    assert "evals/golden.json" not in files
    assert ".local/probe.py" not in files
