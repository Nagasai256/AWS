from django.urls import path, reverse_lazy
from django.contrib.auth import views as auth_views
from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # Forgot password
    path("password-reset/", auth_views.PasswordResetView.as_view(
        template_name="dashboard/password_reset_form.html",
        email_template_name="dashboard/password_reset_email.html",
        success_url=reverse_lazy("dashboard:password_reset_done"),
        extra_context={"title": "Forgot Password"},
    ), name="password_reset"),
    path("password-reset/done/", auth_views.PasswordResetDoneView.as_view(
        template_name="dashboard/password_reset_done.html",
    ), name="password_reset_done"),
    path("password-reset-confirm/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="dashboard/password_reset_confirm.html",
        success_url=reverse_lazy("dashboard:password_reset_complete"),
        post_reset_login=False,
    ), name="password_reset_confirm"),
    path("password-reset-complete/", auth_views.PasswordResetCompleteView.as_view(
        template_name="dashboard/password_reset_complete.html",
    ), name="password_reset_complete"),
    # Patient
    path("sessions/", views.patient_sessions, name="patient_sessions"),
    path("sessions/<int:session_id>/", views.patient_session_detail, name="patient_session_detail"),
    path("upload/", views.upload_csv, name="upload_csv"),
    path("download/sample/", views.download_sample_csv, name="download_sample_csv"),
    # Clinician
    path("clinician/patients/", views.clinician_patients, name="clinician_patients"),
    path("clinician/patients/<int:patient_id>/sessions/", views.clinician_patient_sessions, name="clinician_patient_sessions"),
    path("clinician/sessions/<int:session_id>/", views.clinician_session_detail, name="clinician_session_detail"),
    path("clinician/upload/<int:patient_id>/", views.upload_csv, name="clinician_upload"),
    # Admin
    path("admin-panel/", views.admin_dashboard, name="admin_dashboard"),
    path("admin-panel/create-user/", views.admin_create_user, name="admin_create_user"),
    path("admin-panel/groups/", views.admin_manage_groups, name="admin_manage_groups"),
    # API-style
    path("api/frame/<int:frame_id>/", views.frame_data, name="frame_data"),
    path("download/frame/<int:frame_id>/csv/", views.download_frame_csv, name="download_frame_csv"),
    path("download/session/<int:session_id>/csv/", views.download_session_csv, name="download_session_csv"),
    path("api/session/<int:session_id>/chart/", views.metrics_chart_data, name="metrics_chart_data"),
    path("api/frame/<int:frame_id>/explanation/", views.frame_explanation, name="frame_explanation"),
    path("api/frame/<int:frame_id>/comments/", views.comments_for_frame, name="comments_for_frame"),
    path("api/frame/<int:frame_id>/comment/", views.add_comment, name="add_comment"),
    path("api/comment/<int:comment_id>/reply/", views.add_reply, name="add_reply"),
    # Reports
    path("session/<int:session_id>/report/", views.report_view, name="report"),
]
