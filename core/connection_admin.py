from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django import forms
from core.models import RequestInteraction, RequestOffer, TelegramRequestPublication, TelegramPublicationAttempt
from core.services.telegram_requests import publish


class PublicationForm(forms.ModelForm):
    class Meta:
        model = TelegramRequestPublication
        fields = '__all__'

    def clean(self):
        values = super().clean()
        if self.instance.pk and self.instance.status != 'draft':
            if any(name in self.changed_data for name in ('request', 'text', 'reviewed')):
                raise ValidationError('Отправленную или неопределённую публикацию нельзя редактировать. Сначала ручная сверка.')
        return values


@admin.register(TelegramRequestPublication)
class PublicationAdmin(admin.ModelAdmin):
    form = PublicationForm
    list_display = ('request', 'status', 'message_id', 'message_url', 'updated_at')
    readonly_fields = ('status', 'message_id', 'message_url', 'created_by', 'updated_at')
    actions = ['publish_selected']
    autocomplete_fields = ['request']

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description='Опубликовать проверенный черновик в Telegram', permissions=['change'])
    def publish_selected(self, request, queryset):
        # One explicit publication at a time; no bulk broadcast action.
        if queryset.count() != 1:
            self.message_user(request, 'Выберите ровно одну заявку.', messages.ERROR)
            return
        publication = queryset.get()
        try:
            publish(publication.pk, request.user)
        except ValueError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.log_change(request, publication, 'Опубликовано в Telegram по явной команде администратора')
            self.message_user(request, 'Публикация сохранена.')


@admin.register(RequestInteraction)
class InteractionAdmin(admin.ModelAdmin):
    list_display = ('request', 'seller', 'actor', 'kind', 'source', 'position', 'created_at')
    list_filter = ('actor', 'kind', 'source')
    search_fields = ('request__id', 'seller__name')
    readonly_fields = tuple(field.name for field in RequestInteraction._meta.fields)
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RequestOffer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ('request', 'seller', 'article', 'price', 'quantity', 'availability', 'expires_at')
    autocomplete_fields = ['request', 'seller']


@admin.register(TelegramPublicationAttempt)
class AttemptAdmin(admin.ModelAdmin):
    list_display = ('publication', 'actor', 'result', 'created_at')
    readonly_fields = tuple(field.name for field in TelegramPublicationAttempt._meta.fields)
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False


class PreparePublicationForm(forms.Form):
    text = forms.CharField(max_length=1500, label='Предпросмотр и редактирование текста', widget=forms.Textarea(attrs={'rows': 8, 'cols': 80}))
    reviewed = forms.BooleanField(label='Я проверил текст: без телефона, ФИО, VIN, адреса и персональных ссылок')


def prepare_publication(modeladmin, request, request_id):
    from django.http import Http404, HttpResponseForbidden
    from django.shortcuts import get_object_or_404, redirect, render
    from django.db import transaction
    from django.urls import reverse
    from core.models import Request
    from core.services.request_connections import enabled, safe_summary
    from core.services.telegram_requests import validate_publication
    if not enabled():
        raise Http404
    req = get_object_or_404(Request, pk=request_id)
    if not modeladmin.has_change_permission(request, req):
        return HttpResponseForbidden()
    if not request.user.has_perm('core.add_telegramrequestpublication') or not request.user.has_perm('core.change_telegramrequestpublication'):
        return HttpResponseForbidden()
    existing = TelegramRequestPublication.objects.filter(request=req).first()
    if existing and existing.status != 'draft':
        return redirect('admin:core_telegramrequestpublication_change', existing.pk)
    form = PreparePublicationForm(request.POST or None, initial={'text': existing.text if existing else safe_summary(req), 'reviewed': False})
    if request.method == 'POST' and form.is_valid():
        publication = TelegramRequestPublication(request=req, text=form.cleaned_data['text'], reviewed=True, created_by=request.user)
        try:
            validate_publication(publication)
        except ValueError as exc:
            form.add_error(None, str(exc))
        else:
            with transaction.atomic():
                Request.objects.select_for_update().get(pk=req.pk)
                publication, created = TelegramRequestPublication.objects.get_or_create(request=req,
                    defaults={'text': form.cleaned_data['text'], 'reviewed': True, 'created_by': request.user})
                if publication.status != 'draft':
                    return redirect('admin:core_telegramrequestpublication_change', publication.pk)
                if not created:
                    publication.text = form.cleaned_data['text']
                    publication.reviewed = True
                    publication.save(update_fields=['text', 'reviewed', 'updated_at'])
                modeladmin.log_change(request, req, 'Подготовлен проверенный черновик Telegram; отправка не выполнялась')
            return redirect('admin:core_telegramrequestpublication_change', publication.pk)
    return render(request, 'admin/core/request/telegram_prepare.html', {
        **modeladmin.admin_site.each_context(request), 'title': 'Подготовить для Telegram', 'form': form,
        'opts': modeladmin.model._meta, 'original': req, 'public_consent': bool(req.public_consent_at)})


from core.models import RequestConsentEvent

@admin.register(RequestConsentEvent)
class ConsentEventAdmin(admin.ModelAdmin):
    list_display = ('request', 'purpose', 'granted', 'source', 'text_version', 'created_at')
    readonly_fields = tuple(field.name for field in RequestConsentEvent._meta.fields)
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False
