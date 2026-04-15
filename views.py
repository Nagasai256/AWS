"""
Views for pressure monitoring: login, role-based dashboards, heat map,
charts, reports, comments, group management, and clinician alerts.
All use parameterized queries (ORM) to prevent SQL injection.
"""
import csv
import io
import zipfile
from datetime import timedelta

from django.db.models import Count, Prefetch
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse, FileResponse
from django.views.decorators.http import require_http_methods, require_GET, require_POST
from django.utils import timezone
from django.utils.text import slugify

from .models import (
    UserProfile,
    PressureMapSession,
    PressureFrame,
    PressureMetric,
    PressureAlert,
    UserComment,
    ClinicianReply,
    PatientGroup,
    ClinicianPatientAccess,
    PatientFeedback,
    ClinicianFeedbackReply,
)

from .decorators import admin_required, clinician_required, patient_required
from .auth_helpers import get_profile, get_patients_accessible_by_clinician, can_clinician_view_patient
from .forms import (
    LoginForm,
    UserCreateForm,
    UserCommentForm,
    ClinicianReplyForm,
    PressureCSVUploadForm,
    PatientFeedbackForm,
    ClinicianFeedbackReplyForm,
)
from .services.pressure_analysis import (
    parse_pressure_csv,
    compute_frame_metrics,
)

User = get_user_model()


# ---------- Auth ----------

@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    form = LoginForm(request, data=request.POST or None)
    if form.is_valid():
        user = form.get_user()
        login(request, user)
        remember_me = request.POST.get("remember_me") == "on"
        if not remember_me:
            request.session.set_expiry(0)
        next_url = request.GET.get("next") or "dashboard:home"
        return redirect(next_url)
    return render(request, "dashboard/login.html", {"form": form})


@require_GET
def register_view(request):
    profile = get_profile(request.user) if request.user.is_authenticated else None
    if profile and profile.role == UserProfile.Role.ADMIN:
        return redirect("dashboard:admin_create_user")
    return render(request, "dashboard/register_info.html")


@require_GET
@login_required
def logout_view(request):
    logout(request)
    return redirect("dashboard:login")


# ---------- Home (role-based) ----------

@login_required
def home(request):
    profile = get_profile(request.user)
    if not profile:
        if request.user.is_superuser:
            return redirect("dashboard:admin_dashboard")
        return redirect("dashboard:login")
    if profile.role == UserProfile.Role.ADMIN or request.user.is_superuser:
        return redirect("dashboard:admin_dashboard")
    if profile.role == UserProfile.Role.CLINICIAN:
        return redirect("dashboard:clinician_patients")
    return redirect("dashboard:patient_sessions")


# ---------- Patient: my sessions ----------

@patient_required
def patient_sessions(request):
    sessions = (
        PressureMapSession.objects.filter(patient=request.user)
        .annotate(comment_count=Count("frames__comments", distinct=True))
        .order_by("-started_at")
    )
    return render(request, "dashboard/patient_sessions.html", {"sessions": sessions})


@patient_required
def patient_session_detail(request, session_id):
    session = get_object_or_404(PressureMapSession, id=session_id, patient=request.user)
    frames = session.frames.select_related("metrics").order_by("timestamp")
    feedbacks = session.feedbacks.select_related("author").all()
    form = PatientFeedbackForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        fb = form.save(commit=False)
        fb.session = session
        fb.author = request.user
        fb.save()
        return redirect("dashboard:patient_session_detail", session_id=session.id)
    return render(
        request,
        "dashboard/session_detail.html",
        {
            "session": session,
            "frames": frames,
            "feedbacks": feedbacks,
            "feedback_form": form,
        },
    )


# ---------- Clinician: patient list & view ----------

@clinician_required
def clinician_patients(request):
    patients = get_patients_accessible_by_clinician(request.user)
    return render(request, "dashboard/clinician_patients.html", {"patients": patients})


