from .models import Membership


def workspace_role(user):
    if not user.is_authenticated or not user.is_active:
        return None
    if user.is_superuser:
        return Membership.Role.ADMIN
    return Membership.objects.filter(user=user, active=True).values_list('role', flat=True).first()


def navigation_access(request):
    from inventory.permissions import can_access_accounting
    role = workspace_role(request.user)
    accounting = can_access_accounting(request.user)
    count = None
    match = getattr(request, 'resolver_match', None)
    if role is not None and match and (match.namespace in ('inventory', 'procurement', 'repricer') or match.url_name == 'office_home'):
        from .models import Selection
        count = Selection.objects.filter(user=request.user, offer__supplier__name='Omega').count()
    return {'can_access_procurement': role is not None,
            'can_access_accounting': accounting,
            'agparts_role': role,
            'agparts_can_manage': role in ('sales', 'admin'),
            'agparts_basket_count': count}
