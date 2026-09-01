"""
auth.py
-------
Signup, email confirmation codes, and login.

HOW PASSWORDS ARE HANDLED:
We never store the actual password. We store a "hash" - a scrambled version
that cannot be turned back into the original. When you log in, we scramble
what you typed and compare the two scrambles. So even someone who steals
the database file cannot read anyone's password.

HOW THE CONFIRMATION CODE WORKS:
On signup we generate a random 6-digit code, save it against the user, and
mark the account unverified. The user must type that code before they can
log in. Right now the code is shown on screen and printed to your terminal
(no email setup needed). The send_confirmation_email() function below is
where you would plug in real email later - it is clearly marked.
"""

import random
import re

from werkzeug.security import check_password_hash, generate_password_hash

import database


def generate_code():
    """A random 6-digit confirmation code, e.g. '482913'."""
    return f"{random.randint(100000, 999999)}"


def send_confirmation_email(email, code):
    """
    WHERE REAL EMAIL WOULD GO.

    Right now this only prints the code to your terminal, and the app shows
    it on the verification page. That is deliberate: it needs zero setup and
    demos perfectly.

    TO SEND REAL EMAIL LATER (about 10 minutes of work):
      1. Turn on 2-factor auth on a Gmail account.
      2. Create an "App Password" at myaccount.google.com/apppasswords
      3. Uncomment the block below and put the address + app password in
         environment variables (NEVER type them into this file).
    """
    print(f"\n[CONFIRMATION CODE] {email} -> {code}\n")

    # import os, smtplib
    # from email.message import EmailMessage
    # message = EmailMessage()
    # message["Subject"] = "Your FinOz confirmation code"
    # message["From"] = os.environ["MAIL_FROM"]
    # message["To"] = email
    # message.set_content(f"Your confirmation code is {code}")
    # with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    #     server.login(os.environ["MAIL_FROM"], os.environ["MAIL_APP_PASSWORD"])
    #     server.send_message(message)

    return True


def validate_signup(username, email, password):
    """
    Check the signup form before we touch the database.
    Returns an error message string, or None if everything is fine.
    """
    if not username or not email or not password:
        return "Please fill in every field."
    if len(username) < 3:
        return "Username must be at least 3 characters."
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return "That does not look like a valid email address."
    if len(password) < 6:
        return "Password must be at least 6 characters."
    if database.get_user_by_username(username):
        return "That username is already taken."
    if database.get_user_by_email(email):
        return "An account with that email already exists."
    return None


def signup(username, email, password, risk="medium"):
    """
    Create an unverified account and generate a confirmation code.
    Returns (user_id, code, error_message).
    """
    try:
        username = (username or "").strip()
        email = (email or "").strip().lower()

        error = validate_signup(username, email, password)
        if error:
            return None, None, error

        if risk not in ("low", "medium", "high"):
            risk = "medium"

        code = generate_code()
        user_id = database.create_user(
            username, email, generate_password_hash(password), code, risk
        )
        send_confirmation_email(email, code)
        return user_id, code, None

    except Exception as error:
        return None, None, f"Could not create the account: {error}"


def verify_code(email, code):
    """Check the confirmation code. Returns (user, error_message)."""
    try:
        user = database.get_user_by_email((email or "").strip().lower())
        if not user:
            return None, "No account found for that email address."
        if user["is_verified"]:
            return user, None  # already verified, just let them through
        if not code or code.strip() != str(user["confirmation_code"]):
            return None, "That confirmation code is not correct."

        database.mark_verified(user["id"])
        return database.get_user_by_id(user["id"]), None

    except Exception as error:
        return None, f"Could not verify the account: {error}"


def login(identifier, password):
    """
    Log in with either a username or an email address.
    Returns (user, error_message).
    """
    try:
        identifier = (identifier or "").strip()
        if not identifier or not password:
            return None, "Please enter your username and password."

        user = (database.get_user_by_username(identifier)
                or database.get_user_by_email(identifier.lower()))

        # Same message for both failures, so an attacker cannot use this
        # page to discover which usernames exist.
        if not user or not check_password_hash(user["password_hash"], password):
            return None, "Incorrect username or password."

        if not user["is_verified"]:
            return None, "UNVERIFIED"

        return user, None

    except Exception as error:
        return None, f"Could not log in: {error}"
