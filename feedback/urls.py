from django.urls import path
from . import views

urlpatterns = [
    path('', views.page, {'template': 'feedback/landing.html'}, name='landing'),
    path('feedback/', views.page, {'template': 'feedback/index.html'}, name='feedback-index'),
    path('feedback/submit/', views.submit_feedback, name='feedback-submit'),
]
