import csv
from functools import wraps
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator

from .analysis import evaluate
from .forms import PreferencesForm, UploadForm, DraftForm
from .importing import import_offers, import_demand
from .models import Membership, Supplier, Offer, Selection, Preferences, Draft, Activity


def access(*roles):
    def decorate(view):
        @login_required(login_url='procurement:login')
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            membership = Membership.objects.filter(user=request.user, active=True).first()
            request.procurement_role = 'admin' if request.user.is_superuser else membership.role if membership else None
            if request.procurement_role is None or (roles and request.procurement_role not in roles):
                raise PermissionDenied('Нет доступа к кабинету закупок.')
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def context(request, **extra):
    return {'role': request.procurement_role, 'can_manage': request.procurement_role in ('sales', 'admin'),
            'basket_count': Selection.objects.filter(user=request.user).count(), **extra}


@access()
def dashboard(request):
    preferences, _ = Preferences.objects.get_or_create(user=request.user)
    query = Offer.objects.select_related('supplier').all()
    term = request.GET.get('q', '').strip()[:200]
    if term:
        query = query.filter(Q(name__icontains=term) | Q(article__icontains=term) | Q(brand__icontains=term) | Q(applicability__icontains=term))
    supplier = request.GET.get('supplier', '')
    if supplier.isdigit():
        query = query.filter(supplier_id=int(supplier))
    rows = [{'offer': offer, **evaluate(offer, preferences)} for offer in query]
    mode = request.GET.get('mode', 'difference')
    if mode == 'best':
        rows = [r for r in rows if r['qualified']]
        rows.sort(key=lambda r: r['profit'], reverse=True)
    else:
        rows.sort(key=lambda r: (r['difference'] is not None, r['difference'] or Decimal(0)), reverse=True)
    page = Paginator(rows, 40).get_page(request.GET.get('page'))
    return render(request, 'procurement/dashboard.html', context(request, page=page, total=len(rows), suppliers=Supplier.objects.all(), preferences=preferences, q=term, mode=mode, selected_supplier=supplier))


@access()
def detail(request, pk):
    offer = get_object_or_404(Offer.objects.select_related('supplier'), pk=pk)
    preferences, _ = Preferences.objects.get_or_create(user=request.user)
    alternatives = Offer.objects.filter(brand__iexact=offer.brand, article__iexact=offer.article).select_related('supplier').order_by('cost')
    return render(request, 'procurement/detail.html', context(request, offer=offer, analysis=evaluate(offer, preferences), alternatives=alternatives))


@access()
def settings(request):
    preferences, _ = Preferences.objects.get_or_create(user=request.user)
    form = PreferencesForm(request.POST or None, instance=preferences)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Параметры расчёта сохранены.')
        return redirect('procurement:dashboard')
    return render(request, 'procurement/form.html', context(request, form=form, title='Алгоритм и настройки', help='Прибыль = Kaspi − закупка − комиссия − все прочие расходы на единицу. Для отбора нужны проверенное совпадение, цены и сведения о спросе не старше 7 дней, положительные продажи за 30 дней или найденные отзывы за 90 дней. Отзывы не равны продажам. Пороги задаются ниже. Неизвестные расходы не считаются нулевыми.'))


@access('sales', 'admin')
def upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        if form.cleaned_data['checked_on'] > timezone.localdate():
            form.add_error('checked_on', 'Дата не может быть в будущем.')
        else:
            try:
                handler = import_demand if form.cleaned_data['kind'] == 'demand' else import_offers
                count = handler(form.cleaned_data['file'], form.cleaned_data['supplier'], form.cleaned_data['checked_on'], request.user)
            except Exception as error:
                # Import transaction rolls back completely; never expose file contents or credentials.
                form.add_error(None, str(error) if isinstance(error, ValueError) else 'Файл не прочитан. Проверьте формат и структуру.')
            else:
                messages.success(request, f'Загружено товаров: {count}.')
                return redirect('procurement:dashboard')
    return render(request, 'procurement/form.html', context(request, form=form, title='Загрузка данных', help='Прайс: article, brand, name, cost; необязательно category, stock, min_order, kaspi_price, kaspi_url, supplier_url. Поддерживается исходный Excel Omega. Отчёт спроса: article, brand, sales_30, review_count, recent_reviews, last_review (YYYY-MM-DD). Неизвестные значения оставьте пустыми. Для sales_30 обязательны period_start и period_end: ровно 30 календарных дней включительно до указанной даты. Отчёт спроса должен быть приведён к этим колонкам; автоматическое чтение любого формата Kaspi пока не поддерживается. Импорт атомарный.'))


