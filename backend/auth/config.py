"""
auth/config.py
==============
Hardcoded users + JWT utilities + role-based agent mapping.

Roles:
  admin     — sees everything, all agents, all PDFs
  reviewer1 — LinkedIn + Tax/Credit agents
  reviewer2 — YouTube + Income agents
"""

import os
import jwt
import bcrypt
from datetime import datetime, timedelta
from typing import Optional

# ── JWT config ─────────────────────────────────────────────
JWT_SECRET    = os.getenv("JWT_SECRET", "verixa-super-secret-key-2026")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_H  = 8  # hours

# ── Hardcoded users ────────────────────────────────────────
# Passwords are bcrypt hashed — plaintext stored only here for reference
# admin:Admin@2026  reviewer1:Review1@2026  reviewer2:Review2@2026

def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

USERS = {
    "admin@verixa.com": {
        "user_id":      "USR_001",
        "name":         "Verixa Admin",
        "email":        "admin@verixa.com",
        "role":         "admin",
        "password_hash": "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TIxrpVOmaITFnF.PjD8dDN5kJOGe",
        "db_schema":    "admin",
        "color":        "#1A56A8",
    },
    "reviewer1@verixa.com": {
        "user_id":      "USR_002",
        "name":         "Priya Investigator",
        "email":        "reviewer1@verixa.com",
        "role":         "reviewer1",
        "password_hash": "$2b$12$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uSrnZOEzO",
        "db_schema":    "reviewer1",
        "color":        "#D85A30",
    },
    "reviewer2@verixa.com": {
        "user_id":      "USR_003",
        "name":         "Rahul Analyst",
        "email":        "reviewer2@verixa.com",
        "role":         "reviewer2",
        "password_hash": "$2b$12$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uSrnZOEzO",
        "db_schema":    "reviewer2",
        "color":        "#BA7517",
    },
}

# Plaintext passwords for dev (remove in prod)
USER_PASSWORDS = {
    "admin@verixa.com":     "Admin@2026",
    "reviewer1@verixa.com": "Review1@2026",
    "reviewer2@verixa.com": "Review2@2026",
}

# ── Role → agents mapping ──────────────────────────────────
ROLE_AGENTS = {
    "admin": [
        "web_scraping",
        "sql_analysis",
        "knowledge_graph",
        "linkedin",
        "itr_credit",
        "youtube",
        "income",
        "risk_synthesis",
    ],
    "reviewer1": [
        "web_scraping",
        "sql_analysis",
        "linkedin",
        "itr_credit",
        "risk_synthesis",
    ],
    "reviewer2": [
        "web_scraping",
        "sql_analysis",
        "youtube",
        "income",
        "risk_synthesis",
    ],
}

# ── Role → PDF sections ────────────────────────────────────
ROLE_PDF_SECTIONS = {
    "admin": [
        "executive_summary",
        "transaction_analysis",
        "network_graph",
        "linkedin_profile",
        "itr_credit",
        "youtube_channel",
        "income_credentials",
        "risk_score",
        "recommendations",
    ],
    "reviewer1": [
        "executive_summary",
        "transaction_analysis",
        "linkedin_profile",
        "itr_credit",
        "risk_score",
        "recommendations",
    ],
    "reviewer2": [
        "executive_summary",
        "transaction_analysis",
        "youtube_channel",
        "income_credentials",
        "risk_score",
        "recommendations",
    ],
}

# ── Agent display labels ───────────────────────────────────
AGENT_LABELS = {
    "web_scraping":    "Web Intelligence",
    "sql_analysis":    "Transaction Analytics",
    "knowledge_graph": "Network Graph",
    "linkedin":        "LinkedIn Profile",
    "itr_credit":      "Tax and Credit Risk",
    "youtube":         "YouTube Channel",
    "income":          "Income Credentials",
    "risk_synthesis":  "Risk Synthesis",
}

# ── JWT utils ──────────────────────────────────────────────

def create_token(user: dict) -> str:
    payload = {
        "user_id":   user["user_id"],
        "email":     user["email"],
        "role":      user["role"],
        "name":      user["name"],
        "db_schema": user["db_schema"],
        "exp":       datetime.utcnow() + timedelta(hours=JWT_EXPIRY_H),
        "iat":       datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise ValueError("Token expired — please login again")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        # Fallback for dev — direct compare
        return plain == USER_PASSWORDS.get(
            next((u["email"] for u in USERS.values() if u["password_hash"] == hashed), ""), ""
        )


def authenticate(email: str, password: str) -> Optional[dict]:
    user = USERS.get(email.lower())
    if not user:
        return None
    # Dev shortcut — direct password match
    if password == USER_PASSWORDS.get(email.lower()):
        return user
    # Bcrypt verify
    if verify_password(password, user["password_hash"]):
        return user
    return None


def get_user_from_token(token: str) -> Optional[dict]:
    payload = decode_token(token)
    return USERS.get(payload.get("email", ""))