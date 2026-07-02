from django.db.models import Q, Prefetch
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.mail import send_mail
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.urls import reverse
from urllib.parse import quote, urlencode

from core.forms import FeedbackForm
from .forms import SellerRegisterForm, SellerProfileForm, ProductForm
from .models import (
    Product,
    ProductImage,
    Country,
    Brand,
    CarModel,
    Category,
    SellerProfile,
)

FEEDBACK_NOTIFY_EMAIL = 'rkhaibullin@gmail.com'

POPULAR_SEO_BRANDS = [
    'Toyota', 'Lexus', 'Hyundai', 'Kia', 'Chevrolet', 'Nissan',
    'Mercedes-Benz', 'BMW', 'Changan', 'Geely', 'Haval',
]
POPULAR_SEO_CATEGORIES = [
    ('Ходовая часть', 'Ходовая часть'),
    ('фильтры', 'Фильтры'),
    ('амортизаторы', 'Амортизаторы'),
    ('двигатель', 'Двигатель'),
    ('кузовные детали', 'Кузовные детали'),
    ('электрика', 'Электрика'),
    ('тормозная система', 'Тормозная система'),
    ('трансмиссия', 'Трансмиссия'),
]
POPULAR_SEO_CITIES = [
    'Алматы', 'Астана', 'Шымкент', 'Караганда', 'Актобе', 'Павлодар',
    'Усть-Каменогорск',
]


def _build_home_seo_links():
    catalog_url = reverse('catalog_list')

    brand_links = []
    for name in POPULAR_SEO_BRANDS:
        brand = Brand.objects.filter(name__iexact=name).first()
        if brand:
            url = f'{catalog_url}?brand={brand.pk}'
        else:
            url = f'{catalog_url}?q={quote(name)}'
        brand_links.append({'label': name, 'url': url})

    category_links = []
    for label, lookup in POPULAR_SEO_CATEGORIES:
        category = Category.objects.filter(name__icontains=lookup).first()
        if category:
            url = f'{catalog_url}?category={category.pk}'
        else:
            url = f'{catalog_url}?q={quote(lookup)}'
        category_links.append({'label': label, 'url': url})

    city_links = []
    for name in POPULAR_SEO_CITIES:
        city_links.append({
            'label': name,
            'url': f'{catalog_url}?city={quote(name)}',
        })

    return {
        'seo_brand_links': brand_links,
        'seo_category_links': category_links,
        'seo_city_links': city_links,
    }


