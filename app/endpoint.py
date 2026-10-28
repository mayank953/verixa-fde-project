# endpoint.py — All FastAPI route handlers for Fraud Analytics API
#
# Routes:
#   POST /api/auth/login            → login and get JWT token
#   GET  /api/auth/me               → get current user info
#   GET  /api/auth/logout           → logout (client-side)
#   GET  /api/health               → health check
#   GET  /api/validate/{entity_id} → check if customer exists in DB
#   POST /api/run                  → run investigation via MCP, return results
#   POST /api/eval/run             → run DeepEval bulk evaluation (blocking)
#   POST /api/eval/start           → start DeepEval job (SSE mode)
#   GET  /api/eval/stream/{job_id} → SSE stream for eval progress
#   GET  /api/admin/sessions       → admin: get all review sessions
#   GET  /api/admin/pdfs           → admin: get all PDF reports
#   POST /api/pdf/generate         → generate PDF report for investigation
#   GET  /api/pdf/my-reports       → get user's PDF reports

from __future__ import annotations
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import asyncio
import asyncio as _asyncio
import json
import logging
import time
import traceback
import uuid
from datetime import datetime
from typing import AsyncGenerator, Dict

import json as _json
from deepeval import evaluate
from deepeval.test_case import LLMTestCase
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.evals.metrics import get_metrics
from utils.helpers import _get_db_connection
from schema import InvestigateRequest, LoginRequest, PDFRequest
from utils.helpers import validate_customer, _flatten_indicators, _mcp_investigate
from backend.auth.config import (
    authenticate, create_token, decode_token,
    ROLE_AGENTS, AGENT_LABELS
)
from backend.pdf.generator import generate_pdf
from fastapi.responses import FileResponse
import os
from backend.logger import GLOBAL_LOGGER as logger



router = APIRouter()

# ── Security ──────────────────────────────────────────────────
security = HTTPBearer(auto_error=False)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """JWT middleware — extracts user from Bearer token."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
        return payload
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

def get_optional_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Optional auth — returns None if no token."""
    if not credentials:
        return None
    try:
        return decode_token(credentials.credentials)
    except Exception:
        return None

# ── In-memory eval job store ───────────────────────────────────
_eval_jobs: Dict[str, Dict] = {}


# ══════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ══════════════════════════════════════════════════════════════

