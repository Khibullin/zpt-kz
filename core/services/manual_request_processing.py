"""Temporary owner-operated processing; customer intake stays available."""
from django.conf import settings

PARTS_ACCEPTED_MESSAGE = 'Ваша заявка принята! Мы передадим её подходящим продавцам. Если понадобятся уточнения, свяжемся с вами.'
SERVICE_ACCEPTED_MESSAGE = 'Ваша заявка принята! Мы передадим её подходящим исполнителям. Если понадобятся уточнения, свяжемся с вами.'


def manual_processing_enabled():
    return bool(getattr(settings, 'MANUAL_REQUEST_PROCESSING', False))
