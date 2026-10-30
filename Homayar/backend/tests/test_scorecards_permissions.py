"""HOM-54: the scorecard permissions (SPEC section 5).

Every workspace role reads scorecards; only the owner and admins publish them; no API key
scope carries either permission (session only).  The route behaviour per role is in
``test_scorecards_api.py``.
"""

from __future__ import annotations

from backend.database.saas_models import WorkspaceRole
from backend.security.authorization import API_KEY_SCOPES, ROLE_PERMISSIONS, Permission


def _roles_with(permission: Permission) -> set[WorkspaceRole]:
    return {role for role, granted in ROLE_PERMISSIONS.items() if permission in granted}


def test_the_two_permissions_have_their_strings():
    assert Permission.SCORECARD_READ.value == "scorecard:read"
    assert Permission.SCORECARD_MANAGE.value == "scorecard:manage"


def test_every_role_reads_and_only_the_owner_and_admins_manage():
    assert _roles_with(Permission.SCORECARD_READ) == {
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.MEMBER,
        WorkspaceRole.VIEWER,
    }
    assert _roles_with(Permission.SCORECARD_MANAGE) == {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}


def test_no_api_key_scope_carries_a_scorecard_permission():
    assert Permission.SCORECARD_READ not in API_KEY_SCOPES
    assert Permission.SCORECARD_MANAGE not in API_KEY_SCOPES
