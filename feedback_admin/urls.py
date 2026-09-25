from django.urls import path
from . import views

urlpatterns = [
    # ── Auth ──────────────────────────────────────────────────────────
    path('login/',  views.admin_login,  name='admin_login'),
    path('logout/', views.admin_logout, name='admin_logout'),

    # ── Dashboard & pages ─────────────────────────────────────────────
    path('',                            views.dashboard,          name='dashboard'),
    path('responses/<int:entry_id>/status/', views.response_status_update, name='response_status_update'),
    path('responses/<int:entry_id>/category/', views.response_category_update, name='response_category_update'),
    path('responses/<int:entry_id>/notes/add/', views.response_note_add, name='response_note_add'),
    path('sentiment_analysis/',         views.sentiment_analysis, name='sentiment_analysis'),
    path('reports/',                    views.reports,            name='reports'),
    path('reports/export/excel/',        views.export_report_excel, name='export_report_excel'),
    path('activity-log/',               views.activity_log,       name='activity_log'),

    # ── User management ───────────────────────────────────────────────
    path('users/',                       views.users,              name='users'),
    path('users/add/',                   views.user_add,           name='user_add'),
    path('users/<int:user_id>/edit/',    views.user_edit,          name='user_edit'),
    path('users/<int:user_id>/delete/',  views.user_delete,        name='user_delete'),
    path('users/<int:user_id>/toggle/',  views.user_toggle_active, name='user_toggle_active'),

    # ── Group management ──────────────────────────────────────────────
    path('groups/add/',                   views.group_add,    name='group_add'),
    path('groups/<int:group_id>/edit/',   views.group_edit,   name='group_edit'),
    path('groups/<int:group_id>/delete/', views.group_delete, name='group_delete'),

    # ── Management ──────────────────────────────────────────────
    path('responses/', views.responses, name='responses'),
    path('responses/count/', views.responses_count, name='responses_count'),
    path('responses/delete/', views.responses_delete, name='responses_delete'),
    path('settings/', views.settings_page, name='settings_page'),
    path('settings/survey/', views.update_survey_settings, name='update_survey_settings'),
    path('settings/sentiment/', views.update_sentiment_settings, name='update_sentiment_settings'),
    path('settings/notifications/', views.update_notification_settings, name='update_notification_settings'),
    path('settings/notifications/send-now/', views.send_summary_now, name='send_summary_now'),
    path('settings/notifications/test/', views.send_test_daily_summary, name='send_test_daily_summary'),
    path('cron/daily-summary/', views.cron_daily_summary, name='admin_cron_daily_summary'),

    # ── Sentiment trigger ──────────────────────────────────────────────
    path('settings/reanalyze/', views.reanalyze_sentiment_view, name='reanalyze_sentiment'),

    # ── Backup / restore ─────────────────────────────────────────────
    path('settings/backup/create/', views.backup_create, name='backup_create'),
    path('settings/backup/restore/', views.backup_restore, name='backup_restore'),
    path('settings/backup/<str:filename>/download/', views.backup_download, name='backup_download'),
    path('settings/backup/<str:filename>/delete/', views.backup_delete, name='backup_delete'),
]