def _send_feedback_notification(feedback):
    try:
        send_mail(
            subject='Новая заявка с сайта ZPT.KZ',
            message=(
                f'Имя: {feedback.name}\n'
                f'Телефон / WhatsApp: {feedback.phone}\n\n'
                f'Сообщение:\n{feedback.message}'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[FEEDBACK_NOTIFY_EMAIL],
            fail_silently=False,
        )
    except Exception as e:
        print("ПОЧТА ОШИБКА:", str(e))


def _parse_filter_id(value):
    value = (value or '').strip()
    return int(value) if value.isdigit() else None


def _seller_filter_query(**params):
    clean = {}
    for key, value in params.items():
        if value not in (None, '', False):
            clean[key] = value
    return urlencode(clean)


def _apply_seller_warehouse_filters(
    base_products,
    *,
    q='',
    category_id=None,
    brand_id=None,
    model_id=None,
):
    if brand_id and model_id:
        if not CarModel.objects.filter(pk=model_id, brand_id=brand_id).exists():
            model_id = None

    if brand_id:
        valid_brand_ids = base_products.exclude(
            brand__isnull=True
        ).values_list('brand_id', flat=True)
        if brand_id not in set(valid_brand_ids):
            brand_id = None
            model_id = None

    if model_id:
        valid_model_ids = base_products.exclude(
            car_model__isnull=True
        ).values_list('car_model_id', flat=True)
        if model_id not in set(valid_model_ids):
            model_id = None

    products = base_products

    if q:
        products = products.filter(
            Q(title__icontains=q) |
            Q(article__icontains=q)
        )

    if category_id:
        products = products.filter(category_id=category_id)

    if brand_id:
        products = products.filter(brand_id=brand_id)

    if model_id:
        products = products.filter(car_model_id=model_id)

    brand_scope = base_products
    if category_id:
        brand_scope = brand_scope.filter(category_id=category_id)
    if model_id:
        brand_scope = brand_scope.filter(car_model_id=model_id)

    seller_brands = Brand.objects.filter(
        pk__in=brand_scope.exclude(
            brand__isnull=True
        ).values_list('brand_id', flat=True)
    ).order_by('name')

    model_scope = base_products
    if category_id:
        model_scope = model_scope.filter(category_id=category_id)
    if brand_id:
        model_scope = model_scope.filter(brand_id=brand_id)

    if brand_id:
        seller_models = CarModel.objects.filter(
            brand_id=brand_id,
            pk__in=model_scope.exclude(
                car_model__isnull=True
            ).values_list('car_model_id', flat=True),
        ).order_by('name')
    else:
        seller_models = CarModel.objects.none()

    category_scope = base_products
    if brand_id:
        category_scope = category_scope.filter(brand_id=brand_id)
    if model_id:
        category_scope = category_scope.filter(car_model_id=model_id)

    seller_categories = Category.objects.filter(
        pk__in=category_scope.exclude(
            category__isnull=True
        ).values_list('category_id', flat=True)
    ).order_by('name')

    return {
        'products': products,
        'category_id': category_id,
        'brand_id': brand_id,
        'model_id': model_id,
        'seller_brands': seller_brands,
        'seller_models': seller_models,
        'seller_categories': seller_categories,
    }


def attach_sellers_to_products(products):
    products = list(products)
    if not products:
        return products

    profiles = list(SellerProfile.objects.all())
    by_name = {profile.name.lower(): profile for profile in profiles}
    by_phone_suffix = {}

    for profile in profiles:
        digits = ''.join(filter(str.isdigit, profile.phone))
        if len(digits) >= 10:
            by_phone_suffix[digits[-10:]] = profile

    for product in products:
        seller = None

        if product.whatsapp_number:
            digits = ''.join(filter(str.isdigit, product.whatsapp_number))
            if len(digits) >= 10:
                seller = by_phone_suffix.get(digits[-10:])

        if not seller and product.seller_name:
            seller = by_name.get(product.seller_name.strip().lower())

        product.seller = seller

    return products


@ensure_csrf_cookie
def catalog_list(request):
    query = request.GET.get('q', '').strip()
    country_id = request.GET.get('country', '').strip()
    brand_id = request.GET.get('brand', '').strip()
    model_id = request.GET.get('model', '').strip()
    category_id = request.GET.get('category', '').strip()
    city = request.GET.get('city', '').strip()

    countries = Country.objects.all().order_by('name')
    categories = Category.objects.all().order_by('name')

    brands = Brand.objects.select_related('country').all().order_by('name')
    models = CarModel.objects.select_related('brand', 'brand__country').all().order_by('name')

    if country_id:
        brands = brands.filter(country_id=country_id)

        if brand_id and not brands.filter(id=brand_id).exists():
            brand_id = ''
            model_id = ''

    if brand_id:
        models = models.filter(brand_id=brand_id)

        if model_id and not models.filter(id=model_id).exists():
            model_id = ''
    else:
        if country_id:
            models = models.none()

    products = Product.objects.filter(status='active').select_related(
        'brand',
        'brand__country',
        'car_model',
        'category',
    )

    if query:
        products = products.filter(
            Q(title__icontains=query) |
            Q(article__icontains=query) |
            Q(description__icontains=query) |
            Q(compatibility__icontains=query)
        )

    if country_id:
        products = products.filter(brand__country_id=country_id)

    if brand_id:
        products = products.filter(brand_id=brand_id)

    if model_id:
        products = products.filter(car_model_id=model_id)

    if category_id:
        products = products.filter(category_id=category_id)

    if city:
        products = products.filter(city__icontains=city)

    has_filters = any([query, country_id, brand_id, model_id, category_id, city])
    show_all = request.GET.get('all') == '1'

    if has_filters or show_all:
        products = products.order_by('-created_at')
    else:
        products = products.order_by('?')[:12]

    products = attach_sellers_to_products(products)

    context = {
        'products': products,
        'has_filters': has_filters,
        'show_all': show_all,
        'countries': countries,
        'brands': brands,
        'models': models,
        'categories': categories,
        'query': query,
        'selected_country': country_id,
        'selected_brand': brand_id,
        'selected_model': model_id,
        'selected_category': category_id,
        'selected_city': city,
        **_build_home_seo_links(),
    }
    return render(request, 'catalog/catalog_list.html', context)


@ensure_csrf_cookie
def product_detail(request, slug=None, pk=None):

    if slug:
        product = get_object_or_404(
            Product,
            slug=slug,
            status='active'
        )

    else:
        product = get_object_or_404(
            Product,
            pk=pk,
            status='active'
        )

        if product.slug:
            return redirect(
                'product_detail',
                slug=product.slug
            )

    seller = None

    if product.whatsapp_number:
        clean_phone = ''.join(
            filter(str.isdigit, product.whatsapp_number)
        )

        seller = SellerProfile.objects.filter(
            phone__icontains=clean_phone[-10:]
        ).first()

    if not seller and product.seller_name:
        seller = SellerProfile.objects.filter(
            name__iexact=product.seller_name.strip()
        ).first()

    seller_products = Product.objects.filter(
        seller_name=product.seller_name,
        status='active'
    ).exclude(pk=product.pk).select_related(
        'brand',
        'car_model',
    ).prefetch_related(
        Prefetch(
            'selected_models',
            queryset=CarModel.objects.select_related('brand'),
        ),
    )[:8]

    return render(request, 'catalog/product_detail.html', {
        'product': product,
        'seller': seller,
        'seller_products': seller_products,
    })


def seller_register(request):
    error_message = None

    if request.method == 'POST':
        form = SellerRegisterForm(request.POST, request.FILES)
        if form.is_valid():
            phone = ''.join(filter(str.isdigit, form.cleaned_data['phone']))
            username = phone
            password = form.cleaned_data['password']

            if User.objects.filter(username=username).exists():
                error_message = 'Пользователь с таким WhatsApp уже существует.'
            else:
                user = User.objects.create_user(
                    username=username,
                    password=password
                )

                SellerProfile.objects.create(
                    user=user,
                    name=form.cleaned_data['name'],
                    phone=phone,
                    city=form.cleaned_data.get('city', ''),
                    address=form.cleaned_data.get('address', ''),
                    work_hours=form.cleaned_data.get('work_hours', ''),
                    delivery_info=form.cleaned_data.get('delivery_info', ''),
                    instagram=form.cleaned_data.get('instagram', ''),
                    website=form.cleaned_data.get('website', ''),
                    description=form.cleaned_data.get('description', ''),
                    logo=form.cleaned_data.get('logo')
                )

                return redirect('seller_login')
    else:
        form = SellerRegisterForm()

    return render(request, 'catalog/seller_register.html', {
        'form': form,
        'error_message': error_message,
    })


def seller_login(request):
    if request.user.is_authenticated:
        return redirect('/cabinet/select/')

    error_message = None
    username = ''
    remember_me = False

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        remember_me = bool(request.POST.get('remember_me'))

        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            if remember_me:
                request.session.set_expiry(1209600)
            else:
                request.session.set_expiry(0)
            return redirect('/cabinet/select/')
        else:
            error_message = 'Неверный логин или пароль.'

    return render(request, 'catalog/seller_login.html', {
        'error_message': error_message,
        'username': username,
        'remember_me': remember_me,
    })


def seller_logout(request):
    logout(request)
    return redirect('catalog_list')


@login_required
def seller_dashboard(request):
    seller = get_object_or_404(SellerProfile, user=request.user)
    base_products = Product.objects.filter(seller_name=seller.name)

    query = request.GET.get('q_dashboard', '').strip()
    status_filter = request.GET.get('status_filter', '').strip()
    category_id = _parse_filter_id(request.GET.get('category'))
    brand_id = _parse_filter_id(request.GET.get('brand'))
    model_id = _parse_filter_id(request.GET.get('model'))

    products = base_products

    if status_filter == 'active':
        products = products.filter(status='active')
    elif status_filter == 'hidden':
        products = products.filter(status='hidden')

    warehouse = _apply_seller_warehouse_filters(
        products,
        q=query,
        category_id=category_id,
        brand_id=brand_id,
        model_id=model_id,
    )

    products = warehouse['products'].select_related(
        'brand',
        'car_model',
        'category',
    ).order_by('-created_at')

    filter_qs = _seller_filter_query(
        q_dashboard=query,
        status_filter=status_filter,
        brand=warehouse['brand_id'],
        model=warehouse['model_id'],
    )

    return render(request, 'catalog/seller_dashboard.html', {
        'seller': seller,
        'products': products,
        'query': query,
        'status_filter': status_filter,
        'category_id': warehouse['category_id'],
        'brand_id': warehouse['brand_id'],
        'model_id': warehouse['model_id'],
        'seller_brands': warehouse['seller_brands'],
        'seller_models': warehouse['seller_models'],
        'seller_categories': warehouse['seller_categories'],
        'filter_qs': filter_qs,
        'has_any_products': Product.objects.filter(seller_name=seller.name).exists(),
        'has_active_filters': any([
            query,
            status_filter,
            warehouse['category_id'],
            warehouse['brand_id'],
            warehouse['model_id'],
        ]),
    })


@login_required
def seller_profile(request):
    seller = get_object_or_404(SellerProfile, user=request.user)
    products_count = Product.objects.filter(seller_name=seller.name).count()

    return render(request, 'catalog/seller_profile.html', {
        'seller': seller,
        'products_count': products_count,
    })


@login_required
def seller_change_password(request):
    seller = get_object_or_404(SellerProfile, user=request.user)
    error_message = None
    success_message = None

    if request.method == 'POST':
        current_password = request.POST.get('current_password', '').strip()
        new_password = request.POST.get('new_password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()

        if not request.user.check_password(current_password):
            error_message = 'Текущий пароль указан неверно.'

        elif not new_password:
            error_message = 'Введите новый пароль.'

        elif new_password != confirm_password:
            error_message = 'Новый пароль и подтверждение не совпадают.'

        else:
            request.user.set_password(new_password)
            request.user.save()
            update_session_auth_hash(request, request.user)
            success_message = 'Пароль успешно изменён.'

    return render(request, 'catalog/seller_change_password.html', {
        'seller': seller,
        'error_message': error_message,
        'success_message': success_message,
    })


@login_required
def seller_profile_edit(request):
    seller = get_object_or_404(SellerProfile, user=request.user)
    old_name = seller.name

    if request.method == 'POST':
        form = SellerProfileForm(request.POST, request.FILES, instance=seller)
        if form.is_valid():
            updated_seller = form.save()

            if old_name != updated_seller.name:
                Product.objects.filter(seller_name=old_name).update(
                    seller_name=updated_seller.name,
                    whatsapp_number=updated_seller.phone,
                    city=updated_seller.city
                )
            else:
                Product.objects.filter(seller_name=updated_seller.name).update(
                    whatsapp_number=updated_seller.phone,
                    city=updated_seller.city
                )

            return redirect('seller_profile')
    else:
        form = SellerProfileForm(instance=seller)

    return render(request, 'catalog/seller_profile_edit.html', {
        'seller': seller,
        'form': form,
    })


@login_required
def seller_profile_delete(request):
    seller = get_object_or_404(SellerProfile, user=request.user)

    if request.method == 'POST':
        user = request.user
        Product.objects.filter(seller_name=seller.name).delete()
        seller.delete()
        user.delete()
        logout(request)
        return redirect('catalog_list')

    return render(request, 'catalog/seller_profile_delete.html', {
        'seller': seller,
    })


@login_required
def add_product(request):
    seller = get_object_or_404(SellerProfile, user=request.user)

    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES)
        files = request.FILES.getlist('extra_images')

        if form.is_valid():
            product = form.save(commit=False)

            product.seller_name = seller.name
            product.whatsapp_number = seller.phone
            product.city = seller.city

            product.save()

            uploaded_images = []

            for f in files[:4]:
                img = ProductImage.objects.create(
                    product=product,
                    image=f
                )
                uploaded_images.append(img)

            if not product.main_image and uploaded_images:
                product.main_image = uploaded_images[0].image
                product.save(update_fields=['main_image'])

            return redirect('seller_dashboard')
    else:
        form = ProductForm()

    return render(request, 'catalog/add_product.html', {
        'form': form,
        'seller': seller,
        'page_title': 'Добавить товар',
        'submit_text': 'Сохранить товар',
    })


@login_required
def edit_product(request, pk):
    seller = get_object_or_404(SellerProfile, user=request.user)
    product = get_object_or_404(Product, pk=pk, seller_name=seller.name)

    initial = {}
    if product.brand:
        initial['country'] = product.brand.country_id

    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product, initial=initial)
        files = request.FILES.getlist('extra_images')

        if form.is_valid():
            updated_product = form.save(commit=False)

            if request.POST.get('remove_main_image'):
                if product.main_image:
                    product.main_image.delete(save=False)

                updated_product.main_image = None

            if request.POST.get('remove_extra_images'):
                for img in product.images.all():
                    img.image.delete(save=False)
                    img.delete()

            updated_product.seller_name = seller.name
            updated_product.whatsapp_number = seller.phone
            updated_product.city = seller.city

            updated_product.save()

            uploaded_images = []

            if files:
                for img in product.images.all():
                    img.image.delete(save=False)
                    img.delete()

                for f in files[:4]:
                    img = ProductImage.objects.create(
                        product=updated_product,
                        image=f
                    )
                    uploaded_images.append(img)

            if not updated_product.main_image:
                first_image = updated_product.images.first()

                if first_image:
                    updated_product.main_image = first_image.image
                    updated_product.save(update_fields=['main_image'])

            return redirect('seller_dashboard')
    else:
        form = ProductForm(instance=product, initial=initial)

    return render(request, 'catalog/add_product.html', {
        'form': form,
        'seller': seller,
        'product': product,
        'page_title': 'Редактировать товар',
        'submit_text': 'Сохранить изменения',
    })


