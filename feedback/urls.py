from django.urls import path
from . import views

urlpatterns = [
    path('', views.landing, name='landing'),
    path('feedback/', views.index, name='feedback-index'),
    path('feedback/submit/', views.submit_feedback, name='feedback-submit'),
]