@clinician_required
def clinician_patient_sessions(request, patient_id):
    patient = get_object_or_404(User, id=patient_id)
    if not can_clinician_view_patient(request.user, patient):
        return redirect("dashboard:clinician_patients")
    sessions = (
        PressureMapSession.objects.filter(patient=patient)
        .annotate(comment_count=Count("frames__comments", distinct=True))
        .order_by("-started_at")
    )
    return render(request, "dashboard/clinician_patient_sessions.html", {"patient": patient, "sessions": sessions})


@clinician_required
def clinician_session_detail(request, session_id):
    session = get_object_or_404(PressureMapSession, id=session_id)
    if not can_clinician_view_patient(request.user, session.patient):
        return redirect("dashboard:clinician_patients")
    frames = session.frames.select_related("metrics").order_by("timestamp")
    feedbacks = session.feedbacks.select_related("author").all()
    return render(
        request,
        "dashboard/session_detail.html",
        {
            "session": session,
            "frames": frames,
            "is_clinician_view": True,
            "feedbacks": feedbacks,
        },
    )


@clinician_required
@require_http_methods(["GET", "POST"])
def clinician_alerts(request):
    """Clinician view: list all flagged alerts for accessible patients, with review action."""
    accessible_patients = get_patients_accessible_by_clinician(request.user)

    if request.method == "POST":
        alert_id = request.POST.get("alert_id")
        if alert_id:
            alert = get_object_or_404(PressureAlert, id=alert_id)
            if can_clinician_view_patient(request.user, alert.frame.session.patient):
                alert.reviewed_by = request.user
                alert.reviewed_at = timezone.now()
                alert.flagged_for_review = False
                alert.save(update_fields=["reviewed_by", "reviewed_at", "flagged_for_review"])
        return redirect("dashboard:clinician_alerts")

    show_reviewed = request.GET.get("show_reviewed") == "1"
    alerts_qs = PressureAlert.objects.filter(
        frame__session__patient__in=accessible_patients
    ).select_related(
        "frame__session__patient", "reviewed_by"
    ).order_by("-created_at")

    if not show_reviewed:
        alerts_qs = alerts_qs.filter(flagged_for_review=True)

    return render(request, "dashboard/clinician_alerts.html", {
        "alerts": alerts_qs,
        "show_reviewed": show_reviewed,
    })


# ---------- Admin: create users and manage groups ----------

@admin_required
def admin_dashboard(request):
    return render(request, "dashboard/admin_dashboard.html")


@admin_required
def admin_patient_uploads(request):
    patients = User.objects.filter(profile__role=UserProfile.Role.PATIENT).order_by("username")
    return render(request, "dashboard/admin_patient_uploads.html", {"patients": patients})


@admin_required
@require_http_methods(["GET", "POST"])
def admin_create_user(request):
    form = UserCreateForm(request.POST or None)
    if form.is_valid():
        form.save()
        return redirect("dashboard:admin_dashboard")
    return render(request, "dashboard/admin_create_user.html", {"form": form})


@admin_required
def admin_manage_groups(request):
    groups = PatientGroup.objects.prefetch_related("patients").all()
    return render(request, "dashboard/admin_manage_groups.html", {"groups": groups})


@admin_required
@require_http_methods(["GET", "POST"])
def admin_create_group(request):
    """Create a new patient group."""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        if name:
            group = PatientGroup.objects.create(name=name, description=description)
            return redirect("dashboard:admin_group_detail", group_id=group.id)
    return render(request, "dashboard/admin_create_group.html")


