from django.urls import path
from .views import *

urlpatterns = [

    path(
        'result/<int:request_id>/',
        service_request_result,
        name='service_request_result'
    ),

]