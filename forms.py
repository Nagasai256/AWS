"""
Forms for login, user creation, comments, and CSV upload.
All use Django's built-in security (CSRF, validation) to prevent injection.
"""
from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth import get_user_model

from .models import UserProfile, UserComment, ClinicianReply, PatientFeedback, ClinicianFeedbackReply, PressureMapSession

User = get_user_model()


class LoginForm(AuthenticationForm):
    """Login form with styled fields."""
    username = forms.CharField(
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Username", "autofocus": True}),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Password"}),
    )


class UserCreateForm(forms.Form):
    """Admin form to create user accounts (patient or clinician) – layout matches Create Account Form design."""
    first_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "First"}),
    )
    last_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Last"}),
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    birthdate = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "placeholder": "MM/DD/YYYY", "type": "date"}),
    )
    username = forms.CharField(
        max_length=150,
        label="Preferred Username",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
        min_length=8,
    )
    role = forms.ChoiceField(
        choices=[(r, l) for r, l in UserProfile.Role.choices if r != UserProfile.Role.ADMIN],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    email_updates = forms.ChoiceField(
        required=False,
        label="Do you want to receive updates by email?",
        choices=[("yes", "Yes"), ("no", "No")],
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
        initial="no",
    )

    def clean_username(self):
        if User.objects.filter(username=self.cleaned_data["username"]).exists():
            raise forms.ValidationError("A user with this username already exists.")
        return self.cleaned_data["username"]

    def save(self):
        user = User.objects.create_user(
            username=self.cleaned_data["username"],
            password=self.cleaned_data["password"],
            email=self.cleaned_data.get("email") or "",
            first_name=self.cleaned_data.get("first_name") or "",
            last_name=self.cleaned_data.get("last_name") or "",
        )
        profile = user.profile
        profile.role = self.cleaned_data["role"]
        profile.birthdate = self.cleaned_data.get("birthdate")
        profile.email_updates = self.cleaned_data.get("email_updates") == "yes"
        profile.save(update_fields=["role", "birthdate", "email_updates"])
        return user


class UserCommentForm(forms.ModelForm):
    """Patient comment on a pressure frame."""
    text = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Add a comment or flag a risk region..."}),
        max_length=2000,
    )

    class Meta:
        model = UserComment
        fields = ("text",)


class ClinicianReplyForm(forms.ModelForm):
    """Clinician reply to a comment."""
    text = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Reply..."}),
        max_length=2000,
    )

    class Meta:
        model = ClinicianReply
        fields = ("text",)


class PressureCSVUploadForm(forms.Form):
    """Upload CSV pressure map data."""
    session_choice = forms.ChoiceField(
        required=False,
        label="Session",
        choices=[("new", "Create new session")],
        initial="new",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    session_name = forms.CharField(max_length=200, required=False, label="Session name")
    csv_file = forms.FileField(
        label="CSV file",
        allow_empty_file=False,
        help_text="CSV with pressure grid (numbers). Optional first column: timestamp.",
    )

    def __init__(self, *args, patient=None, **kwargs):
        super().__init__(*args, **kwargs)
        if patient:
            sessions = PressureMapSession.objects.filter(patient=patient).order_by("-started_at")
            choices = [("new", "Create new session")] + [
                (str(s.id), f"{s.name} ({s.started_at.date()})") for s in sessions[:10]  # Last 10 sessions
            ]
            self.fields["session_choice"].choices = choices


class PatientFeedbackForm(forms.ModelForm):
    """Overall session feedback form for patients."""

    class Meta:
        model = PatientFeedback
        fields = ("rating", "text")

    rating = forms.ChoiceField(
        required=False,
        label="Overall comfort rating",
        choices=[("", "Skip rating")] + [(str(i), str(i)) for i in range(1, 6)],
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    text = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Share anything about your comfort, pain, or seating experience during this session...",
            }
        ),
        max_length=2000,
    )

    def clean_rating(self):
        """Convert empty string to None for optional rating field."""
        rating = self.cleaned_data.get("rating")
        if rating == "" or rating is None:
            return None
        return int(rating)


class ClinicianFeedbackReplyForm(forms.ModelForm):
    """Clinician reply to patient feedback."""
    text = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Reply to patient feedback..."}),
        max_length=2000,
    )

    class Meta:
        model = ClinicianFeedbackReply
        fields = ("text",)
