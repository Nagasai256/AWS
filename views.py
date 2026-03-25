"""
Views for pressure monitoring: login, role-based dashboards, heat map,
charts, reports, comments. All use parameterized queries (ORM) to prevent SQL injection.
"""
import csv
import io
import zipfile
from datetime import timedelta

from django.db.models import Count
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
@login_required
def logout_view(request):
    logout(request)
    return redirect("dashboard:login")


# ---------- Home (role-based) ----------

@login_required
def home(request):
    profile = get_profile(request.user)
    if not profile:
        return redirect("dashboard:login")
    if profile.role == UserProfile.Role.ADMIN:
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


# ---------- Admin: create users ----------

@admin_required
def admin_dashboard(request):
    return render(request, "dashboard/admin_dashboard.html")


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
    frame = get_object_or_404(PressureFrame, id=frame_id)
    if not _can_view_session(request, frame.session):
        return JsonResponse({"error": "Forbidden"}, status=403)
    return JsonResponse({"id": frame.id, "timestamp": frame.timestamp.isoformat(), "data": frame.data_json})


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
    # Use the last frame in the session as the reference point so
    # "Last hour/6 hours/24 hours/7 days" are relative to the session data,
    # not the current wall-clock time.
    last_frame = frames_qs.last()
    last_ts = last_frame.timestamp
    if period == "1h":
        start = last_ts - timedelta(hours=1)
    elif period == "6h":
        start = last_ts - timedelta(hours=6)
    elif period == "24h":
        start = last_ts - timedelta(hours=24)
    elif period == "7d":
        start = last_ts - timedelta(days=7)
    else:
        # "all" or any other value: include the entire session range
        first_frame = frames_qs.first()
        start = first_frame.timestamp
    frames = frames_qs.filter(timestamp__gte=start)
    labels = []
    ppi = []
    contact = []
    for f in frames:
        labels.append(f.timestamp.isoformat())
        m = getattr(f, "metrics", None)
        ppi.append(m.peak_pressure_index if m else None)
        contact.append(m.contact_area_pct if m else None)
    return JsonResponse({"labels": labels, "peak_pressure_index": ppi, "contact_area_pct": contact})


# ---------- Reports (comparison) ----------

@login_required
def report_view(request, session_id):
    session = get_object_or_404(PressureMapSession, id=session_id)
    if not _can_view_session(request, session):
        return redirect("dashboard:home")
    metrics_list = list(PressureMetric.objects.filter(frame__session=session).select_related("frame"))
    ppi_vals = [m.peak_pressure_index for m in metrics_list if m.peak_pressure_index is not None]
    contact_vals = [m.contact_area_pct for m in metrics_list if m.contact_area_pct is not None]
    summary = {
        "ppi_max": max(ppi_vals) if ppi_vals else None,
        "ppi_avg": round(sum(ppi_vals) / len(ppi_vals), 2) if ppi_vals else None,
        "contact_avg": round(sum(contact_vals) / len(contact_vals), 2) if contact_vals else None,
        "frame_count": len(metrics_list),
    }
    prev_session = PressureMapSession.objects.filter(patient=session.patient, started_at__lt=session.started_at).order_by("-started_at").first()
    prev_summary = None
    if prev_session:
        prev_metrics = list(PressureMetric.objects.filter(frame__session=prev_session))
        p_ppi = [m.peak_pressure_index for m in prev_metrics if m.peak_pressure_index is not None]
        p_contact = [m.contact_area_pct for m in prev_metrics if m.contact_area_pct is not None]
        prev_summary = {
            "ppi_max": max(p_ppi) if p_ppi else None,
            "ppi_avg": round(sum(p_ppi) / len(p_ppi), 2) if p_ppi else None,
            "contact_avg": round(sum(p_contact) / len(p_contact), 2) if p_contact else None,
        }
    feedbacks = session.feedbacks.select_related("author").all()
    return render(
        request,
        "dashboard/report.html",
        {
            "session": session,
            "summary": summary,
            "prev_session": prev_session,
            "prev_summary": prev_summary,
            "feedbacks": feedbacks,
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
        return JsonResponse({"id": comment.id, "text": comment.text, "created_at": comment.created_at.isoformat()})
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
        return JsonResponse({"id": reply.id, "text": reply.text, "created_at": reply.created_at.isoformat()})
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
            "replies": [{"id": r.id, "author": r.author.get_username(), "text": r.text, "created_at": r.created_at.isoformat()} for r in c.replies.all()],
        })
    return JsonResponse({"comments": comments})


# ---------- CSV upload (patient or admin) ----------

