# schema.py — Pydantic request/response models for Fraud Analytics API

from pydantic import BaseModel


class InvestigateRequest(BaseModel):
    """Request model for investigation endpoint."""
    entity_id: str
    scope: str = "full"


class LoginRequest(BaseModel):
    """Request model for login endpoint."""
    email: str
    password: str


class PDFRequest(BaseModel):
    """Request model for PDF generation."""
    entity_id:  str
    session_id: int = None