@admin_required
@require_http_methods(["GET", "POST"])
def admin_group_detail(request, group_id):
    """View and manage a patient group: add/remove patients and clinician access."""
    group = get_object_or_404(PatientGroup, id=group_id)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_patient":
            patient_id = request.POST.get("patient_id")
            patient = get_object_or_404(User, id=patient_id, profile__role=UserProfile.Role.PATIENT)
            group.patients.add(patient)
        elif action == "remove_patient":
            patient_id = request.POST.get("patient_id")
            patient = get_object_or_404(User, id=patient_id)
            group.patients.remove(patient)
        elif action == "grant_access":
            clinician_id = request.POST.get("clinician_id")
            clinician = get_object_or_404(User, id=clinician_id, profile__role=UserProfile.Role.CLINICIAN)
            ClinicianPatientAccess.objects.get_or_create(clinician=clinician, group=group)
        elif action == "revoke_access":
            clinician_id = request.POST.get("clinician_id")
            ClinicianPatientAccess.objects.filter(
                clinician_id=clinician_id, group=group
            ).delete()
        elif action == "delete_group":
            group.delete()
            return redirect("dashboard:admin_manage_groups")
        return redirect("dashboard:admin_group_detail", group_id=group.id)

    group_patient_ids = set(group.patients.values_list("id", flat=True))
    all_patients = User.objects.filter(profile__role=UserProfile.Role.PATIENT).order_by("username")
    all_clinicians = User.objects.filter(profile__role=UserProfile.Role.CLINICIAN).order_by("username")
    access_grants = ClinicianPatientAccess.objects.filter(group=group).select_related("clinician")
    granted_clinician_ids = set(access_grants.values_list("clinician_id", flat=True))

    return render(request, "dashboard/admin_group_detail.html", {
        "group": group,
        "group_patients": all_patients.filter(id__in=group_patient_ids),
        "available_patients": all_patients.exclude(id__in=group_patient_ids),
        "all_clinicians": all_clinicians,
        "granted_clinician_ids": granted_clinician_ids,
    })


# ---------- Heat map & frame data (JSON) ----------

def _can_view_session(request, session):
    if session.patient_id == request.user.id:
        return True
    profile = get_profile(request.user)
    if profile and profile.is_clinician and can_clinician_view_patient(request.user, session.patient):
        return True
    return False


@login_required
@require_GET
def frame_data(request, frame_id):
    """Return frame grid data plus computed metrics and any active alerts."""
    frame = get_object_or_404(PressureFrame, id=frame_id)
    if not _can_view_session(request, frame.session):
        return JsonResponse({"error": "Forbidden"}, status=403)

    metrics = getattr(frame, "metrics", None)
    alerts = list(
        frame.alerts.values("id", "severity", "message", "flagged_for_review")
    )
    metrics_data = None
    if metrics:
        metrics_data = {
            "peak_pressure_index": metrics.peak_pressure_index,
            "contact_area_pct": metrics.contact_area_pct,
            "mean_pressure": metrics.mean_pressure,
            "high_pressure_region_count": metrics.high_pressure_region_count,
            "extra": metrics.extra_metrics_json or {},
        }

    # Calculate max value for heatmap coloring
    max_val = None
    if frame.data_json:
        try:
            max_val = max(max(row) if row else 0 for row in frame.data_json) or None
        except (TypeError, ValueError):
            max_val = None

    return JsonResponse({
        "id": frame.id,
        "timestamp": frame.timestamp.isoformat(),
        "data": frame.data_json,
        "maxVal": max_val,
        "metrics": metrics_data,
        "alerts": alerts,
    })


# ---------- Charts: metrics over time (JSON) ----------

