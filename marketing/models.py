from django.db import models


class MarketingCabinetPermission(models.Model):
    """Модель-носитель permission для внутреннего кабинета маркетинга."""

    class Meta:
        verbose_name = 'Доступ к кабинету маркетинга'
        verbose_name_plural = 'Доступ к кабинету маркетинга'
        permissions = [
            (
                'access_marketing_cabinet',
                'Доступ к кабинету рассылок и уведомлений',
            ),
        ]