@login_required
def delete_product(request, pk):
    seller = get_object_or_404(SellerProfile, user=request.user)
    product = get_object_or_404(Product, pk=pk, seller_name=seller.name)

    if request.method == 'POST':
        product.delete()
        return redirect('seller_dashboard')

    return render(request, 'catalog/delete_product.html', {
        'seller': seller,
        'product': product,
    })


def load_brands(request):
    country_id = request.GET.get('country')
    brands = Brand.objects.filter(
        country_id=country_id
    ).order_by('name')

    data = [
        {'id': b.id, 'name': b.name}
        for b in brands
    ]

    return JsonResponse(data, safe=False)


def load_models(request):
    brand_id = request.GET.get('brand')

    models = CarModel.objects.filter(
        brand_id=brand_id
    ).order_by('name')

    data = [
        {'id': m.id, 'name': m.name}
        for m in models
    ]

    return JsonResponse(data, safe=False)


def load_compatible_models(request):
    brand_id = request.GET.get('brand')

    models = CarModel.objects.filter(
        brand_id=brand_id
    ).order_by('name')

    data = [
        {
            'id': m.id,
            'name': m.name
        }
        for m in models
    ]

    return JsonResponse(data, safe=False)


def public_seller_profile(request, slug):
    seller = get_object_or_404(
        SellerProfile,
        slug=slug
    )

    base_products = Product.objects.filter(
        seller_name=seller.name,
        status='active'
    )
    products_count = base_products.count()
    platform_year = seller.user.date_joined.year

    q_seller = request.GET.get('q_seller', '').strip()
    category_id = _parse_filter_id(request.GET.get('category'))
    brand_id = _parse_filter_id(request.GET.get('brand'))
    model_id = _parse_filter_id(request.GET.get('model'))
    page_number = request.GET.get('page', '1')

    warehouse = _apply_seller_warehouse_filters(
        base_products,
        q=q_seller,
        category_id=category_id,
        brand_id=brand_id,
        model_id=model_id,
    )

    products = warehouse['products'].select_related(
        'category',
        'brand',
        'car_model',
    ).prefetch_related(
        Prefetch(
            'selected_models',
            queryset=CarModel.objects.select_related('brand'),
        ),
    ).order_by('-created_at')

    category_id = warehouse['category_id']
    brand_id = warehouse['brand_id']
    model_id = warehouse['model_id']

    paginator = Paginator(products, 12)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    filter_qs = _seller_filter_query(
        q_seller=q_seller,
        brand=brand_id,
        model=model_id,
    )

    return render(
        request,
        'catalog/public_seller_profile.html',
        {
            'seller': seller,
            'page_obj': page_obj,
            'products_count': products_count,
            'filtered_count': paginator.count,
            'platform_year': platform_year,
            'q_seller': q_seller,
            'category_id': category_id,
            'brand_id': brand_id,
            'model_id': model_id,
            'seller_categories': warehouse['seller_categories'],
            'seller_brands': warehouse['seller_brands'],
            'seller_models': warehouse['seller_models'],
            'filter_qs': filter_qs,
            'has_active_filters': any([
                q_seller,
                category_id,
                brand_id,
                model_id,
            ]),
        }
    )


def faq_view(request):
    return render(request, 'catalog/faq.html')


def feedback_view(request):
    form = FeedbackForm()
    success = False

    if request.method == 'POST':
        form = FeedbackForm(request.POST)
        if form.is_valid():
            feedback = form.save()
            _send_feedback_notification(feedback)
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': True,
                    'message': 'Спасибо! Мы получили ваше сообщение и скоро свяжемся с вами.',
                })
            success = True
            form = FeedbackForm()
        elif request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': False,
                'message': 'Проверьте правильность заполнения полей.',
                'errors': form.errors,
            }, status=400)

    return render(request, 'catalog/feedback.html', {
        'form': form,
        'success': success,
    })