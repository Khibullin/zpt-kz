from django.test import Client, TestCase, override_settings


@override_settings(ALLOWED_HOSTS=['*'])
class B2BLoginPageSmokeTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST='zpt.kz')

    def test_request_parts_cabinet_login_page_renders(self):
        response = self.client.get('/request-parts/cabinet/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Вход продавца')
        self.assertContains(response, 'b2b-auth-card')
        self.assertContains(response, 'request-parts/register/')

    def test_seller_login_page_renders(self):
        response = self.client.get('/seller/login/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'b2b-auth-card')
        self.assertContains(response, 'seller/register/')

    def test_service_request_cabinet_login_page_renders(self):
        response = self.client.get('/service-request/cabinet/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Вход исполнителя')
        self.assertContains(response, 'b2b-auth-card')
        self.assertContains(response, 'service-request/register/')

    def test_b2b_auth_header_links(self):
        response = self.client.get('/seller/login/')
        self.assertContains(response, 'href="/"')
        self.assertContains(response, 'href="/business/"')

    def test_business_gateway_shows_login_and_register_for_each_direction(self):
        response = self.client.get('/business/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Уже зарегистрированы? Войдите в нужный кабинет. Новый пользователь? Зарегистрируйтесь.',
        )
        self.assertContains(response, 'Войти в кабинет')
        self.assertContains(response, 'Зарегистрироваться как продавец')
        self.assertContains(response, 'Войти в магазин')
        self.assertContains(response, 'Создать магазин')
        self.assertContains(response, 'Зарегистрироваться как СТО/сервис')
        self.assertContains(response, 'href="/request-parts/cabinet/"')
        self.assertContains(response, 'href="/request-parts/register/"')
        self.assertContains(response, 'href="/seller/login/"')
        self.assertContains(response, 'href="/seller/register/"')
        self.assertContains(response, 'href="/service-request/cabinet/"')
        self.assertContains(response, 'href="/service-request/register/"')
