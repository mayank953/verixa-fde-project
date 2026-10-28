"""
pdf/generator.py — Per-user role-based PDF reports using ReportLab.

Fixes applied:
  - Rupee symbol rendered via TTF font (NotoSans) with Latin fallback
  - Fraud indicators pulled correctly from findings_json
  - Fraud summary included
  - File SAR correct
  - SQL/transaction findings shown
  - Recommendations populated
  - Duplicate risk flags deduplicated
  - PageBreak only between major section groups, not every section
  - City + monthly_income now expected from caller (fix validate_customer in api.py)

Roles:
  admin     -> all sections
  reviewer1 -> linkedin + itr_credit + core
  reviewer2 -> youtube + income + core
"""
import os
import logging
from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = logging.getLogger("pdf_generator")

PDF_BASE_DIR = os.getenv("PDF_OUTPUT_DIR", "/tmp/verixa_pdfs")

# ── Font setup ─────────────────────────────────────────────
# Register NotoSans for full Unicode support.
# Falls back gracefully to Helvetica if font files are absent.
_FONT_NORMAL = "Helvetica"
_FONT_BOLD   = "Helvetica-Bold"

def _register_fonts():
    global _FONT_NORMAL, _FONT_BOLD
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    candidates_bold = [
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]
    reg = bold = None
    for p in candidates:
        if os.path.exists(p):
            reg = p; break
    for p in candidates_bold:
        if os.path.exists(p):
            bold = p; break
    if reg:
        try:
            pdfmetrics.registerFont(TTFont("NotoSans", reg))
            _FONT_NORMAL = "NotoSans"
        except Exception:
            pass
    if bold:
        try:
            pdfmetrics.registerFont(TTFont("NotoSans-Bold", bold))
            _FONT_BOLD = "NotoSans-Bold"
        except Exception:
            pass

_register_fonts()

# ── Helpers ────────────────────────────────────────────────
def _fmt_money(val):
    """Format a number as a USD string."""
    try:
        n = float(val or 0)
        return f"${n:,.0f}"
    except Exception:
        return "$0"

def _safe(val, fallback="N/A"):
    if val is None or val == "" or val == []:
        return fallback
    return str(val)

def _dedup(lst):
    """Deduplicate a list while preserving order."""
    seen = set()
    out  = []
    for item in (lst or []):
        key = str(item).strip()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out

# ── Colours ────────────────────────────────────────────────
ROLE_COLORS = {
    "admin":     colors.HexColor("#1A56A8"),
    "reviewer1": colors.HexColor("#D85A30"),
    "reviewer2": colors.HexColor("#BA7517"),
}
RISK_COLORS = {
    "CRITICAL": colors.HexColor("#C0392B"),
    "HIGH":     colors.HexColor("#E67E22"),
    "MEDIUM":   colors.HexColor("#F39C12"),
    "LOW":      colors.HexColor("#27AE60"),
    "UNKNOWN":  colors.HexColor("#95A5A6"),
}
ROLE_SECTIONS = {
    "admin":     ["summary", "transactions", "linkedin", "itr_credit", "youtube", "income", "recommendations"],
    "reviewer1": ["summary", "transactions", "linkedin", "itr_credit", "recommendations"],
    "reviewer2": ["summary", "transactions", "youtube", "income", "recommendations"],
}

# Sections that should trigger a page break BEFORE them
PAGE_BREAK_BEFORE = {"linkedin", "youtube", "recommendations"}


def _path(role, entity_id, user_id):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d  = Path(PDF_BASE_DIR) / role
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"{entity_id}_{user_id}_{ts}.pdf")


def _styles(role):
    accent = ROLE_COLORS.get(role, ROLE_COLORS["admin"])
    fn  = _FONT_NORMAL
    fb  = _FONT_BOLD
    return {
        "accent": accent,
        "h1":    ParagraphStyle("h1",   fontSize=12, textColor=accent,
                                fontName=fb, spaceBefore=12, spaceAfter=5),
        "h2":    ParagraphStyle("h2",   fontSize=10, textColor=colors.HexColor("#222222"),
                                fontName=fb, spaceBefore=7, spaceAfter=3),
        "body":  ParagraphStyle("body", fontSize=8.5, textColor=colors.HexColor("#333333"),
                                fontName=fn, spaceAfter=3, leading=13),
        "flag":  ParagraphStyle("flag", fontSize=8.5, textColor=colors.HexColor("#C0392B"),
                                fontName=fn, spaceAfter=3, leftIndent=10),
        "good":  ParagraphStyle("good", fontSize=8.5, textColor=colors.HexColor("#1E7E34"),
                                fontName=fn, spaceAfter=3, leftIndent=10),
        "title": ParagraphStyle("ttl",  fontSize=18, textColor=accent,
                                fontName=fb, spaceAfter=5),
        "sub":   ParagraphStyle("sub",  fontSize=8.5, textColor=colors.HexColor("#666666"),
                                fontName=fn, spaceAfter=3),
        "date":  ParagraphStyle("date", fontSize=8,   textColor=colors.HexColor("#999999"),
                                fontName=fn, spaceAfter=10),
        "empty": ParagraphStyle("emp",  fontSize=8.5, textColor=colors.HexColor("#27AE60"),
                                fontName=fn, spaceAfter=3),
        "warn":  ParagraphStyle("warn", fontSize=8.5, textColor=colors.HexColor("#888888"),
                                fontName=fn, spaceAfter=3, leftIndent=10),
    }


