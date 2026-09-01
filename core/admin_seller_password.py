"""Admin-only reset of the unified seller account password."""

from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.models import Seller
from core.services.seller_identity import (
    MIN_PASSWORD_LENGTH,
    SellerIdentityError,
    admin_reset_seller_password,
)


class SellerPasswordResetForm(forms.Form):
    new_password = forms.CharField(
        label='Новый пароль',
        min_length=MIN_PASSWORD_LENGTH,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        error_messages={
            'min_length': f'Пароль должен содержать минимум {MIN_PASSWORD_LENGTH} символов.',
            'required': 'Введите новый пароль.',
        },
    )
    new_password_confirm = forms.CharField(
        label='Повторите новый пароль',
        min_length=MIN_PASSWORD_LENGTH,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        error_messages={
            'min_length': f'Пароль должен содержать минимум {MIN_PASSWORD_LENGTH} символов.',
            'required': 'Повторите новый пароль.',
        },
    )

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('new_password') or ''
        confirm = cleaned_data.get('new_password_confirm') or ''
        if password and confirm and password != confirm:
            self.add_error('new_password_confirm', 'Пароли не совпадают.')
        return cleaned_data


@staff_member_required
@permission_required('core.change_seller', raise_exception=True)
def reset_seller_password(request, seller_id):
    seller = get_object_or_404(Seller, pk=seller_id)

    if request.method == 'POST':
        form = SellerPasswordResetForm(request.POST)
        if form.is_valid():
            try:
                admin_reset_seller_password(seller, form.cleaned_data['new_password'])
            except SellerIdentityError as exc:
                form.add_error('new_password', exc.message)
            else:
                messages.success(request, 'Пароль кабинета продавца обновлён.')
                return redirect(reverse('admin:core_seller_change', args=[seller.pk]))
    else:
        form = SellerPasswordResetForm()

    return render(request, 'admin/core/seller/reset_password.html', {
        'form': form,
        'seller': seller,
        'title': 'Сбросить пароль продавца',
        'opts': Seller._meta,
    })
