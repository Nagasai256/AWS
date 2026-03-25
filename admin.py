from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth import get_user_model
from .models import (
    UserProfile,
    PatientGroup,
    ClinicianPatientAccess,
    PressureMapSession,
    PressureFrame,
    PressureMetric,
    PressureAlert,
    UserComment,
    ClinicianReply,
)

User = get_user_model()


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("user__username",)


@admin.register(PatientGroup)
class PatientGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    filter_horizontal = ("patients",)


@admin.register(ClinicianPatientAccess)
class ClinicianPatientAccessAdmin(admin.ModelAdmin):
    list_display = ("clinician", "group", "patient")
    list_filter = ("clinician",)


@admin.register(PressureMapSession)
class PressureMapSessionAdmin(admin.ModelAdmin):
    list_display = ("patient", "name", "started_at", "ended_at")
    list_filter = ("patient",)
    date_hierarchy = "started_at"


@admin.register(PressureFrame)
class PressureFrameAdmin(admin.ModelAdmin):
    list_display = ("session", "timestamp", "rows", "cols")
    list_filter = ("session",)
    date_hierarchy = "timestamp"


@admin.register(PressureMetric)
class PressureMetricAdmin(admin.ModelAdmin):
    list_display = ("frame", "peak_pressure_index", "contact_area_pct", "computed_at")


@admin.register(PressureAlert)
class PressureAlertAdmin(admin.ModelAdmin):
    list_display = ("frame", "severity", "flagged_for_review", "reviewed_by", "reviewed_at")
    list_filter = ("severity", "flagged_for_review")


@admin.register(UserComment)
class UserCommentAdmin(admin.ModelAdmin):
    list_display = ("frame", "author", "created_at")
    list_filter = ("author",)


@admin.register(ClinicianReply)
class ClinicianReplyAdmin(admin.ModelAdmin):
    list_display = ("comment", "author", "created_at")
