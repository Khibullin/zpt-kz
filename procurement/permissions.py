from .models import Membership


def workspace_role(user):
    if not user.is_authenticated or not user.is_active:
        return None
    if user.is_superuser:
        return Membership.Role.ADMIN
    return Membership.objects.filter(user=user, active=True).values_list('role', flat=True).first()


def navigation_access(request):
    from inventory.permissions import can_access_accounting
    return {'can_access_procurement': workspace_role(request.user) is not None,
            'can_access_accounting': can_access_accounting(request.user)}
