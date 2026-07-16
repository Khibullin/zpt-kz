from __future__ import annotations

MARKETING_CABINET_PERMISSION = 'marketing.access_marketing_cabinet'


def user_can_access_marketing_cabinet(user) -> bool:
    if not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return bool(getattr(user, 'is_staff', False)) and user.has_perm(
        MARKETING_CABINET_PERMISSION,
    )
