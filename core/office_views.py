"""A navigation-only entry point; each destination retains its own access checks."""
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from procurement.permissions import workspace_role
from inventory.permissions import can_access_accounting
from marketing.permissions import user_can_access_marketing_cabinet


def office_access(user):
    return user.is_authenticated and user.is_active and (user.is_staff or user.is_superuser or workspace_role(user) is not None)


class OfficeAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self,user):
        super().confirm_login_allowed(user)
        if not office_access(user):
            raise ValidationError('Доступ к служебному кабинету не выдан. Обратитесь к администратору.',code='office_access_denied')


class OfficeLoginView(LoginView):
    template_name='office/login.html'
    authentication_form=OfficeAuthenticationForm
    next_page='/office/'
    redirect_authenticated_user=True


@never_cache
@login_required(login_url='office_login')
def office_home(request):
    if not office_access(request.user):
        return render(request,'office/denied.html',status=403)
    role=workspace_role(request.user)
    return render(request,'office/home.html',{
        'office_accounting':can_access_accounting(request.user),
        'office_procurement':role is not None,
        'office_staff':request.user.is_staff,
        'office_marketing':user_can_access_marketing_cabinet(request.user),
        'office_role':'Администратор' if request.user.is_staff or role=='admin' else 'Сотрудник',
    })
