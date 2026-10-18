from unittest.mock import patch
from django.test import TestCase
from core.models import Seller,Country,Brand,CarModel,PartCategory,SellerExtraPhone,Feedback
from service_requests.models import ServiceSeller

class DirectoryPublicDetailsTests(TestCase):
    def test_description_does_not_hide_specialization_and_flags_are_not_assortment(self):
        country=Country.objects.create(name='Private country')
        brand=Brand.objects.create(name='Toyota',country=country)
        model=CarModel.objects.create(name='Camry',brand=brand)
        seller=Seller.objects.create(name='Shop <test>',directory_parts=True,transport_type='car',
            directory_description='Описание компании',whatsapp='77010000001',
            all_brands=True,all_models=True,all_categories=True,receive_requests=False,
            directory_contact_label='Отдел запчастей',directory_entrance='Бутик 14')
        seller.selected_brands.add(brand);seller.selected_models.add(model)
        seller.selected_categories.add(PartCategory.objects.create(name='Оптика'))
        SellerExtraPhone.objects.create(seller=seller,phone='77010000002',label='Сервис')
        page=self.client.get(f'/parts-seller/{seller.pk}/')
        for value in ['Описание компании','Toyota','Camry','Оптика','Легковые автомобили','Отдел запчастей','Бутик 14','Сервис','Сообщить об ошибке','Это моя организация','Shop &lt;test&gt;']:
            self.assertContains(page,value)
        for value in ['Все марки','Все модели','Private country','Проверено']:
            self.assertNotContains(page,value)
        self.assertContains(self.client.get('/parts-sellers/'),'Toyota')
        seller.refresh_from_db();self.assertFalse(seller.receive_requests)

    def test_service_public_fields_optional_and_do_not_change_dispatch(self):
        seller=ServiceSeller.objects.create(name='Studio',seller_type='detailing',city='Алматы',whatsapp='77010000003',
            directory_brands='Hyundai, Genesis',directory_transport='Легковые автомобили',
            directory_powertrain='Электромобили, гибриды',directory_mobile_service=True,
            directory_entrance='Вход со двора',directory_contact_label='Администратор',receive_requests=False)
        page=self.client.get(f'/catalog/services/{seller.pk}/?directory=detailing')
        for value in ['Hyundai, Genesis','Электромобили, гибриды','Выездной ремонт','Вход со двора','Администратор','Это моя организация']:
            self.assertContains(page,value)
        seller.directory_mobile_service=None;seller.directory_powertrain='';seller.save()
        page=self.client.get(f'/catalog/services/{seller.pk}/?directory=detailing')
        self.assertNotContains(page,'Выездной ремонт');self.assertNotContains(page,'Электромобили, гибриды')
        seller.refresh_from_db();self.assertFalse(seller.receive_requests)

    @patch('catalog.views._send_feedback_notification')
    def test_existing_feedback_receives_context_without_granting_access(self,notify):
        message='Запрос на подтверждение организации: Shop\nhttps://zpt.kz/parts-seller/123/\nМоя связь: владелец'
        response=self.client.post('/feedback/',{'name':'Иван','phone':'+77010000001','message':message},HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code,200)
        self.assertEqual(Feedback.objects.get().message,message)
        self.assertEqual(Seller.objects.count(),0)
