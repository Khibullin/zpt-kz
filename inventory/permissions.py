"""Accounting access is separate from procurement and site administration."""
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from procurement.permissions import workspace_role


def can_access_accounting(user):
    role = workspace_role(user)
    return role == 'admin' or (
        role == 'sales' and user.has_perm('inventory.manage_accounting')
    )


def accounting_access(view):
    @login_required(login_url='office_login')
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        request.procurement_role = workspace_role(request.user)
        if not can_access_accounting(request.user):
            return render(request, 'procurement/access_denied.html', status=403)
        return view(request, *args, **kwargs)
    return wrapped
