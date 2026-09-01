"""Admin-only reset of core.Seller cabinet password.

Does not touch django.contrib.auth.User.
"""

from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.hashers import make_password
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.models import Seller

MIN_PASSWORD_LENGTH = 6


class SellerPasswordResetForm(forms.Form):
    new_password = forms.CharField(
        label='Новый пароль',
        min_length=MIN_PASSWORD_LENGTH,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        error_messages={
            'min_length': 'Пароль должен содержать минимум 6 символов.',
            'required': 'Введите новый пароль.',
        },
    )
    new_password_confirm = forms.CharField(
        label='Повторите новый пароль',
        min_length=MIN_PASSWORD_LENGTH,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        error_messages={
            'min_length': 'Пароль должен содержать минимум 6 символов.',
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
            seller.password_hash = make_password(form.cleaned_data['new_password'])
            seller.must_change_password = False
            seller.save(update_fields=['password_hash', 'must_change_password'])
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