@login_required
@require_GET
def metrics_chart_data(request, session_id):
    session = get_object_or_404(PressureMapSession, id=session_id)
    if not _can_view_session(request, session):
        return JsonResponse({"error": "Forbidden"}, status=403)

    period = request.GET.get("period", "24h")
    frames_qs = session.frames.select_related("metrics").order_by("timestamp")
    if not frames_qs.exists():
        return JsonResponse({"labels": [], "peak_pressure_index": [], "contact_area_pct": []})

    last_ts = frames_qs.last().timestamp
    if period == "1h":
        start = last_ts - timedelta(hours=1)
    elif period == "6h":
        start = last_ts - timedelta(hours=6)
    elif period == "24h":
        start = last_ts - timedelta(hours=24)
    elif period == "7d":
        start = last_ts - timedelta(days=7)
    else:
        start = frames_qs.first().timestamp

    frames = frames_qs.filter(timestamp__gte=start)
    labels, ppi, contact, mean_p = [], [], [], []
    for f in frames:
        labels.append(f.timestamp.isoformat())
        m = getattr(f, "metrics", None)
        ppi.append(m.peak_pressure_index if m else None)
        contact.append(m.contact_area_pct if m else None)
        mean_p.append(m.mean_pressure if m else None)

    return JsonResponse({
        "labels": labels,
        "peak_pressure_index": ppi,
        "contact_area_pct": contact,
        "mean_pressure": mean_p,
    })


# ---------- Reports (comparison with delta values) ----------

@login_required
def report_view(request, session_id):
    session = get_object_or_404(PressureMapSession, id=session_id)
    if not _can_view_session(request, session):
        return redirect("dashboard:home")

    is_own = session.patient_id == request.user.id
    is_clinician_view = get_profile(request.user).is_clinician

    # Handle feedback form submission (patient)
    feedback_form = None
    if is_own and request.method == "POST" and "feedback_submit" in request.POST:
        feedback_form = PatientFeedbackForm(request.POST)
        if feedback_form.is_valid():
            feedback = feedback_form.save(commit=False)
            feedback.session = session
            feedback.author = request.user
            feedback.save()
            return redirect("dashboard:report", session_id=session_id)
    elif is_own:
        feedback_form = PatientFeedbackForm()

    # Handle clinician reply to feedback
    if is_clinician_view and request.method == "POST" and "reply_feedback_id" in request.POST:
        feedback_id = request.POST.get("reply_feedback_id")
        feedback = get_object_or_404(PatientFeedback, id=feedback_id, session=session)
        reply_form = ClinicianFeedbackReplyForm(request.POST)
        if reply_form.is_valid():
            reply = reply_form.save(commit=False)
            reply.feedback = feedback
            reply.author = request.user
            reply.save()
            return redirect("dashboard:report", session_id=session_id)

    metrics_list = list(
        PressureMetric.objects.filter(frame__session=session).select_related("frame")
    )
    ppi_vals = [m.peak_pressure_index for m in metrics_list if m.peak_pressure_index is not None]
    contact_vals = [m.contact_area_pct for m in metrics_list if m.contact_area_pct is not None]
    mean_vals = [m.mean_pressure for m in metrics_list if m.mean_pressure is not None]

    summary = {
        "ppi_max": max(ppi_vals) if ppi_vals else None,
        "ppi_avg": round(sum(ppi_vals) / len(ppi_vals), 2) if ppi_vals else None,
        "contact_avg": round(sum(contact_vals) / len(contact_vals), 2) if contact_vals else None,
        "mean_avg": round(sum(mean_vals) / len(mean_vals), 2) if mean_vals else None,
        "frame_count": len(metrics_list),
        "alert_count": PressureAlert.objects.filter(frame__session=session).count(),
    }

    prev_session = (
        PressureMapSession.objects.filter(
            patient=session.patient, started_at__lt=session.started_at
        )
        .order_by("-started_at")
        .first()
    )

    prev_summary = None
    deltas = {}
    if prev_session:
        prev_metrics = list(PressureMetric.objects.filter(frame__session=prev_session))
        p_ppi = [m.peak_pressure_index for m in prev_metrics if m.peak_pressure_index is not None]
        p_contact = [m.contact_area_pct for m in prev_metrics if m.contact_area_pct is not None]
        p_mean = [m.mean_pressure for m in prev_metrics if m.mean_pressure is not None]
        prev_summary = {
            "ppi_max": max(p_ppi) if p_ppi else None,
            "ppi_avg": round(sum(p_ppi) / len(p_ppi), 2) if p_ppi else None,
            "contact_avg": round(sum(p_contact) / len(p_contact), 2) if p_contact else None,
            "mean_avg": round(sum(p_mean) / len(p_mean), 2) if p_mean else None,
        }
        # Compute deltas (positive = increase vs previous session)
        if summary["ppi_max"] is not None and prev_summary["ppi_max"] is not None:
            deltas["ppi_max"] = round(summary["ppi_max"] - prev_summary["ppi_max"], 2)
        if summary["ppi_avg"] is not None and prev_summary["ppi_avg"] is not None:
            deltas["ppi_avg"] = round(summary["ppi_avg"] - prev_summary["ppi_avg"], 2)
        if summary["contact_avg"] is not None and prev_summary["contact_avg"] is not None:
            deltas["contact_avg"] = round(summary["contact_avg"] - prev_summary["contact_avg"], 2)
        if summary["mean_avg"] is not None and prev_summary["mean_avg"] is not None:
            deltas["mean_avg"] = round(summary["mean_avg"] - prev_summary["mean_avg"], 2)

    # Fetch feedbacks with replies
    feedbacks = session.feedbacks.select_related("author").prefetch_related("replies__author").all()

    return render(
        request,
        "dashboard/report.html",
        {
            "session": session,
            "summary": summary,
            "prev_session": prev_session,
            "prev_summary": prev_summary,
            "deltas": deltas,
            "feedbacks": feedbacks,
            "is_own": is_own,
            "is_clinician_view": is_clinician_view,
            "feedback_form": feedback_form,
        },
    )