@access()
@require_POST
def select(request, pk):
    offer = get_object_or_404(Offer, pk=pk)
    try:
        quantity = int(request.POST.get('quantity', offer.min_order))
    except (ValueError, TypeError):
        return HttpResponse('Количество должно быть целым.', status=400)
    if quantity < offer.min_order or quantity > 100000:
        return HttpResponse('Количество вне допустимого диапазона.', status=400)
    Selection.objects.update_or_create(user=request.user, offer=offer, defaults={'quantity': quantity})
    return redirect('procurement:basket')


@access()
@require_POST
def remove(request, pk):
    Selection.objects.filter(pk=pk, user=request.user).delete()
    return redirect('procurement:basket')


@access()
def basket(request):
    rows = list(Selection.objects.filter(user=request.user).select_related('offer__supplier'))
    for row in rows:
        row.subtotal = row.quantity * row.offer.cost
    return render(request, 'procurement/basket.html', context(request, selections=rows, budget=sum((r.subtotal for r in rows), Decimal(0))))


def safe_csv(value):
    text = str(value if value is not None else '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


@access()
def export(request):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="procurement.csv"'
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['Поставщик', 'Артикул', 'Бренд', 'Название', 'Количество', 'Закупка', 'Бюджет', 'Kaspi'])
    for row in Selection.objects.filter(user=request.user).select_related('offer__supplier'):
        writer.writerow([safe_csv(v) for v in [row.offer.supplier.name, row.offer.article, row.offer.brand, row.offer.name, row.quantity, row.offer.cost, row.quantity * row.offer.cost, row.offer.kaspi_url]])
    return response


@access('sales', 'admin')
def draft(request, pk):
    offer = get_object_or_404(Offer, pk=pk)
    existing = Draft.objects.filter(offer=offer).first()
    form = DraftForm(request.POST or None, instance=existing, initial={'title': offer.name, 'description': offer.description, 'photo_url': offer.photo_url, 'category': offer.category, 'characteristics': offer.characteristics})
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.offer = offer
        item.updated_by = request.user
        item.save()
        Activity.objects.create(user=request.user, action='draft_saved', details={'offer': offer.pk})
        messages.success(request, 'Черновик сохранён. В Kaspi ничего не отправлено.')
        return redirect('procurement:detail', pk=pk)
    return render(request, 'procurement/form.html', context(request, form=form, title='Подготовка карточки Kaspi', help='Сохранение создаёт черновик. Публикация и проверка обязательных полей категории будут доступны после настройки интеграции. Не добавляйте неподтверждённые характеристики.'))


@access('sales', 'admin')
@require_POST
def verify(request, pk):
    offer = get_object_or_404(Offer, pk=pk)
    if request.POST.get('confirmed') != 'yes' or not offer.kaspi_url:
        return HttpResponse('Подтвердите бренд, артикул, комплектность и применимость карточки.', status=400)
    offer.match_verified = True
    offer.save(update_fields=['match_verified'])
    Activity.objects.create(user=request.user, action='match_verified', details={'offer': offer.pk})
    return redirect('procurement:detail', pk=pk)


@access('admin')
def users(request):
    if request.method == 'POST':
        user = get_object_or_404(get_user_model(), pk=request.POST.get('user_id') if request.POST.get('user_id', '').isdigit() else 0)
        role = request.POST.get('role')
        if user == request.user or user.is_superuser or role not in [*Membership.Role.values, 'disabled']:
            return HttpResponse('Недопустимое изменение роли.', status=400)
        defaults = {'active': False} if role == 'disabled' else {'role': role, 'active': True}
        membership, _ = Membership.objects.update_or_create(user=user, defaults=defaults)
        Activity.objects.create(user=request.user, action='role_changed', details={'user_id': user.pk, 'role': role})
        messages.success(request, 'Роль сохранена. Глобальные права пользователя не изменены.')
        return redirect('procurement:users')
    return render(request, 'procurement/users.html', context(request, members=Membership.objects.select_related('user'), accounts=get_user_model().objects.filter(is_active=True, is_superuser=False).exclude(pk=request.user.pk).order_by('username'), roles=[*Membership.Role.choices, ('disabled', 'Отключить доступ к закупкам')]))
