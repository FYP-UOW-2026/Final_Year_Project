"""Password rules, shared by the CLI and every GUI form.

Mirrors backend/src/utils/passwordPolicy.js -- change both together. The server is the
authority; this only gives instant feedback before a request is sent.
"""
from typing import Optional

MIN_LENGTH = 12
HINT = "At least 12 characters with upper, lower, a digit and a symbol"

COMMON = {
    "password", "passw0rd", "p@ssw0rd", "p@ssword", "qwerty", "qwertyuiop", "letmein",
    "welcome", "admin", "administrator", "iloveyou", "monkey", "dragon", "football",
    "baseball", "abc123", "trustno1", "sunshine", "princess", "changeme", "bioaudit",
    "123456", "12345678", "123456789", "1234567890", "111111", "000000", "asdfgh",
    "zxcvbn", "starwars", "whatever", "freedom", "master", "login", "hello",
}


def password_strength_problem(password: str) -> Optional[str]:
    """Return what is wrong with the password, or None if it is acceptable."""
    if len(password) < MIN_LENGTH:
        return f"Use a password of at least {MIN_LENGTH} characters."
    if not any(c.islower() for c in password):
        return "Add a lowercase letter to the password."
    if not any(c.isupper() for c in password):
        return "Add an uppercase letter to the password."
    if not any(c.isdigit() for c in password):
        return "Add a digit to the password."
    if all(c.isalnum() for c in password):
        return "Add a symbol (such as ! ? # $) to the password."
    letters = "".join(c for c in password.lower() if c.isalpha())
    for word in COMMON:
        if word.isalpha() and len(word) >= 6 and letters == word:
            return "That password is too common. Choose something less guessable."
    if password.lower() in COMMON or len(set(password)) <= 3:
        return "That password is too common. Choose something less guessable."
    return None
