from types import SimpleNamespace

import pytest

from app.auth.authorization import (
    WorkspaceAccessError,
    WorkspaceAuthorizer,
    WorkspaceRoleError,
)
from app.models.workspace_member import WorkspaceRole


class FakeSession:
    def __init__(self, membership: object | None) -> None:
        self.membership = membership

    async def scalar(self, _statement: object) -> object | None:
        return self.membership


@pytest.mark.asyncio
async def test_missing_membership_is_fail_closed() -> None:
    authorizer = WorkspaceAuthorizer(session=FakeSession(None))  # type: ignore[arg-type]

    with pytest.raises(WorkspaceAccessError):
        await authorizer.require_access(user_id=7, workspace_id=2)


@pytest.mark.asyncio
async def test_member_can_read_but_cannot_use_owner_operation() -> None:
    membership = SimpleNamespace(role=WorkspaceRole.MEMBER.value)
    authorizer = WorkspaceAuthorizer(session=FakeSession(membership))  # type: ignore[arg-type]

    assert await authorizer.require_access(user_id=7, workspace_id=2) is membership
    with pytest.raises(WorkspaceRoleError):
        await authorizer.require_access(
            user_id=7,
            workspace_id=2,
            owner_required=True,
        )


@pytest.mark.asyncio
async def test_owner_passes_owner_operation() -> None:
    membership = SimpleNamespace(role=WorkspaceRole.OWNER.value)
    authorizer = WorkspaceAuthorizer(session=FakeSession(membership))  # type: ignore[arg-type]

    assert (
        await authorizer.require_access(
            user_id=7,
            workspace_id=2,
            owner_required=True,
        )
        is membership
    )
