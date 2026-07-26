"""
BridgeStone Capital — Origination Automation (automation/origination.py)

OriginationAutomator
  - generate_document_checklist(deal) -> list of required docs
  - generate_closing_timeline(deal) -> step-by-step timeline with dates
  - send_term_sheet_email(deal, to_email) -> prints HTML email to console
  - auto_collect_documents(deal, borrower_email) -> sends checklist email
  - generate_payoff_statement(loan, payoff_date) -> full payoff breakdown

Auto-triggers after admin approves a deal.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from underwriting.pricing import PointsCalculator, TIER_DAILY_RATES

logger = logging.getLogger(__name__)


class OriginationAutomator:
    """Automate deal origination — checklists, timelines, emails, payoff statements."""

    @staticmethod
    def generate_document_checklist(deal: Any) -> List[Dict[str, str]]:
        checklist = [
            {"doc_name": "Purchase Contract / HUD-1", "status": "required",
             "description": "Fully executed purchase agreement for the subject property"},
            {"doc_name": "Entity Documentation", "status": "required",
             "description": "LLC operating agreement, articles of organization, EIN confirmation letter"},
            {"doc_name": "Scope of Work", "status": "required",
             "description": "Detailed scope of work with line-item budget for all planned renovations"},
            {"doc_name": "Contractor Bid", "status": "required",
             "description": "Signed contractor bid matching the scope of work. Licensed & insured contractor required"},
            {"doc_name": "Proof of Insurance", "status": "required",
             "description": "ACORD 25 Certificate of Liability + Builder's Risk, naming BridgeStone as additional insured"},
            {"doc_name": "Title Commitment", "status": "required",
             "description": "Title commitment showing clear title (or identifying encumbrances to be resolved)"},
            {"doc_name": "Bank Statements", "status": "required",
             "description": "Last 2 months personal and/or business bank statements showing liquidity"},
            {"doc_name": "Credit Authorization", "status": "required",
             "description": "Signed authorization for BridgeStone to pull credit report"},
            {"doc_name": "Borrower Questionnaire", "status": "required",
             "description": "Completed borrower questionnaire: experience, entity structure, project plan"},
            {"doc_name": "Government-Issued ID", "status": "required",
             "description": "Copy of driver's license or passport for all principals with 20%+ ownership"},
        ]
        if deal.product_type == "bridge":
            checklist.extend([
                {"doc_name": "Rehab Draw Schedule", "status": "required",
                 "description": "Proposed draw schedule with milestones and estimated completion dates"},
                {"doc_name": "Before Photos", "status": "required",
                 "description": "Current condition photos of all rooms in the subject property"},
            ])
        if deal.product_type == "dscr":
            checklist.extend([
                {"doc_name": "Rent Roll / Lease Agreements", "status": "required",
                 "description": "Current rent roll and copies of active lease agreements"},
                {"doc_name": "Property Operating Statement", "status": "required",
                 "description": "Trailing 12-month P&L for the property (if stabilized)"},
            ])
        if getattr(deal, 'financing_type', '') == "cross_collateral":
            checklist.extend([
                {"doc_name": "Collateral Property Deed", "status": "required",
                 "description": "Copy of deed for the cross-collateral property"},
                {"doc_name": "Collateral Title Report", "status": "required",
                 "description": "Title report for the collateral property showing lien position"},
                {"doc_name": "Collateral Property Insurance", "status": "required",
                 "description": "Insurance certificate for collateral property naming BridgeStone as loss payee"},
            ])
        return checklist

    @staticmethod
    def generate_closing_timeline(deal: Any) -> List[Dict[str, Any]]:
        today = date.today()
        return [
            {"step": "Term Sheet Issued", "estimated_date": today, "days_from_now": 0,
             "description": "Signed term sheet returned by borrower with commitment fee"},
            {"step": "Document Collection Deadline", "estimated_date": today + timedelta(days=1), "days_from_now": 1,
             "description": "All required documents due: entity docs, insurance, title, bank statements, SOW + bid"},
            {"step": "Underwriting File Review", "estimated_date": today + timedelta(days=1), "days_from_now": 1,
             "description": "Underwriter reviews complete file, verifies documents, orders appraisal if needed"},
            {"step": "Valuation / Appraisal Ordered", "estimated_date": today + timedelta(days=2), "days_from_now": 2,
             "description": "Desktop or drive-by appraisal ordered; BPO accepted for loans under $250K"},
            {"step": "Clear to Close", "estimated_date": today + timedelta(days=3), "days_from_now": 3,
             "description": "Final underwriting sign-off. Closing documents prepared and sent to title company"},
            {"step": "Closing / Funding", "estimated_date": today + timedelta(days=4), "days_from_now": 4,
             "description": "Borrower signs closing docs at title company. Wire transfer initiated same day"},
            {"step": "Post-Closing / Draw Setup", "estimated_date": today + timedelta(days=5), "days_from_now": 5,
             "description": "Rehab draw schedule confirmed. First draw available after closing (if applicable)"},
        ]

    @staticmethod
    def send_term_sheet_email(deal: Any, to_email: str) -> Dict[str, Any]:
        deal_data = {
            "borrower_name": deal.borrower.entity_name if deal.borrower else "Borrower",
            "property_address": deal.property.address if deal.property else "",
            "loan_amount": deal.loan_amount or 0,
            "interest_rate": deal.approved_rate or 10.5,
            "risk_tier": deal.risk_tier or "C",
            "term_months": deal.approved_term_months or 12,
            "exit_strategy": deal.exit_strategy or "sale",
            "financing_type": getattr(deal, 'financing_type', 'down_payment') or 'down_payment',
        }
        points_type = getattr(deal, 'points_type', 'upfront') or 'upfront'
        term_sheet = PointsCalculator.generate_term_sheet(deal_data, points_type=points_type)
        html_body = _build_term_sheet_html(term_sheet, deal)

        logger.info("=" * 60)
        logger.info("TERM SHEET EMAIL — TO: %s", to_email)
        logger.info("SUBJECT: BridgeStone Capital — Term Sheet for %s", deal_data["property_address"])
        logger.info("=" * 60)
        logger.info(html_body)
        logger.info("=" * 60)

        return {"status": "sent", "to": to_email, "term_sheet": term_sheet, "html_body": html_body}

    @staticmethod
    def auto_collect_documents(deal: Any, borrower_email: str) -> Dict[str, Any]:
        checklist = OriginationAutomator.generate_document_checklist(deal)
        timeline = OriginationAutomator.generate_closing_timeline(deal)
        html_body = _build_document_request_html(deal, checklist, timeline)

        logger.info("=" * 60)
        logger.info("DOCUMENT COLLECTION EMAIL — TO: %s", borrower_email)
        logger.info("SUBJECT: BridgeStone Capital — Required Documents for Deal #%d", deal.id)
        logger.info("=" * 60)
        logger.info(html_body)
        logger.info("=" * 60)

        return {"status": "sent", "to": borrower_email, "checklist": checklist, "timeline": timeline, "html_body": html_body}

    @staticmethod
    def generate_payoff_statement(loan: Any, payoff_date_obj: date) -> Dict[str, Any]:
        origination = loan.origination_date
        if isinstance(origination, datetime):
            origination = origination.date()
        if origination is None:
            origination = payoff_date_obj
        days_outstanding = max(0, (payoff_date_obj - origination).days)

        principal = loan.current_balance or loan.original_amount or 0
        annual_rate = loan.interest_rate or 0
        daily_interest = (principal * (annual_rate / 100.0)) / 365.0 if annual_rate > 0 else 0
        accrued_interest = round(daily_interest * days_outstanding, 2)

        daily_points = 0.0
        daily_rate = 0.0
        if hasattr(loan, 'deal') and loan.deal:
            deal = loan.deal
            if getattr(deal, 'points_type', '') == "daily":
                daily_rate = getattr(deal, 'daily_points_rate', 0) or PointsCalculator.suggest_daily_rate(principal, deal.risk_tier or "C")
                daily_points = PointsCalculator.calc_daily_points(principal, daily_rate, days_outstanding)

        days_late = loan.days_late or 0
        late_fee = 0.0
        if days_late > 0:
            monthly_payment = loan.monthly_payment or PointsCalculator.calc_monthly_io_payment(principal, annual_rate)
            late_fee = round(monthly_payment * 0.05 * (days_late // 15 + 1), 2)

        total_payoff = round(principal + accrued_interest + daily_points + late_fee, 2)

        return {
            "loan_id": loan.id, "borrower": loan.borrower_id,
            "payoff_date": payoff_date_obj.isoformat(), "days_outstanding": days_outstanding,
            "principal_balance": round(principal, 2), "accrued_interest": accrued_interest,
            "annual_interest_rate": annual_rate, "daily_interest_amount": round(daily_interest, 2),
            "daily_points_rate": daily_rate, "daily_points_accrued": round(daily_points, 2),
            "late_fees": round(late_fee, 2), "total_payoff": total_payoff,
            "good_through_date": (payoff_date_obj + timedelta(days=7)).isoformat(),
            "per_diem_after": round(daily_interest + (daily_rate * principal if daily_rate else 0), 2),
        }

    @staticmethod
    def on_approval(deal: Any, borrower_email: str) -> Dict[str, Any]:
        results = {}
        try:
            results["term_sheet"] = OriginationAutomator.send_term_sheet_email(deal, borrower_email)
            logger.info("Term sheet sent for deal #%d", deal.id)
        except Exception as exc:
            logger.error("Term sheet failed for deal #%d: %s", deal.id, exc)
            results["term_sheet"] = {"status": "failed", "error": str(exc)}
        try:
            results["doc_collection"] = OriginationAutomator.auto_collect_documents(deal, borrower_email)
            logger.info("Document collection sent for deal #%d", deal.id)
        except Exception as exc:
            logger.error("Doc collection failed for deal #%d: %s", deal.id, exc)
            results["doc_collection"] = {"status": "failed", "error": str(exc)}
        return results


# ═══════════════════════════════════════════════════════
# HTML email builders (internal helpers)
# ═══════════════════════════════════════════════════════

def _build_term_sheet_html(term_sheet: Dict[str, Any], deal: Any) -> str:
    from html import escape
    docs_html = "".join(f"<li>{escape(d)}</li>" for d in term_sheet.get("required_docs", []))
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>
  body {{ font-family: system-ui, sans-serif; color: #1a1a1a; max-width: 640px; margin: 0 auto; }}
  .header {{ background: #1a365d; color: white; padding: 24px; border-radius: 12px 12px 0 0; }}
  .header h1 {{ margin: 0; font-size: 20px; }}
  .header .sub {{ font-size: 13px; opacity: 0.8; margin-top: 4px; }}
  .body {{ background: white; border: 1px solid #e5e7eb; border-top: none; padding: 24px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #f0f0f0; font-size: 14px; }}
  td:first-child {{ color: #6b7280; width: 180px; font-weight: 500; }}
  td:last-child {{ font-weight: 600; color: #1a1a1a; }}
  .section {{ margin-top: 24px; }}
  .section h3 {{ font-size: 14px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }}
  ul {{ list-style: none; padding: 0; }}
  ul li {{ padding: 4px 0; font-size: 14px; }}
  ul li::before {{ content: '✓ '; color: #059669; font-weight: bold; }}
  .footer {{ background: #f9fafb; padding: 20px 24px; border-radius: 0 0 12px 12px; border: 1px solid #e5e7eb; border-top: none; font-size: 12px; color: #6b7280; }}
</style></head>
<body>
  <div class="header">
    <h1>BridgeStone Capital — Term Sheet</h1>
    <div class="sub">Deal #{deal.id} · Generated {date.today().isoformat()}</div>
  </div>
  <div class="body">
    <p style="font-size:14px;margin:0 0 16px 0">Dear {escape(term_sheet.get('borrower_name', 'Borrower'))},</p>
    <p style="font-size:14px;color:#4b5563;">Congratulations! Your deal has been approved. Below are the proposed terms. Please review, sign, and return this term sheet along with the commitment fee to proceed to closing.</p>
    <table>
      <tr><td>Property</td><td>{escape(term_sheet.get('property_address', ''))}</td></tr>
      <tr><td>Loan Amount</td><td>${term_sheet.get('loan_amount', 0):,.0f}</td></tr>
      <tr><td>Interest Rate</td><td>{term_sheet.get('interest_rate', 0):.2f}%</td></tr>
      <tr><td>Points</td><td>{escape(term_sheet.get('points_display', ''))}</td></tr>
      <tr><td>Monthly Payment (I/O)</td><td>${term_sheet.get('monthly_payment', 0):,.2f}</td></tr>
      <tr><td>Term</td><td>{term_sheet.get('term_months', 12)} months</td></tr>
      <tr><td>Financing Type</td><td>{escape(str(term_sheet.get('financing_type', '')).replace('_', ' ').title())}</td></tr>
      <tr><td>Exit Strategy</td><td>{escape(str(term_sheet.get('exit_strategy', '')).replace('_', ' ').title())}</td></tr>
      <tr><td>Prepayment Penalty</td><td>{escape(term_sheet.get('prepayment_penalty', ''))}</td></tr>
      <tr><td>Rate Lock</td><td>{escape(term_sheet.get('rate_lock', ''))}</td></tr>
      <tr><td>Extension Policy</td><td>{escape(term_sheet.get('extension_policy', ''))}</td></tr>
      <tr><td>Closing Timeline</td><td>{escape(term_sheet.get('closing_timeline', ''))}</td></tr>
    </table>
    <div class="section">
      <h3>Required Documents</h3>
      <ul>{docs_html}</ul>
    </div>
    <p style="font-size:14px;color:#4b5563;margin-top:24px;">Please contact your BridgeStone loan officer with any questions. We look forward to closing your deal quickly.</p>
    <p style="font-size:14px;color:#1a365d;font-weight:600;">— BridgeStone Capital</p>
  </div>
  <div class="footer">
    This term sheet is a non-binding expression of interest. Final terms are subject to underwriting approval, satisfactory appraisal, and clear title. BridgeStone Capital is an equal opportunity lender.
  </div>
</body>
</html>"""