def _header_footer(canvas, doc, role, entity_id, uname):
    canvas.saveState()
    ac = ROLE_COLORS.get(role, ROLE_COLORS["admin"])
    # Header bar
    canvas.setFillColor(ac)
    canvas.rect(1*cm, A4[1]-1.5*cm, A4[0]-2*cm, 0.52*cm, fill=True, stroke=False)
    canvas.setFillColor(colors.white)
    canvas.setFont(_FONT_BOLD if _FONT_BOLD != "Helvetica-Bold" else "Helvetica-Bold", 8)
    canvas.drawString(1.3*cm, A4[1]-1.18*cm, "VERIXA BANK  -  Fraud Intelligence Unit")
    canvas.setFont(_FONT_NORMAL if _FONT_NORMAL != "Helvetica" else "Helvetica", 8)
    canvas.drawRightString(A4[0]-1.3*cm, A4[1]-1.18*cm, f"CONFIDENTIAL  -  {uname.upper()}")
    # Footer
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.setFont("Helvetica", 7)
    canvas.drawString(1*cm, 0.65*cm,
        f"Entity: {entity_id}  |  {datetime.now().strftime('%d %b %Y %H:%M')}")
    canvas.drawRightString(A4[0]-1*cm, 0.65*cm, f"Page {doc.page}  |  Internal Use Only")
    canvas.restoreState()


def _kv_table(rows, st):
    """Key-value info table."""
    data = [
        [Paragraph(f"<b>{k}</b>", st["body"]),
         Paragraph(_safe(v), st["body"])]
        for k, v in rows
    ]
    t = Table(data, colWidths=[5*cm, 12.5*cm])
    t.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.white, colors.HexColor("#F8F9FA")]),
        ("TOPPADDING",     (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",  (0,0), (-1,-1), 4),
        ("LEFTPADDING",    (0,0), (-1,-1), 6),
        ("RIGHTPADDING",   (0,0), (-1,-1), 6),
        ("GRID",           (0,0), (-1,-1), 0.3, colors.HexColor("#E0E0E0")),
    ]))
    return t


def _indicators_table(inds, st):
    """Fraud indicators table."""
    if not inds:
        return Paragraph("No fraud indicators detected.", st["empty"])
    # Deduplicate
    seen = set()
    unique = []
    for i in inds:
        if isinstance(i, dict):
            key = (i.get("source",""), i.get("flag",""), i.get("description",""))
        else:
            key = str(i)
        if key not in seen:
            seen.add(key)
            unique.append(i)

    data = [["Source", "Flag", "Description"]]
    for i in unique[:30]:
        if isinstance(i, dict):
            source_text = _safe(i.get("source"), "")
            flag_text = _safe(i.get("flag"), "")
            desc_text = _safe(i.get("description", ""))
            
            # Wrap long texts in Paragraph for proper text wrapping
            data.append([
                source_text,
                Paragraph(flag_text, st["body"]) if len(flag_text) > 50 else flag_text,
                Paragraph(desc_text, st["body"]) if len(desc_text) > 100 else desc_text,
            ])
        else:
            text_str = str(i)
            data.append(["", "", Paragraph(text_str, st["body"]) if len(text_str) > 100 else text_str])

    t = Table(data, colWidths=[2*cm, 5*cm, 10.5*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  colors.HexColor("#2C3E50")),
        ("TEXTCOLOR",     (0,0), (-1,0),  colors.white),
        ("FONTNAME",      (0,0), (-1,0),  _FONT_BOLD),
        ("FONTNAME",      (0,1), (-1,-1), _FONT_NORMAL),
        ("FONTSIZE",      (0,0), (-1,-1), 7.5),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.white, colors.HexColor("#F8F9FA")]),
        ("GRID",          (0,0), (-1,-1), 0.3, colors.HexColor("#DDDDDD")),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING",   (0,0), (-1,-1), 5),
        ("RIGHTPADDING",  (0,0), (-1,-1), 5),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ("ALIGN",         (0,1), (1,-1), "LEFT"),
        ("ALIGN",         (2,1), (2,-1), "LEFT"),
    ]))
    return t