# ---------- Comments & replies ----------

@login_required
@require_POST
def add_comment(request, frame_id):
    frame = get_object_or_404(PressureFrame, id=frame_id)
    if not _can_view_session(request, frame.session):
        return JsonResponse({"error": "Forbidden"}, status=403)
    if frame.session.patient_id != request.user.id:
        return JsonResponse({"error": "Only the patient can add comments"}, status=403)
    form = UserCommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.frame = frame
        comment.author = request.user
        comment.save()
        return JsonResponse({
            "id": comment.id,
            "text": comment.text,
            "created_at": comment.created_at.isoformat(),
        })
    return JsonResponse({"error": "Invalid form"}, status=400)


@clinician_required
@require_POST
def add_reply(request, comment_id):
    comment = get_object_or_404(UserComment, id=comment_id)
    if not _can_view_session(request, comment.frame.session):
        return JsonResponse({"error": "Forbidden"}, status=403)
    form = ClinicianReplyForm(request.POST)
    if form.is_valid():
        reply = form.save(commit=False)
        reply.comment = comment
        reply.author = request.user
        reply.save()
        return JsonResponse({
            "id": reply.id,
            "text": reply.text,
            "created_at": reply.created_at.isoformat(),
        })
    return JsonResponse({"error": "Invalid form"}, status=400)


@login_required
@require_GET
def comments_for_frame(request, frame_id):
    frame = get_object_or_404(PressureFrame, id=frame_id)
    if not _can_view_session(request, frame.session):
        return JsonResponse({"error": "Forbidden"}, status=403)
    comments = []
    for c in frame.comments.prefetch_related("replies").order_by("created_at"):
        comments.append({
            "id": c.id,
            "author": c.author.get_username(),
            "text": c.text,
            "created_at": c.created_at.isoformat(),
            "replies": [
                {
                    "id": r.id,
                    "author": r.author.get_username(),
                    "text": r.text,
                    "created_at": r.created_at.isoformat(),
                }
                for r in c.replies.all()
            ],
        })
    return JsonResponse({"comments": comments})


# ---------- CSV upload (patient or admin) ----------

