from io import BytesIO
from pathlib import Path
import zipfile

import pytest

from app.api import repository_product


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_safe_repository_zip_extracts_python_source(tmp_path: Path) -> None:
    archive_path = tmp_path / "repo.zip"
    archive_path.write_bytes(
        _zip_bytes({"demo/app.py": "def hello():\\n    return 'world'\\n"})
    )
    target = tmp_path / "source"
    target.mkdir()

    repository_product._extract_archive(archive_path, target)

    assert (target / "demo/app.py").read_text() == "def hello():\\n    return 'world'\\n"


def test_repository_zip_rejects_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "repo.zip"
    archive_path.write_bytes(_zip_bytes({"../escape.py": "print('no')"}))
    target = tmp_path / "source"
    target.mkdir()

    with pytest.raises(Exception):
        repository_product._extract_archive(archive_path, target)
