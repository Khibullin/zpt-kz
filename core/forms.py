from django import forms

from .models import Feedback


class FeedbackForm(forms.ModelForm):
    class Meta:
        model = Feedback
        fields = ['name', 'phone', 'message']
        labels = {
            'name': 'Ваше имя',
            'phone': 'Телефон / WhatsApp',
            'message': 'Сообщение',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'Как к вам обращаться',
                'autocomplete': 'name',
            }),
            'phone': forms.TextInput(attrs={
                'placeholder': 'Например: 7771234567',
                'autocomplete': 'tel',
            }),
            'message': forms.Textarea(attrs={
                'placeholder': 'Опишите вопрос или предложение',
                'rows': 5,
            }),
        }
