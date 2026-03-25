"""
Database models for pressure monitoring: user roles, time-ordered pressure data,
metrics, alerts, and user/clinician feedback.
"""
from django.db import models
from django.conf import settings
from django.utils import timezone


class UserProfile(models.Model):
    """Extended profile with role: patient, clinician, or admin."""
    class Role(models.TextChoices):
        PATIENT = "patient", "Patient"
        CLINICIAN = "clinician", "Clinician"
        ADMIN = "admin", "Admin"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.PATIENT,
    )
    birthdate = models.DateField(null=True, blank=True)
    email_updates = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "dashboard_userprofile"

    def __str__(self):
        return f"{self.user.get_username()} ({self.role})"

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    @property
    def is_clinician(self):
        return self.role == self.Role.CLINICIAN

    @property
    def is_patient(self):
        return self.role == self.Role.PATIENT


class PatientGroup(models.Model):
    """Group of patients that a clinician can be granted access to."""
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    patients = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="patient_groups",
        blank=True,
        limit_choices_to={"profile__role": UserProfile.Role.PATIENT},
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dashboard_patientgroup"

    def __str__(self):
        return self.name


class ClinicianPatientAccess(models.Model):
    """Links clinicians to groups or individual patients they can view."""
    clinician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="access_grants",
        limit_choices_to={"profile__role": UserProfile.Role.CLINICIAN},
    )
    group = models.ForeignKey(
        PatientGroup,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="clinician_access",
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="allowed_clinicians",
        limit_choices_to={"profile__role": UserProfile.Role.PATIENT},
    )

    class Meta:
        db_table = "dashboard_clinicianpatientaccess"
        constraints = [
            models.UniqueConstraint(
                fields=["clinician", "group"],
                condition=models.Q(group__isnull=False),
                name="unique_clinician_group",
            ),
            models.UniqueConstraint(
                fields=["clinician", "patient"],
                condition=models.Q(patient__isnull=False),
                name="unique_clinician_patient",
            ),
        ]

    def __str__(self):
        if self.group:
            return f"{self.clinician.get_username()} -> group {self.group.name}"
        return f"{self.clinician.get_username()} -> {self.patient.get_username()}"


class PressureMapSession(models.Model):
    """One recording session (e.g. one day or one CSV upload) for a patient."""
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pressure_sessions",
        limit_choices_to={"profile__role": UserProfile.Role.PATIENT},
    )
    name = models.CharField(max_length=200, blank=True)  # e.g. "Day 1", "Morning session"
    started_at = models.DateTimeField(db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dashboard_pressuremapsession"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.patient.get_username()} – {self.name or self.started_at}"


class PressureFrame(models.Model):
    """Single timestamped frame of pressure map data (grid stored as JSON)."""
    session = models.ForeignKey(
        PressureMapSession,
        on_delete=models.CASCADE,
        related_name="frames",
    )
    timestamp = models.DateTimeField(db_index=True)
    # Grid as list of lists: [[row0], [row1], ...], each cell is pressure value
    data_json = models.JSONField(default=list)
    # Optional: grid dimensions if not inferrable from data_json
    rows = models.PositiveSmallIntegerField(default=0)
    cols = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dashboard_pressureframe"
        ordering = ["timestamp"]
        indexes = [
            models.Index(fields=["session", "timestamp"]),
        ]

    def __str__(self):
        return f"Frame {self.session_id} @ {self.timestamp}"


class PressureMetric(models.Model):
    """Computed metrics for a single frame (PPI, Contact Area %, etc.)."""
    frame = models.OneToOneField(
        PressureFrame,
        on_delete=models.CASCADE,
        related_name="metrics",
    )
    peak_pressure_index = models.FloatField(
        null=True,
        blank=True,
        help_text="Highest pressure excluding regions < 10 pixels",
    )
    contact_area_pct = models.FloatField(
        null=True,
        blank=True,
        help_text="Percentage of sensor mat above lower threshold",
    )
    mean_pressure = models.FloatField(null=True, blank=True)
    max_pressure_raw = models.FloatField(null=True, blank=True)
    high_pressure_region_count = models.PositiveIntegerField(default=0)
    extra_metrics_json = models.JSONField(default=dict, blank=True)
    computed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dashboard_pressuremetric"

    def __str__(self):
        return f"Metrics for frame {self.frame_id}"


class PressureAlert(models.Model):
    """Alert for high pressure region; flagged for clinician review."""
    frame = models.ForeignKey(
        PressureFrame,
        on_delete=models.CASCADE,
        related_name="alerts",
    )
    severity = models.CharField(
        max_length=20,
        choices=[
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
        ],
        default="medium",
    )
    message = models.TextField(blank=True)
    flagged_for_review = models.BooleanField(default=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_alerts",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dashboard_pressurealert"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Alert {self.frame_id} – {self.severity}"


class UserComment(models.Model):
    """User (patient) comment on a specific pressure map timestamp."""
    frame = models.ForeignKey(
        PressureFrame,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pressure_comments",
    )
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dashboard_usercomment"
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment by {self.author.get_username()} on frame {self.frame_id}"


class ClinicianReply(models.Model):
    """Clinician reply in-thread to a user comment."""
    comment = models.ForeignKey(
        UserComment,
        on_delete=models.CASCADE,
        related_name="replies",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="clinician_replies",
    )
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dashboard_clinicianreply"
        ordering = ["created_at"]

    def __str__(self):
        return f"Reply by {self.author.get_username()} to comment {self.comment_id}"


class PatientFeedback(models.Model):
    """Overall session feedback from a patient (satisfaction/comfort notes)."""
    session = models.ForeignKey(
        PressureMapSession,
        on_delete=models.CASCADE,
        related_name="feedbacks",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="session_feedbacks",
    )
    rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Optional rating from 1 (poor) to 5 (excellent).",
    )
    text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dashboard_patientfeedback"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Feedback by {self.author.get_username()} on session {self.session_id}"