@login_required
@require_http_methods(["GET", "POST"])
def upload_csv(request, patient_id=None):
    profile = get_profile(request.user)
    if patient_id:
        if not profile.is_admin and not (
            profile.is_clinician
            and can_clinician_view_patient(request.user, get_object_or_404(User, id=patient_id))
        ):
            return redirect("dashboard:home")
        patient = get_object_or_404(User, id=patient_id)
    else:
        if not profile.is_patient:
            return redirect("dashboard:home")
        patient = request.user

    form = PressureCSVUploadForm(request.POST or None, request.FILES or None, patient=patient)
    if form.is_valid():
        f = request.FILES["csv_file"]
        content = f.read()
        session_choice = form.cleaned_data.get("session_choice", "new")
        session_name = form.cleaned_data.get("session_name") or f.name
        frames_data = parse_pressure_csv(content)
        if not frames_data:
            return render(request, "dashboard/upload_csv.html", {
                "form": form,
                "patient": patient,
                "error": "No valid pressure data found in this CSV. Please check the file format.",
            })
        if session_choice == "new":
            started = timezone.now()
            session = PressureMapSession.objects.create(
                patient=patient, name=session_name, started_at=started
            )
        else:
            session = get_object_or_404(PressureMapSession, id=int(session_choice), patient=patient)
            # Update session name if provided
            if session_name and session_name != f.name:
                session.name = session_name
                session.save(update_fields=["name"])
        for ts, grid in frames_data:
            frame = PressureFrame.objects.create(
                session=session,
                timestamp=ts,
                data_json=grid,
                rows=len(grid),
                cols=len(grid[0]) if grid else 0,
            )
            metrics_dict = compute_frame_metrics(grid)
            PressureMetric.objects.create(
                frame=frame,
                peak_pressure_index=metrics_dict.get("peak_pressure_index"),
                contact_area_pct=metrics_dict.get("contact_area_pct"),
                mean_pressure=metrics_dict.get("mean_pressure"),
                max_pressure_raw=metrics_dict.get("max_pressure_raw"),
                high_pressure_region_count=metrics_dict.get("high_pressure_region_count", 0),
                extra_metrics_json={k: v for k, v in metrics_dict.items()
                                    if k not in ("alert", "alert_severity",
                                                 "peak_pressure_index", "contact_area_pct",
                                                 "mean_pressure", "max_pressure_raw",
                                                 "high_pressure_region_count")},
            )
            if metrics_dict.get("alert"):
                PressureAlert.objects.create(
                    frame=frame,
                    severity=metrics_dict.get("alert_severity", "medium"),
                    message="High pressure region detected",
                    flagged_for_review=True,
                )
        session.ended_at = timezone.now()
        session.save(update_fields=["ended_at"])
        if profile.is_patient:
            return redirect("dashboard:patient_session_detail", session_id=session.id)
        return redirect("dashboard:clinician_session_detail", session_id=session.id)

    return render(request, "dashboard/upload_csv.html", {"form": form, "patient": patient})


# ---------- CSV download ----------

def _grid_to_csv(grid):
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in grid:
        writer.writerow([round(v, 2) if isinstance(v, float) else v for v in row])
    return buf.getvalue()