def _build_document_request_html(deal: Any, checklist: List[Dict], timeline: List[Dict]) -> str:
    from html import escape
    checklist_html = "".join(
        f'<li><strong>{escape(d["doc_name"])}</strong><br><span style="color:#6b7280;font-size:12px">{escape(d["description"])}</span></li>'
        for d in checklist
    )
    timeline_html = "".join(
        f'<tr><td>{escape(t["step"])}</td><td>{t["estimated_date"]}</td><td style="color:#6b7280;font-size:12px">{escape(t["description"])}</td></tr>'
        for t in timeline
    )
    prop_addr = escape(deal.property.address if deal.property else '')
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>
  body {{ font-family: system-ui, sans-serif; color: #1a1a1a; max-width: 640px; margin: 0 auto; }}
  .header {{ background: #059669; color: white; padding: 24px; border-radius: 12px 12px 0 0; }}
  .header h1 {{ margin: 0; font-size: 20px; }}
  .body {{ background: white; border: 1px solid #e5e7eb; border-top: none; padding: 24px; }}
  .section {{ margin-top: 24px; }}
  .section h3 {{ font-size: 14px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }}
  ul {{ list-style: none; padding: 0; }}
  ul li {{ padding: 8px 0; border-bottom: 1px solid #f0f0f0; font-size: 14px; }}
  ul li:last-child {{ border-bottom: none; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #f0f0f0; font-size: 13px; }}
  th {{ text-align: left; padding: 8px 10px; font-size: 12px; color: #6b7280; text-transform: uppercase; }}
  .footer {{ background: #f9fafb; padding: 20px 24px; border-radius: 0 0 12px 12px; border: 1px solid #e5e7eb; border-top: none; font-size: 12px; color: #6b7280; }}
</style></head>
<body>
  <div class="header">
    <h1>BridgeStone Capital — Required Documents</h1>
    <div style="font-size:13px;opacity:0.8;margin-top:4px">Deal #{deal.id} · {prop_addr}</div>
  </div>
  <div class="body">
    <p style="font-size:14px;color:#4b5563;">Please provide the following documents to proceed with closing. Our standard timeline is <strong>3–4 business days</strong> from receipt of all documents.</p>
    <div class="section">
      <h3>Document Checklist</h3>
      <ul>{checklist_html}</ul>
    </div>
    <div class="section">
      <h3>Estimated Closing Timeline</h3>
      <table>
        <thead><tr><th>Step</th><th>Date</th><th>Details</th></tr></thead>
        <tbody>{timeline_html}</tbody>
      </table>
    </div>
  </div>
  <div class="footer">
    Please upload documents via your BridgeStone portal or reply to this email. Missing documents will delay closing. Contact your loan officer with questions.
  </div>
</body>
</html>"""
