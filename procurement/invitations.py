"""One-use invitations create procurement-only accounts, never reset existing ones."""
import hashlib
import secrets
from datetime import timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_GET

from .models import Activity, ManagerInvitation, Membership, Preferences
from .permissions import workspace_role


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def account_exists(email):
    return get_user_model().objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists()


class InvitationForm(forms.Form):
    email = forms.EmailField(label='Email менеджера', max_length=150)

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if account_exists(email):
            raise forms.ValidationError('Такая учётная запись уже существует. Назначьте ей роль через список пользователей; приглашение не меняет существующие пароли.')
        return email


def private_response(request, **context):
    response = render(request, 'procurement/invitation.html', context)
    response['Referrer-Policy'] = 'same-origin'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive, nosnippet'
    return response


@never_cache
@require_http_methods(['GET', 'POST'])
def invite_manager(request):
    if not request.user.is_authenticated:
        return redirect(reverse('procurement:login') + '?next=' + request.path)
    if workspace_role(request.user) != Membership.Role.ADMIN:
        return render(request, 'procurement/access_denied.html', status=403)
    form = InvitationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        token = secrets.token_urlsafe(32)
        expires_at = timezone.now() + timedelta(hours=72)
        with transaction.atomic():
            ManagerInvitation.objects.update_or_create(email=email, defaults={
                'token_digest': digest(token), 'created_by': request.user,
                'expires_at': expires_at, 'used_at': None, 'accepted_by': None,
            })
            Activity.objects.create(user=request.user, action='manager_invited', details={'email': email, 'role': 'sales', 'expires_at': expires_at.isoformat()})
        # Return the secret only here, never store it or send an unsolicited email.
        url = request.build_absolute_uri(reverse('procurement:accept_invitation', args=[token]))
        return private_response(request, created=True, invitation_url=url, email=email, expires_at=expires_at)
    return private_response(request, create=True, form=form)


def usable(invitation):
    return (invitation is not None and invitation.used_at is None
            and invitation.expires_at > timezone.now()
            and workspace_role(invitation.created_by) == Membership.Role.ADMIN)


@never_cache
@require_GET
def accept_invitation(request, token):
    invitation = ManagerInvitation.objects.select_related('created_by').filter(token_digest=digest(token)).first()
    if not usable(invitation) or account_exists(invitation.email):
        return private_response(request, invalid=True)
    # Remove the bearer secret from the address bar before showing a password form.
    # A GET stores only a digest in this browser session and never consumes the invite.
    request.session['procurement_invitation_digest'] = invitation.token_digest
    response = redirect('procurement:activate_invitation')
    response['Referrer-Policy'] = 'no-referrer'
    return response


@never_cache
@sensitive_post_parameters('new_password1', 'new_password2')
@require_http_methods(['GET', 'POST'])
def activate_invitation(request):
    invitation = ManagerInvitation.objects.select_related('created_by').filter(
        token_digest=request.session.get('procurement_invitation_digest', '')).first()
    if not usable(invitation) or account_exists(invitation.email):
        return private_response(request, invalid=True)
    user = get_user_model()(username=invitation.email, email=invitation.email)
    form = SetPasswordForm(user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                invitation = ManagerInvitation.objects.select_for_update().select_related('created_by').get(pk=invitation.pk)
                if not usable(invitation) or account_exists(invitation.email):
                    return private_response(request, invalid=True)
                # Never attach to an existing account or grant global staff/admin rights.
                user = form.save(commit=False)
                user.is_staff = False
                user.is_superuser = False
                user.save(force_insert=True)
                Membership.objects.create(user=user, role=Membership.Role.SALES)
                source = Preferences.objects.filter(user=invitation.created_by).first()
                Preferences.objects.create(user=user, commission=source.commission if source else None,
                    expenses=source.expenses if source else None)
                invitation.used_at = timezone.now()
                invitation.accepted_by = user
                invitation.save(update_fields=['used_at', 'accepted_by'])
                Activity.objects.create(user=user, action='manager_invitation_accepted', details={'invitation_id': invitation.pk, 'invited_by': invitation.created_by_id, 'role': 'sales'})
        except IntegrityError:
            return private_response(request, invalid=True)
        request.session.pop('procurement_invitation_digest', None)
        # Do not switch an existing signed-in session or expose account credentials.
        return private_response(request, activated=True, email=invitation.email)
    return private_response(request, form=form, email=invitation.email, expires_at=invitation.expires_at)
