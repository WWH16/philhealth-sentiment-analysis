from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path('', views.index, name='feedback-index'),
    # Old address of the form; keeps printed links and bookmarks working.
    path('feedback/', RedirectView.as_view(pattern_name='feedback-index')),
    path('feedback/submit/', views.submit_feedback, name='feedback-submit'),
]
