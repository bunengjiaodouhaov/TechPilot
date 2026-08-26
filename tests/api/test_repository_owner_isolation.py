import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import repository_product
from app.auth.dependencies import AuthPrincipal


def _write_repository(
    root: Path,
    *,
    repository_id: str,
    owner_user_id: int | None,
) -> None:
    directory = root / repository_id
    source = directory / "source"
    source.mkdir(parents=True)
    (source / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    manifest: dict[str, object] = {
        "repository_id": repository_id,
        "name": repository_id,
    }
    if owner_user_id is not None:
        manifest["owner_user_id"] = owner_user_id
    (directory / "techpilot_repository.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def test_uploaded_repository_is_visible_only_to_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repository_product, "_REPOSITORY_STORE", tmp_path)
    _write_repository(tmp_path, repository_id="owned", owner_user_id=7)

    owner = AuthPrincipal(id=7, email="owner@example.com")
    other = AuthPrincipal(id=8, email="other@example.com")

    assert repository_product._uploaded_root(
        "owned",
        principal=owner,
    ).name == "source"
    with pytest.raises(HTTPException) as exc:
        repository_product._uploaded_root("owned", principal=other)
    assert exc.value.status_code == 404


def test_legacy_repository_is_preserved_only_for_demo_principal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repository_product, "_REPOSITORY_STORE", tmp_path)
    _write_repository(tmp_path, repository_id="legacy", owner_user_id=None)

    demo = AuthPrincipal(
        id=1,
        email="demo@techpilot.local",
        is_demo=True,
    )
    normal = AuthPrincipal(id=7, email="user@example.com")

    assert repository_product._uploaded_root(
        "legacy",
        principal=demo,
    ).name == "source"
    with pytest.raises(HTTPException):
        repository_product._uploaded_root("legacy", principal=normal)