@login_required
@require_http_methods(["GET", "POST"])
def upload_csv(request, patient_id=None):
    profile = get_profile(request.user)
    if patient_id:
        if not profile.is_admin and not (profile.is_clinician and can_clinician_view_patient(request.user, get_object_or_404(User, id=patient_id))):
            return redirect("dashboard:home")
        patient = get_object_or_404(User, id=patient_id)
    else:
        if not profile.is_patient:
            return redirect("dashboard:home")
        patient = request.user
    form = PressureCSVUploadForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        f = request.FILES["csv_file"]
        content = f.read()
        session_name = form.cleaned_data.get("session_name") or f.name
        frames_data = parse_pressure_csv(content)
        if not frames_data:
            return render(request, "dashboard/upload_csv.html", {"form": form, "patient": patient, "error": "No valid pressure data in CSV."})
        started = timezone.now()
        session = PressureMapSession.objects.create(patient=patient, name=session_name, started_at=started)
        for ts, grid in frames_data:
            frame = PressureFrame.objects.create(session=session, timestamp=ts, data_json=grid, rows=len(grid), cols=len(grid[0]) if grid else 0)
            metrics_dict = compute_frame_metrics(grid)
            PressureMetric.objects.create(
                frame=frame,
                peak_pressure_index=metrics_dict.get("peak_pressure_index"),
                contact_area_pct=metrics_dict.get("contact_area_pct"),
                mean_pressure=metrics_dict.get("mean_pressure"),
                max_pressure_raw=metrics_dict.get("max_pressure_raw"),
                high_pressure_region_count=metrics_dict.get("high_pressure_region_count", 0),
                extra_metrics_json=metrics_dict,
            )
            if metrics_dict.get("alert"):
                from .models import PressureAlert
                PressureAlert.objects.create(frame=frame, severity=metrics_dict.get("alert_severity", "medium"), message="High pressure region detected", flagged_for_review=True)
        session.ended_at = timezone.now()
        session.save(update_fields=["ended_at"])
        if profile.is_patient:
            return redirect("dashboard:patient_session_detail", session_id=session.id)
        return redirect("dashboard:clinician_session_detail", session_id=session.id)
    return render(request, "dashboard/upload_csv.html", {"form": form, "patient": patient})


# ---------- CSV download ----------

def _grid_to_csv(grid):
    """Convert pressure grid to CSV string."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in grid:
        writer.writerow([round(v, 2) if isinstance(v, float) else v for v in row])
    return buf.getvalue()


@login_required
@require_GET
def download_frame_csv(request, frame_id):
    """Download a single pressure frame as CSV."""
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
    """Download all frames in a session as a ZIP of CSV files."""
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
    """Download a sample pressure map CSV template."""
    sample = """10,12,15,18,22,25,28,30,28,25,22,18,15,12,10
12,15,20,25,32,38,42,45,42,38,32,25,20,15,12
15,20,28,35,45,55,62,68,62,55,45,35,28,20,15
18,25,35,45,58,72,82,90,82,72,58,45,35,25,18
22,32,45,58,75,92,105,115,105,92,75,58,45,32,22
25,38,55,72,92,112,128,140,128,112,92,72,55,38,25
28,42,62,82,105,128,148,162,148,128,105,82,62,42,28
30,45,68,90,115,140,162,178,162,140,115,90,68,45,30
28,42,62,82,105,128,148,162,148,128,105,82,62,42,28
25,38,55,72,92,112,128,140,128,112,92,72,55,38,25
22,32,45,58,75,92,105,115,105,92,75,58,45,32,22
18,25,35,45,58,72,82,90,82,72,58,45,35,25,18
15,20,28,35,45,55,62,68,62,55,45,35,28,20,15
12,15,20,25,32,38,42,45,42,38,32,25,20,15,12
10,12,15,18,22,25,28,30,28,25,22,18,15,12,10
"""
    response = HttpResponse(sample, content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="sample_pressure_map.csv"'
    return response


# ---------- Plain-English explanation (nice-to-have) ----------

@login_required
@require_GET
def frame_explanation(request, frame_id):
    frame = get_object_or_404(PressureFrame, id=frame_id)
    if not _can_view_session(request, frame.session):
        return JsonResponse({"error": "Forbidden"}, status=403)
    m = getattr(frame, "metrics", None)
    if not m:
        return JsonResponse({"explanation": "No metrics computed for this frame."})
    parts = []
    if m.peak_pressure_index is not None:
        parts.append(f"Peak pressure index is {m.peak_pressure_index}. This is the highest pressure in areas where you had meaningful contact (at least 10 sensor points).")
    if m.contact_area_pct is not None:
        parts.append(f"About {m.contact_area_pct}% of the sensor mat was in contact. Higher percentage means more of the seat was covered.")
    if m.high_pressure_region_count and m.high_pressure_region_count > 0:
        parts.append(f"There were {m.high_pressure_region_count} high-pressure region(s) detected. Consider shifting position to reduce prolonged pressure in one spot.")
    return JsonResponse({"explanation": " ".join(parts) if parts else "No summary available."})
