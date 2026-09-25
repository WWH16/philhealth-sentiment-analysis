from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='feedback-index'),
    path('submit/', views.submit_feedback, name='feedback-submit'),
]