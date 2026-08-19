from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db
from app.models import User, UserSettings

from .forms import DeleteAccountForm, ForgotPasswordForm, LoginForm, RegisterForm, ResetPasswordForm

bp = Blueprint("auth", __name__)


@bp.get("/login")
@bp.post("/login")
def login():
    """Authenticate an account and establish its session."""
    if current_user.is_authenticated:
        return redirect(url_for("chat.chat_home"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if user and user.check_password(form.password.data):
            if user.settings is None:
                user.settings = UserSettings()
            if user.password_needs_rehash():
                user.set_password(form.password.data)
            db.session.commit()
            login_user(user, remember=form.remember.data)
            return redirect(request.args.get("next") or url_for("chat.chat_home"))
        flash("Invalid email or password.", "error")
    return render_template("auth/login.html", form=form)


@bp.get("/register")
@bp.post("/register")
def register():
    """Create an account and sign in the new user."""
    if current_user.is_authenticated:
        return redirect(url_for("chat.chat_home"))
    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        if User.query.filter_by(email=email).first():
            flash("An account already exists for that email.", "error")
        else:
            user = User(
                email=email,
                is_platform_admin=email in current_app.config.get("PLATFORM_ADMIN_EMAILS", ()),
            )
            user.set_password(form.password.data)
            user.settings = UserSettings()
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash(
                "Account created. Email verification is ready to wire to your mail provider.",
                "info",
            )
            return redirect(url_for("chat.chat_home"))
    return render_template("auth/register.html", form=form)


@bp.get("/logout")
@login_required
def logout():
    """End the authenticated user's session."""
    logout_user()
    return redirect(url_for("auth.login"))


@bp.get("/forgot-password")
@bp.post("/forgot-password")
def forgot_password():
    """Create a password-reset token without revealing account existence."""
    form = ForgotPasswordForm()
    reset_token = None
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if user:
            reset_token = user.make_token("password-reset")
        flash("If that account exists, a reset link will be sent.", "info")
    return render_template("auth/forgot_password.html", form=form, reset_token=reset_token)


@bp.get("/reset-password/<token>")
@bp.post("/reset-password/<token>")
def reset_password(token: str):
    """Replace the password associated with a valid reset token.

    Args:
        token: Time-limited password-reset token.
    """
    user = User.verify_token(token, "password-reset", max_age=3600)
    if not user:
        flash("That reset link is invalid or expired.", "error")
        return redirect(url_for("auth.forgot_password"))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.session_version += 1
        db.session.commit()
        flash("Your password was updated. Please sign in again.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", form=form)


@bp.get("/verify-email/<token>")
@login_required
def verify_email(token: str):
    """Mark the current user's email as verified when the token is valid.

    Args:
        token: Time-limited email-verification token.
    """
    user = User.verify_token(token, "verify-email", max_age=86400)
    if user and user.id == current_user.id:
        user.is_email_verified = True
        db.session.commit()
        flash("Email verified.", "success")
    else:
        flash("That verification link is invalid or expired.", "error")
    return redirect(url_for("settings.settings_page"))


@bp.get("/account/delete")
@bp.post("/account/delete")
@login_required
def delete_account():
    """Delete the current account after confirming its email address."""
    form = DeleteAccountForm()
    if form.validate_on_submit() and form.email.data.lower().strip() == current_user.email:
        db.session.delete(current_user)
        db.session.commit()
        logout_user()
        flash("Your account was deleted.", "success")
        return redirect(url_for("auth.register"))
    return render_template("auth/delete_account.html", form=form)