def _risk_badge(risk_level, risk_score, st):
    rc  = RISK_COLORS.get(risk_level, RISK_COLORS["UNKNOWN"])
    ws  = ParagraphStyle("w", fontSize=16, textColor=colors.white,
                         fontName=_FONT_BOLD, alignment=TA_CENTER)
    score_str = f"{float(risk_score or 0):.3f}"
    t = Table(
        [[Paragraph(f"<b>{risk_level}</b>", ws),
          Paragraph(f"<b>{score_str}</b>", ws)]],
        colWidths=[8*cm, 8*cm]
    )
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (0,0), rc),
        ("BACKGROUND",    (1,0), (1,0), colors.HexColor("#2C3E50")),
        ("TOPPADDING",    (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 10),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("ROUNDEDCORNERS",(0,0), (-1,-1), [4,4,4,4]),
    ]))
    return t


# ══════════════════════════════════════════════════════════
# SECTION BUILDERS
# ══════════════════════════════════════════════════════════

def sec_summary(result, customer, st):
    e = []
    e.append(Paragraph("Executive Summary", st["h1"]))
    e.append(HRFlowable(width="100%", thickness=1, color=st["accent"]))
    e.append(Spacer(1, 6))
    e.append(_risk_badge(result.get("risk_level", "UNKNOWN"), result.get("risk_score", 0), st))
    e.append(Spacer(1, 10))

    file_sar = result.get("file_sar", False)
    # Coerce various truthy forms
    if isinstance(file_sar, str):
        file_sar = file_sar.lower() in ("true", "yes", "1")

    e.append(_kv_table([
        ("Customer ID",    customer.get("customer_id", "")),
        ("Name",           customer.get("name", "")),
        ("Email",          customer.get("email", "")),
        ("City",           customer.get("city", "N/A")),
        ("Occupation",     customer.get("occupation", "N/A")),
        ("Monthly Income", _fmt_money(customer.get("monthly_income", 0))),
        ("Risk Level",     result.get("risk_level", "UNKNOWN")),
        ("Risk Score",     f"{float(result.get('risk_score', 0)):.3f}"),
        ("File SAR",       "YES - Immediate action required" if file_sar else "No"),
        ("Monitoring",     f"{result.get('monitoring_days', 30)} days"),
        ("Agents Run",     ", ".join(result.get("completed_steps", []) or [])),
    ], st))

    # Fraud Summary
    summary = result.get("fraud_summary", "")
    if summary:
        e.append(Spacer(1, 8))
        e.append(Paragraph("Fraud Summary", st["h2"]))
        e.append(Paragraph(summary, st["body"]))

    return e


def sec_transactions(result, st):
    e = []
    e.append(Paragraph("Transaction Analysis", st["h1"]))
    e.append(HRFlowable(width="100%", thickness=1, color=st["accent"]))
    e.append(Spacer(1, 6))

    sql = result.get("sql_findings") or {}
    if sql:
        anomaly_flags = sql.get("anomaly_flags") or []
        e.append(_kv_table([
            ("Total Transactions", _safe(sql.get("transaction_count"))),
            ("Total Amount",       _fmt_money(sql.get("total_amount", 0))),
            ("Anomaly Flags",      ", ".join(anomaly_flags) if anomaly_flags else "None"),
            ("SQL Risk Score",     _safe(sql.get("sql_risk_score"))),
        ], st))
        e.append(Spacer(1, 8))
    else:
        e.append(Paragraph("No SQL transaction data available for this investigation.", st["warn"]))
        e.append(Spacer(1, 8))

    e.append(Paragraph("Fraud Indicators", st["h2"]))

    # Pull indicators — they may live in multiple places depending on how the session was saved
    indicators = result.get("fraud_indicators") or []
    # Also check inside findings sub-keys
    if not indicators:
        findings = result.get("findings", {}) or {}
        indicators = findings.get("fraud_indicators", [])

    e.append(_indicators_table(indicators, st))
    return e