@router.post("/api/auth/login")
async def login(req: LoginRequest):
    """Login — returns JWT token + user info."""
    user = authenticate(req.email.strip().lower(), req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_token(user)
    return {
        "token": token,
        "user": {
            "user_id": user["user_id"],
            "name":    user["name"],
            "email":   user["email"],
            "role":    user["role"],
            "color":   user["color"],
        },
        "agents_available": ROLE_AGENTS.get(user["role"], []),
        "agent_labels": {
            k: AGENT_LABELS[k]
            for k in ROLE_AGENTS.get(user["role"], [])
            if k in AGENT_LABELS
        },
    }


@router.get("/api/auth/me")
async def me(current_user: dict = Depends(get_current_user)):
    """Get current user info from JWT."""
    return {
        "user_id":          current_user["user_id"],
        "name":             current_user["name"],
        "email":            current_user["email"],
        "role":             current_user["role"],
        "agents_available": ROLE_AGENTS.get(current_user["role"], []),
    }


@router.get("/api/auth/logout")
async def logout():
    """Logout — client should discard token."""
    return {"message": "Logged out successfully"}


# ══════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS
# ══════════════════════════════════════════════════════════════

@router.get("/api/admin/sessions")
async def admin_sessions(current_user: dict = Depends(get_current_user)):
    """Admin only — get all review sessions."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        
        conn = _get_db_connection()
        cur  = conn.cursor()
        cur.execute("""
            SELECT rs.session_id, rs.user_id, rs.entity_id, rs.status,
                   rs.risk_score, rs.risk_level, rs.started_at, rs.completed_at,
                   u.name AS reviewer_name, u.role
            FROM review_sessions rs
            LEFT JOIN users u ON rs.user_id = u.user_id
            ORDER BY rs.started_at DESC
            LIMIT 100
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
        return {"sessions": [
            {
                "session_id":     r[0], "user_id":      r[1],
                "entity_id":      r[2], "status":       r[3],
                "risk_score":     float(r[4]) if r[4] else None,
                "risk_level":     r[5],  "started_at":   str(r[6]),
                "completed_at":   str(r[7]) if r[7] else None,
                "reviewer_name":  r[8],  "role":         r[9],
            }
            for r in rows
        ]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")


@router.get("/api/admin/pdfs")
async def admin_pdfs(current_user: dict = Depends(get_current_user)):
    """Admin only — get all PDF reports."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        
        conn = _get_db_connection()
        cur  = conn.cursor()
        cur.execute("""
            SELECT p.pdf_id, p.session_id, p.user_id, p.entity_id,
                   p.filename, p.storage_path, p.file_size_kb, p.created_at,
                   u.name AS reviewer_name
            FROM pdf_reports p
            LEFT JOIN users u ON p.user_id = u.user_id
            ORDER BY p.created_at DESC
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
        return {"pdfs": [
            {
                "pdf_id":        r[0], "session_id":    r[1],
                "user_id":       r[2], "entity_id":     r[3],
                "filename":      r[4], "storage_path":  r[5],
                "file_size_kb":  float(r[6]) if r[6] else None,
                "created_at":    str(r[7]), "reviewer_name": r[8],
            }
            for r in rows
        ]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")


# ══════════════════════════════════════════════════════════════
# HEALTH & VALIDATION
# ══════════════════════════════════════════════════════════════

@router.get("/api/health")
async def health():
    """Health check — used by frontend status badge."""
    from backend import providers
    return {"status": "ok", "external_mode": providers.external_mode(), "ts": datetime.utcnow().isoformat() + "Z"}


@router.get("/api/validate/{entity_id}")
async def validate_entity(entity_id: str):
    """Check if customer exists before running investigation."""
    customer = validate_customer(entity_id.upper())
    if not customer:
        raise HTTPException(status_code=404, detail=f"Customer '{entity_id}' not found in database.")
    return {"exists": True, "customer": customer}


# ══════════════════════════════════════════════════════════════
# INVESTIGATION
# ══════════════════════════════════════════════════════════════

@router.post("/api/run")
async def run_investigation(req: InvestigateRequest, current_user: dict = Depends(get_optional_user)):
    """Run investigation via MCP — single blocking call, returns full result."""
    entity_id = req.entity_id.strip().upper()

    # Default to admin role if no JWT (backward compat)
    user_role  = (current_user or {}).get("role",      "admin")
    user_id    = (current_user or {}).get("user_id",   "USR_001")
    db_schema  = (current_user or {}).get("db_schema", "admin")

    customer = validate_customer(entity_id)
    if not customer:
        raise HTTPException(status_code=404, detail=f"Customer '{entity_id}' not found in database.")

    try:
        result = await _mcp_investigate(
            entity_id=entity_id,
            scope=req.scope,
            user_role=user_role,
            user_id=user_id,
            db_schema=db_schema,
            customer_info=customer
        )
    except Exception as e:
        
        logger.error(f"[/api/run] {entity_id} failed:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

    flat = _flatten_indicators(result.get("fraud_indicators", []))

    # ── Save session to DB ────────────────────────────
    try:
        
        conn = _get_db_connection()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO review_sessions
                (user_id, entity_id, scope, status, risk_score, risk_level,
                 agents_run, findings_json, completed_at)
            VALUES (%s,%s,%s,'completed',%s,%s,%s,%s,NOW())
        """, (
            user_id, entity_id, req.scope,
            result.get("risk_score"),
            result.get("risk_level"),
            result.get("completed_steps", []),
            json.dumps({
                "fraud_summary":   result.get("fraud_summary",""),
                "fraud_indicators": result.get("fraud_indicators",[]),
                "recommendations":  result.get("recommendations",[]),
                "file_sar":         result.get("file_sar",False),
                "linkedin":        result.get("linkedin_findings"),
                "youtube":         result.get("youtube_findings"),
                "itr_credit":      result.get("itr_credit_findings"),
                "income":          result.get("income_findings"),
                "knowledge_graph":  result.get("knowledge_graph"),
            })
        ))
        conn.commit()
        cur.close(); conn.close()
    except Exception as db_err:
        logger.warning(f"Session save failed: {db_err}")

    return {
        "entity_id":        entity_id,
        "customer":         customer,
        "user_role":        user_role,
        "risk_score":       result.get("risk_score", 0),
        "risk_level":       result.get("risk_level", "UNKNOWN"),
        "fraud_indicators": flat,
        "fraud_summary":    result.get("fraud_summary", ""),
        "recommendations":  result.get("recommendations", []),
        "file_sar":         result.get("file_sar", False),
        "monitoring_days":  result.get("monitoring_days", 30),
        "completed_steps":  result.get("completed_steps", []),
        "report":           result.get("report", ""),
        "errors":           result.get("errors", []),
        # Role-specific findings
        "linkedin_findings":  result.get("linkedin_findings"),
        "youtube_findings":   result.get("youtube_findings"),
        "itr_credit_findings": result.get("itr_credit_findings"),
        "income_findings":    result.get("income_findings"),
        "knowledge_graph":    result.get("knowledge_graph"),
    }



# ══════════════════════════════════════════════════════════════
# DEEPEVAL HELPERS
# ══════════════════════════════════════════════════════════════

def _push_eval(job_id: str, event: str, data: Dict):
    """Push an event into the eval job's event queue."""
    if job_id in _eval_jobs:
        _eval_jobs[job_id]["events"].append({"event": event, "data": data})


async def _run_deepeval(job_id: str):
    """Run DeepEval bulk evaluation in a background thread."""
    def _blocking_eval():
        try:
            test_entities = [
                ("CUST_0300", "CRITICAL"),
                ("CUST_0042", "CRITICAL"),
                ("CUST_0001", "LOW"),
            ]

            _push_eval(job_id, "eval_start", {"total": len(test_entities)})

            cases = []
            for i, (entity_id, expected_level) in enumerate(test_entities):
                _push_eval(job_id, "entity_start", {"entity_id": entity_id, "index": i + 1})
                try:
                    loop   = _asyncio.new_event_loop()
                    result = loop.run_until_complete(_mcp_investigate(entity_id, "full"))
                    loop.close()

                    flat_str = [
                        f"[{x.get('source','')}] {x.get('flag','')}: {x.get('description','')}"
                        if isinstance(x, dict) else str(x)
                        for x in (result.get("fraud_indicators") or [])
                    ]

                    tc = LLMTestCase(
                        input=f"Full fraud risk assessment for {entity_id}",
                        actual_output=json.dumps({
                            "risk_level":       result.get("risk_level"),
                            "risk_score":       result.get("risk_score"),
                            "fraud_indicators": flat_str,
                            "recommendations":  result.get("recommendations", []),
                            "file_sar":         result.get("file_sar"),
                        }),
                        expected_output=json.dumps({
                            "risk_level":        expected_level,
                            "file_sar_expected": expected_level in ("HIGH", "CRITICAL"),
                        }),
                        context=[
                            f"Expected: {expected_level}",
                            f"Got: {result.get('risk_level')}",
                            f"Indicators: {len(flat_str)}",
                        ],
                    )
                    cases.append((entity_id, expected_level, tc))
                    _push_eval(job_id, "entity_done", {
                        "entity_id": entity_id,
                        "risk_level": result.get("risk_level"),
                        "status": "ok",
                    })
                except Exception as e:
                    _push_eval(job_id, "entity_done", {
                        "entity_id": entity_id, "status": "error", "error": str(e)
                    })

            if not cases:
                _push_eval(job_id, "eval_error", {"message": "No test cases collected."})
                return

            _push_eval(job_id, "metrics_start", {"message": "Running Gemini judge evaluation…"})
            eval_results = evaluate(
                test_cases=[tc for _, _, tc in cases],
                metrics=get_metrics(),
            )

            passed = 0
            total  = len(eval_results.test_results)
            metric_scores: Dict[str, list] = {}
            test_results_out = []

            for tr, (entity_id, expected_level, _) in zip(eval_results.test_results, cases):
                tp = all(m.success for m in tr.metrics_data)
                if tp:
                    passed += 1
                mout = []
                for m in tr.metrics_data:
                    mn = getattr(m, "name", type(m).__name__)
                    ms = round(float(m.score), 4) if m.score is not None else 0.0
                    metric_scores.setdefault(mn, []).append(ms)
                    mout.append({
                        "name": mn, "score": ms,
                        "passed": m.success, "reason": str(m.reason or "")[:300],
                    })
                test_results_out.append({
                    "entity_id": entity_id, "expected_level": expected_level,
                    "passed": tp, "metrics": mout,
                })

            _push_eval(job_id, "eval_complete", {
                "total":           total,
                "passed":          passed,
                "failed":          total - passed,
                "pass_rate":       round(passed / total * 100, 1) if total else 0,
                "metric_averages": {n: round(sum(s) / len(s) * 100, 1) for n, s in metric_scores.items()},
                "test_results":    test_results_out,
            })

        except Exception as e:
            
            logger.error(f"[DeepEval] Fatal:\n{traceback.format_exc()}")
            _push_eval(job_id, "eval_error", {"message": str(e)})
        finally:
            _eval_jobs[job_id]["status"] = "complete"

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _blocking_eval)


# ══════════════════════════════════════════════════════════════
# DEEPEVAL ROUTES
# ══════════════════════════════════════════════════════════════

@router.post("/api/eval/start")
async def start_eval(current_user: dict = Depends(get_current_user)):
    """Start a DeepEval bulk evaluation job (SSE mode)."""
    job_id = str(uuid.uuid4())[:8].upper()
    _eval_jobs[job_id] = {"status": "running", "events": []}
    asyncio.create_task(_run_deepeval(job_id))
    return {"job_id": job_id}


@router.get("/api/eval/stream/{job_id}")
async def stream_eval(job_id: str, current_user: dict = Depends(get_current_user)):
    """SSE stream for DeepEval progress."""
    if job_id not in _eval_jobs:
        raise HTTPException(status_code=404, detail="Eval job not found")

    async def gen() -> AsyncGenerator[str, None]:
        sent  = 0
        start = time.time()
        while time.time() - start < 900:
            events = _eval_jobs[job_id]["events"]
            while sent < len(events):
                ev = events[sent]
                yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'])}\n\n"
                sent += 1
            if _eval_jobs[job_id]["status"] == "complete":
                yield "event: stream_end\ndata: {}\n\n"
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.post("/api/eval/run")
async def run_eval_sync(current_user: dict = Depends(get_current_user)):
    """Run DeepEval bulk evaluation — blocking, returns full results."""
    def _do_eval():
        entities = [("CUST_0300", "CRITICAL"), ("CUST_0042", "CRITICAL"), ("CUST_0001", "LOW")]
        cases = []

        for entity_id, expected_level in entities:
            try:
                loop   = _asyncio.new_event_loop()
                result = loop.run_until_complete(_mcp_investigate(entity_id, "full"))
                loop.close()

                flat_str = [
                    f"[{x.get('source','')}] {x.get('flag','')}: {x.get('description','')}"
                    if isinstance(x, dict) else str(x)
                    for x in (result.get("fraud_indicators") or [])
                ]

                tc = LLMTestCase(
                    input=f"Full fraud risk assessment for {entity_id}",
                    actual_output=json.dumps({
                        "risk_level":       result.get("risk_level"),
                        "risk_score":       result.get("risk_score"),
                        "fraud_indicators": flat_str,
                        "recommendations":  result.get("recommendations", []),
                        "file_sar":         result.get("file_sar"),
                    }),
                    expected_output=json.dumps({
                        "risk_level":        expected_level,
                        "file_sar_expected": expected_level in ("HIGH", "CRITICAL"),
                    }),
                    context=[
                        f"Expected: {expected_level}",
                        f"Got: {result.get('risk_level')}",
                        f"Indicators: {len(flat_str)}",
                    ],
                )
                cases.append((entity_id, expected_level, tc))
            except Exception as e:
                logger.error(f"[eval/run] {entity_id} failed: {e}")

        valid = [(eid, elv, tc) for eid, elv, tc in cases if tc]
        if not valid:
            raise HTTPException(status_code=500, detail="No test cases collected.")

        eval_results = evaluate(
            test_cases=[tc for _, _, tc in valid],
            metrics=get_metrics(),
        )

        passed = 0
        total  = len(eval_results.test_results)
        metric_scores: Dict[str, list] = {}
        test_results_out = []

        for tr, (entity_id, expected_level, _) in zip(eval_results.test_results, valid):
            tp = all(m.success for m in tr.metrics_data)
            if tp:
                passed += 1
            mout = []
            for m in tr.metrics_data:
                mn = getattr(m, "name", type(m).__name__)
                ms = round(float(m.score), 4) if m.score is not None else 0.0
                metric_scores.setdefault(mn, []).append(ms)
                mout.append({
                    "name": mn, "score": ms,
                    "passed": m.success, "reason": str(m.reason or "")
                })
            test_results_out.append({
                "entity_id": entity_id, "expected_level": expected_level,
                "passed": tp, "metrics": mout,
            })

        return {
            "total":           total,
            "passed":          passed,
            "failed":          total - passed,
            "pass_rate":       round(passed / total * 100, 1) if total else 0,
            "metric_averages": {n: round(sum(s) / len(s) * 100, 1) for n, s in metric_scores.items()},
            "test_results":    test_results_out,
        }

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _do_eval)
    except HTTPException:
        raise
    except Exception as e:
        
        logger.error(f"[/api/eval/run] Fatal:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# PDF ROUTES
# ══════════════════════════════════════════════════════════════

@router.post("/api/pdf/generate")
async def generate_pdf_report(req: PDFRequest, current_user: dict = Depends(get_current_user)):
    """Generate role-specific PDF for an investigation."""
    

    entity_id = req.entity_id.strip().upper()
    user_role = current_user.get("role", "admin")
    user_id   = current_user.get("user_id", "USR_001")
    user_name = current_user.get("name", "Unknown")

    # Fetch last session result from DB
    try:
        # from utils.helpers import _get_db_connection
        conn = _get_db_connection()
        cur  = conn.cursor()
        cur.execute("""
            SELECT findings_json, risk_score, risk_level, agents_run
            FROM review_sessions
            WHERE entity_id = %s AND user_id = %s
            ORDER BY started_at DESC LIMIT 1
        """, (entity_id, user_id))
        row = cur.fetchone()
        cur.close(); conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")

    if not row:
        raise HTTPException(status_code=404,
            detail="No investigation found for this entity. Run investigation first.")

    
    raw = row[0]
    # psycopg2 returns JSONB as dict already — handle both cases
    if isinstance(raw, dict):
        findings = raw
    elif isinstance(raw, str):
        findings = _json.loads(raw)
    else:
        findings = {}
    customer   = validate_customer(entity_id) or {}

    result = {
        "entity_id":           entity_id,
        "risk_score":          float(row[1]) if row[1] else 0,
        "risk_level":          row[2] or "UNKNOWN",
        "completed_steps":     row[3] or [],
        "fraud_summary":       findings.get("fraud_summary",""),
        "fraud_indicators":    findings.get("fraud_indicators",[]),
        "recommendations":     findings.get("recommendations",[]),
        "file_sar":            findings.get("file_sar", False),
        "monitoring_days":     findings.get("monitoring_days", 30),
        "findings":            findings,  # Keep full findings for PDF generator
        "customer":            customer,
        "user_name":           user_name,
        "user_role":           user_role,
        # Role-specific findings
        "linkedin_findings":   findings.get("linkedin"),
        "youtube_findings":    findings.get("youtube"),
        "itr_credit_findings": findings.get("itr_credit"),
        "income_findings":     findings.get("income"),
        "knowledge_graph":     findings.get("knowledge_graph"),
    }

    # Generate PDF
    pdf_path = generate_pdf(result, customer, user_role, user_id, user_name)

    # Save to DB
    try:
        conn = _get_db_connection()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO pdf_reports
                (session_id, user_id, entity_id, filename, storage_path, file_size_kb)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (
            req.session_id,
            user_id,
            entity_id,
            os.path.basename(pdf_path),
            pdf_path,
            round(os.path.getsize(pdf_path) / 1024, 1) if os.path.exists(pdf_path) else 0,
        ))
        conn.commit()
        cur.close(); conn.close()
    except Exception as db_err:
        logger.warning(f"PDF save failed: {db_err}")

    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"fraud_report_{entity_id}_{user_role}.pdf"
    )


@router.get("/api/pdf/my-reports")
async def my_pdf_reports(current_user: dict = Depends(get_current_user)):
    """Get user's PDF reports."""
    user_id = current_user.get("user_id", "USR_001")
    try:
        # from utils.helpers import _get_db_connection
        conn = _get_db_connection()
        cur  = conn.cursor()
        cur.execute("""
            SELECT pdf_id, session_id, entity_id, filename, storage_path, file_size_kb, created_at
            FROM pdf_reports
            WHERE user_id = %s
            ORDER BY created_at DESC
        """, (user_id,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        return {"pdfs": [
            {
                "pdf_id":       r[0], "session_id":   r[1],
                "entity_id":    r[2], "filename":     r[3],
                "storage_path": r[4], "file_size_kb": float(r[5]) if r[5] else None,
                "created_at":   str(r[6]),
            }
            for r in rows
        ]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
