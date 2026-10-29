from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from .permissions import workspace_role


class WorkspaceAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Логин или email'

    def clean_username(self):
        value = self.cleaned_data['username'].strip()
        return value.lower() if '@' in value else value

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if workspace_role(user) is None:
            raise ValidationError('Доступ к кабинету закупок не выдан. Обратитесь к администратору ZPT.KZ.', code='workspace_access_denied')


class WorkspaceLoginView(LoginView):
    template_name = 'procurement/login.html'
    authentication_form = WorkspaceAuthenticationForm
    next_page = '/office/procurement/'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if workspace_role(request.user) is None:
                return render(request, 'procurement/access_denied.html', status=403)
            return redirect(self.get_success_url())
        return super().dispatch(request, *args, **kwargs)