def sec_linkedin(result, st):
    li = result.get("linkedin_findings") or {}
    e  = []
    e.append(Paragraph("LinkedIn Profile Analysis", st["h1"]))
    e.append(HRFlowable(width="100%", thickness=1, color=st["accent"]))
    e.append(Spacer(1, 6))

    if not li:
        e.append(Paragraph("LinkedIn data not available for this investigation.", st["warn"]))
        return e

    e.append(_kv_table([
        ("Profile Found",  "Yes" if li.get("found") else "No / Private"),
        ("Name",           _safe(li.get("name"))),
        ("Headline",       _safe(li.get("headline"))),
        ("Connections",    _safe(li.get("connections"))),
        ("Jobs Listed",    _safe(li.get("employment_count"))),
        ("LinkedIn Score", _safe(li.get("linkedin_score"))),
    ], st))

    risk_flags = _dedup(li.get("risk_flags") or [])
    if risk_flags:
        e.append(Spacer(1, 6))
        e.append(Paragraph("Risk Flags", st["h2"]))
        for f in risk_flags:
            e.append(Paragraph(f"! {f}", st["flag"]))

    income_signals = _dedup(li.get("income_signals") or [])
    if income_signals:
        e.append(Spacer(1, 4))
        e.append(Paragraph("Income Signals", st["h2"]))
        for s in income_signals:
            e.append(Paragraph(f"+ {s}", st["good"]))

    return e


def sec_itr(result, st):
    itr = result.get("itr_credit_findings") or {}
    e   = []
    e.append(Paragraph("Tax and Credit Risk Analysis", st["h1"]))
    e.append(HRFlowable(width="100%", thickness=1, color=st["accent"]))
    e.append(Spacer(1, 6))

    if not itr:
        e.append(Paragraph("Tax and credit data not available.", st["warn"]))
        return e

    e.append(_kv_table([
        ("Declared Monthly Income", _fmt_money(itr.get("declared_income", 0))),
        ("Annual Income Est.",      _fmt_money(itr.get("annual_income_est", 0))),
        ("Transaction Volume",      _fmt_money(itr.get("txn_volume", 0))),
        ("Tax and Credit Score",      f"{float(itr.get('itr_credit_score', 0) or 0):.2f}"),
    ], st))

    if itr.get("five_year_assessment"):
        e.append(Spacer(1, 6))
        e.append(Paragraph("5-Year Assessment", st["h2"]))
        for line in (itr["five_year_assessment"] or []):
            e.append(Paragraph(f"- {line}", st["body"]))

    risk_flags = _dedup(itr.get("risk_flags") or [])
    if risk_flags:
        e.append(Spacer(1, 6))
        e.append(Paragraph("Risk Flags", st["h2"]))
        for f in risk_flags:
            e.append(Paragraph(f"! {f}", st["flag"]))

    income_consistency = _dedup(itr.get("income_consistency") or [])
    if income_consistency:
        e.append(Spacer(1, 4))
        e.append(Paragraph("Income Consistency", st["h2"]))
        for s in income_consistency:
            e.append(Paragraph(f"+ {s}", st["good"]))

    return e


def sec_youtube(result, st):
    yt = result.get("youtube_findings") or {}
    e  = []
    e.append(Paragraph("YouTube Channel Analysis", st["h1"]))
    e.append(HRFlowable(width="100%", thickness=1, color=st["accent"]))
    e.append(Spacer(1, 6))

    if not yt:
        e.append(Paragraph("YouTube data not available.", st["warn"]))
        return e

    e.append(_kv_table([
        ("Channel Found",            "Yes" if yt.get("found") else "No"),
        ("Channel Name",             _safe(yt.get("channel_name"))),
        ("Subscribers",              f"{int(yt.get('subscribers', 0) or 0):,}"),
        ("Total Videos",             _safe(yt.get("total_videos"))),
        ("Monetized",                "Yes" if yt.get("monetized") else "No"),
        ("Est. Monthly Income (USD)", _safe(yt.get("income_range_usd"))),
    ], st))

    risk_flags = _dedup(yt.get("risk_flags") or [])
    if risk_flags:
        e.append(Spacer(1, 6))
        e.append(Paragraph("Risk Flags", st["h2"]))
        for f in risk_flags:
            e.append(Paragraph(f"! {f}", st["flag"]))

    income_signals = _dedup(yt.get("income_signals") or [])
    if income_signals:
        e.append(Spacer(1, 4))
        e.append(Paragraph("Income Signals", st["h2"]))
        for s in income_signals:
            e.append(Paragraph(f"+ {s}", st["good"]))

    return e