@login_required
@require_GET
def download_frame_csv(request, frame_id):
    frame = get_object_or_404(PressureFrame, id=frame_id)
    if not _can_view_session(request, frame.session):
        return HttpResponse("Forbidden", status=403)
    grid = frame.data_json
    if not grid:
        return HttpResponse("No data in this frame.", status=404)
    csv_content = _grid_to_csv(grid)
    ts_str = frame.timestamp.strftime("%Y%m%d_%H%M%S")
    filename = f"pressure_frame_{frame.id}_{ts_str}.csv"
    response = HttpResponse(csv_content, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@require_GET
def download_session_csv(request, session_id):
    session = get_object_or_404(PressureMapSession, id=session_id)
    if not _can_view_session(request, session):
        return HttpResponse("Forbidden", status=403)
    frames = session.frames.order_by("timestamp")
    if not frames:
        return HttpResponse("No frames in this session.", status=404)
    session_slug = slugify(session.name or f"session_{session.id}")[:50]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for frame in frames:
            grid = frame.data_json
            if not grid:
                continue
            csv_content = _grid_to_csv(grid)
            ts_str = frame.timestamp.strftime("%Y%m%d_%H%M%S")
            arcname = f"{session_slug}/frame_{frame.id}_{ts_str}.csv"
            zf.writestr(arcname, csv_content)
    buf.seek(0)
    filename = f"{session_slug}_pressure_maps.zip"
    return FileResponse(buf, as_attachment=True, filename=filename, content_type="application/zip")


@login_required
@require_GET
def download_sample_csv(request):
    sample = (
        "10,12,15,18,22,25,28,30,28,25,22,18,15,12,10\n"
        "12,15,20,25,32,38,42,45,42,38,32,25,20,15,12\n"
        "15,20,28,35,45,55,62,68,62,55,45,35,28,20,15\n"
        "18,25,35,45,58,72,82,90,82,72,58,45,35,25,18\n"
        "22,32,45,58,75,92,105,115,105,92,75,58,45,32,22\n"
        "25,38,55,72,92,112,128,140,128,112,92,72,55,38,25\n"
        "28,42,62,82,105,128,148,162,148,128,105,82,62,42,28\n"
        "30,45,68,90,115,140,162,178,162,140,115,90,68,45,30\n"
        "28,42,62,82,105,128,148,162,148,128,105,82,62,42,28\n"
        "25,38,55,72,92,112,128,140,128,112,92,72,55,38,25\n"
        "22,32,45,58,75,92,105,115,105,92,75,58,45,32,22\n"
        "18,25,35,45,58,72,82,90,82,72,58,45,35,25,18\n"
        "15,20,28,35,45,55,62,68,62,55,45,35,28,20,15\n"
        "12,15,20,25,32,38,42,45,42,38,32,25,20,15,12\n"
        "10,12,15,18,22,25,28,30,28,25,22,18,15,12,10\n"
    )
    response = HttpResponse(sample, content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="sample_pressure_map.csv"'
    return response


# ---------- Plain-English explanation ----------

@login_required
@require_GET
def frame_explanation(request, frame_id):
    frame = get_object_or_404(PressureFrame, id=frame_id)
    if not _can_view_session(request, frame.session):
        return JsonResponse({"error": "Forbidden"}, status=403)
    m = getattr(frame, "metrics", None)
    if not m:
        return JsonResponse({"explanation": "No metrics have been computed for this frame yet."})

    parts = []
    if m.peak_pressure_index is not None:
        level = "low" if m.peak_pressure_index < 80 else ("moderate" if m.peak_pressure_index < 140 else "high")
        parts.append(
            f"The peak pressure in this snapshot is {m.peak_pressure_index} — "
            f"that is considered {level}. This is measured in areas where at least "
            f"10 sensor points are in contact, so small isolated spots are ignored."
        )
    if m.contact_area_pct is not None:
        coverage = "small" if m.contact_area_pct < 30 else ("moderate" if m.contact_area_pct < 60 else "large")
        parts.append(
            f"About {m.contact_area_pct}% of the sensor mat was in contact — "
            f"a {coverage} coverage area. A higher percentage means your weight is more evenly spread."
        )
    if m.high_pressure_region_count and m.high_pressure_region_count > 0:
        parts.append(
            f"There {'is' if m.high_pressure_region_count == 1 else 'are'} "
            f"{m.high_pressure_region_count} high-pressure region(s) detected. "
            f"Prolonged pressure in a concentrated area can be uncomfortable — "
            f"try shifting position slightly to redistribute the load."
        )
    extra = m.extra_metrics_json or {}
    lr = extra.get("left_right_asymmetry_pct")
    if lr is not None and abs(lr) > 10:
        side = "left" if lr > 0 else "right"
        parts.append(
            f"Your weight distribution is slightly uneven: about {abs(lr):.1f}% more "
            f"load is on the {side} side. Centering your posture may help."
        )

    return JsonResponse({
        "explanation": " ".join(parts) if parts else "No summary available for this frame."
    })