def sec_income(result, st):
    inc = result.get("income_findings") or {}
    e   = []
    e.append(Paragraph("Income Credentials Analysis", st["h1"]))
    e.append(HRFlowable(width="100%", thickness=1, color=st["accent"]))
    e.append(Spacer(1, 6))

    if not inc:
        e.append(Paragraph("Income data not available.", st["warn"]))
        return e

    e.append(_kv_table([
        ("Declared Monthly Income", _fmt_money(inc.get("declared_monthly_income", 0))),
        ("Occupation",              _safe(inc.get("occupation"))),
        ("Credential Score",        f"{float(inc.get('income_credential_score', 0) or 0):.2f}"),
    ], st))

    good_credentials = _dedup(inc.get("good_credentials") or [])
    if good_credentials:
        e.append(Spacer(1, 6))
        e.append(Paragraph("Good Credentials", st["h2"]))
        for c in good_credentials:
            e.append(Paragraph(f"+ {c}", st["good"]))

    lifestyle_signals = _dedup(inc.get("lifestyle_signals") or [])
    if lifestyle_signals:
        e.append(Spacer(1, 4))
        e.append(Paragraph("Lifestyle Signals", st["h2"]))
        for s in lifestyle_signals:
            e.append(Paragraph(f"- {s}", st["body"]))

    risk_flags = _dedup(inc.get("risk_flags") or [])
    if risk_flags:
        e.append(Spacer(1, 6))
        e.append(Paragraph("Risk Flags", st["h2"]))
        for f in risk_flags:
            e.append(Paragraph(f"! {f}", st["flag"]))

    return e


def sec_recommendations(result, st):
    e = []
    e.append(Paragraph("Recommendations & Next Steps", st["h1"]))
    e.append(HRFlowable(width="100%", thickness=1, color=st["accent"]))
    e.append(Spacer(1, 6))

    recs = result.get("recommendations") or []
    if not recs:
        e.append(Paragraph("No specific recommendations generated.", st["warn"]))
    else:
        for i, rec in enumerate(recs, 1):
            e.append(Paragraph(f"{i}. {rec}", st["body"]))
            e.append(Spacer(1, 3))

    # SAR notice
    file_sar = result.get("file_sar", False)
    if isinstance(file_sar, str):
        file_sar = file_sar.lower() in ("true", "yes", "1")
    if file_sar:
        e.append(Spacer(1, 10))
        sar_style = ParagraphStyle("sar", fontSize=9, textColor=colors.HexColor("#C0392B"),
                                   fontName=_FONT_BOLD, backColor=colors.HexColor("#FDECEA"),
                                   borderPadding=8, spaceAfter=4)
        e.append(Paragraph(
            "ACTION REQUIRED: Suspicious Activity Report (SAR) must be filed immediately "
            "with the Financial Intelligence Unit.", sar_style))

    return e


BUILDERS = {
    "summary":         sec_summary,
    "transactions":    sec_transactions,
    "linkedin":        sec_linkedin,
    "itr_credit":      sec_itr,
    "youtube":         sec_youtube,
    "income":          sec_income,
    "recommendations": sec_recommendations,
}


# ══════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════

def generate_pdf(result, customer, user_role, user_id, user_name):
    """Generate role-specific PDF. Returns file path."""
    entity_id = result.get("entity_id", "UNKNOWN")
    out       = _path(user_role, entity_id, user_id)
    st        = _styles(user_role)

    doc = SimpleDocTemplate(
        out, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=2.5*cm,  bottomMargin=2*cm,
    )

    story = [Spacer(1, 0.3*cm)]

    # Cover header
    story.append(Paragraph("FRAUD INVESTIGATION REPORT", st["title"]))
    story.append(Paragraph(
        f"Entity: {entity_id}  |  Investigator: {user_name}  |  Role: {user_role.upper()}",
        st["sub"]
    ))
    story.append(Paragraph(
        f"Generated: {datetime.now().strftime('%d %B %Y, %H:%M')}",
        st["date"]
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=st["accent"]))
    story.append(Spacer(1, 0.3*cm))

    sections = ROLE_SECTIONS.get(user_role, ROLE_SECTIONS["admin"])

    for i, sec in enumerate(sections):
        fn = BUILDERS.get(sec)
        if not fn:
            continue

        # Page break before certain sections to keep them clean
        if i > 0 and sec in PAGE_BREAK_BEFORE:
            story.append(PageBreak())

        # Build section content
        if sec == "summary":
            content = fn(result, customer, st)
        else:
            content = fn(result, st)

        # Wrap small sections in KeepTogether so they don't orphan
        if len(content) <= 8:
            story.append(KeepTogether(content))
        else:
            story.extend(content)

        story.append(Spacer(1, 14))

    hf = lambda c, d: _header_footer(c, d, user_role, entity_id, user_name)
    doc.build(story, onFirstPage=hf, onLaterPages=hf)

    sz = round(os.path.getsize(out) / 1024, 1)
    logger.info(f"PDF generated: {out} ({sz} KB)")
    return out