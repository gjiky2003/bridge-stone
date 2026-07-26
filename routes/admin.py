"""BridgeStone Capital — Admin Blueprint"""
import logging
from datetime import datetime, timezone, date
from flask import (
    Blueprint, render_template, redirect, url_for, request,
    flash, jsonify
)
from flask_login import login_required, current_user

from models import db, User, Borrower, Property, Deal, Loan, Draw, Payment, Investor, Investment

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)


def _require_admin():
    """Ensure current_user is an admin, flash + redirect if not."""
    if not current_user.is_authenticated or current_user.role != 'admin':
        flash('Access restricted to administrators.', 'error')
        return False
    return True


# ================================================================
# Dashboard — stats cards, recent activity
# ================================================================
@admin_bp.route('/dashboard')
@login_required
def dashboard():
    if not _require_admin():
        return redirect(url_for('landing'))

    # Stats cards
    active_loans_count = Loan.query.filter_by(status='active').count()
    total_active_balance = db.session.query(db.func.sum(Loan.current_balance))\
        .filter(Loan.status == 'active').scalar() or 0

    pipeline_count = Deal.query.filter(
        Deal.status.in_(['new', 'pre_screened', 'under_review'])
    ).count()
    pipeline_volume = db.session.query(db.func.sum(Deal.loan_amount))\
        .filter(Deal.status.in_(['new', 'pre_screened', 'under_review'])).scalar() or 0

    # Revenue: sum of origination + interest payments
    total_revenue = db.session.query(db.func.sum(Payment.amount))\
        .filter(Payment.status == 'paid',
                Payment.payment_type.in_(['interest', 'origination'])).scalar() or 0

    # NPL (non-performing loans) — loans with days_late > 30
    npl_count = Loan.query.filter(Loan.status == 'active', Loan.days_late > 30).count()
    npl_balance = db.session.query(db.func.sum(Loan.current_balance))\
        .filter(Loan.status == 'active', Loan.days_late > 30).scalar() or 0

    stats = {
        'active_loans': active_loans_count,
        'active_balance': total_active_balance,
        'pipeline_count': pipeline_count,
        'pipeline_volume': pipeline_volume,
        'total_revenue': total_revenue,
        'npl_count': npl_count,
        'npl_balance': npl_balance,
    }

    # Recent activity
    recent_deals = db.session.query(Deal).order_by(Deal.submitted_at.desc()).limit(10).all()
    recent_payments = db.session.query(Payment).order_by(Payment.created_at.desc()).limit(10).all()

    return render_template('admin/dashboard.html',
                           stats=stats,
                           recent_deals=recent_deals,
                           recent_payments=recent_payments)


# ================================================================
# Pipeline — Kanban view
# ================================================================
@admin_bp.route('/pipeline')
@login_required
def pipeline():
    if not _require_admin():
        return redirect(url_for('landing'))

    # Columns: New, Under Review, Approved, In Closing, Active, Paid Off, Defaulted
    columns = {
        'New': Deal.query.filter_by(status='new').order_by(Deal.submitted_at.desc()).all(),
        'Under Review': Deal.query.filter(Deal.status.in_(['pre_screened', 'under_review']))\
            .order_by(Deal.submitted_at.desc()).all(),
        'Approved': Deal.query.filter_by(status='approved').order_by(Deal.approved_at.desc()).all(),
        'In Closing': Deal.query.filter_by(status='in_closing').order_by(Deal.submitted_at.desc()).all(),
        'Active': Deal.query.filter(Deal.status.in_(['active', 'funded']))\
            .order_by(Deal.funded_at.desc()).all(),
        'Paid Off': Deal.query.filter_by(status='paid_off').order_by(Deal.paid_off_at.desc()).all(),
        'Defaulted': Deal.query.filter_by(status='defaulted').order_by(Deal.submitted_at.desc()).all(),
    }

    # Rejected separate
    rejected = Deal.query.filter_by(status='rejected').order_by(Deal.reviewed_at.desc()).all()

    return render_template('admin/pipeline.html',
                           columns=columns,
                           rejected=rejected)


# ================================================================
# Underwriting review — single deal
# ================================================================
@admin_bp.route('/review/<int:deal_id>')
@login_required
def review_deal(deal_id):
    if not _require_admin():
        return redirect(url_for('landing'))

    deal = Deal.query.get_or_404(deal_id)
    borrower = Borrower.query.get(deal.borrower_id)
    user = User.query.get(borrower.user_id) if borrower else None

    # Run AI scoring if not yet scored
    if deal.deal_score is None and deal.property:
        try:
            from underwriting.bridge_scorer import HardMoneyScorer
            scorer = HardMoneyScorer()
            result = scorer.score(deal.property.address, deal.product_type)
            deal.deal_score = result.get('score', 0)
            deal.risk_tier = result.get('tier', 'C')
            db.session.commit()
        except ImportError:
            logger.warning("HardMoneyScorer not available for admin review")
        except Exception as exc:
            logger.error("AI scoring failed in admin review: %s", exc)

    return render_template('admin/review.html',
                           deal=deal,
                           borrower=borrower,
                           user=user)


@admin_bp.route('/review/<int:deal_id>', methods=['POST'])
@login_required
def submit_review(deal_id):
    if not _require_admin():
        return redirect(url_for('landing'))

    deal = Deal.query.get_or_404(deal_id)

    decision = request.form.get('decision', '')

    try:
        approved_rate = float(request.form.get('approved_rate', 0) or 0)
    except ValueError:
        approved_rate = 0.0

    try:
        approved_points = float(request.form.get('approved_points', 0) or 0)
    except ValueError:
        approved_points = 0.0

    try:
        approved_term = int(request.form.get('approved_term_months', 0) or 0)
    except ValueError:
        approved_term = 0

    reason = request.form.get('decision_reason', '')
    notes = request.form.get('underwriter_notes', '')

    if decision == 'approved':
        if approved_rate <= 0 or approved_term <= 0:
            flash('Approved loans require a rate and term.', 'error')
            return redirect(url_for('admin.review_deal', deal_id=deal.id))
        deal.status = 'approved'
        deal.approved_rate = approved_rate
        deal.approved_points = approved_points
        deal.approved_term_months = approved_term
        deal.decision_reason = reason
        deal.underwriter_notes = notes
        deal.approved_at = datetime.now(timezone.utc)
        flash(f'Deal #{deal.id} approved at {approved_rate:.2f}% + {approved_points:.1f} pts.', 'success')

    elif decision == 'adjusted':
        deal.status = 'approved'
        deal.approved_rate = approved_rate or deal.approved_rate
        deal.approved_points = approved_points or deal.approved_points
        deal.approved_term_months = approved_term or deal.approved_term_months
        deal.decision_reason = reason
        deal.underwriter_notes = notes
        deal.approved_at = datetime.now(timezone.utc)
        flash(f'Deal #{deal.id} approved with adjusted terms.', 'success')

    elif decision == 'denied':
        deal.status = 'rejected'
        deal.decision_reason = reason
        deal.underwriter_notes = notes
        deal.reviewed_at = datetime.now(timezone.utc)
        flash(f'Deal #{deal.id} has been rejected.', 'warning')

    elif decision == 'request_info':
        deal.status = 'new'
        deal.decision_reason = reason
        deal.underwriter_notes = notes
        flash('Additional information requested from borrower.', 'info')

    else:
        flash('Invalid decision. Choose approved, adjusted, denied, or request_info.', 'error')
        return redirect(url_for('admin.review_deal', deal_id=deal.id))

    deal.reviewed_at = datetime.now(timezone.utc)
    db.session.commit()

    # Create Loan on approval if not present
    if decision in ('approved', 'adjusted') and deal.status == 'approved':
        existing_loan = Loan.query.filter_by(deal_id=deal.id).first()
        if not existing_loan:
            loan = Loan(
                deal_id=deal.id,
                borrower_id=deal.borrower_id,
                product_type=deal.product_type,
                status='pending',
                original_amount=deal.loan_amount,
                current_balance=deal.loan_amount,
                interest_rate=deal.approved_rate,
                origination_points=deal.approved_points,
                term_months=deal.approved_term_months,
                origination_date=date.today(),
            )
            db.session.add(loan)
            db.session.commit()

    return redirect(url_for('admin.pipeline'))


# ================================================================
# Portfolio — all active loans + metrics
# ================================================================
@admin_bp.route('/portfolio')
@login_required
def portfolio():
    if not _require_admin():
        return redirect(url_for('landing'))

    active_loans = Loan.query.filter_by(status='active').order_by(Loan.origination_date.desc()).all()
    all_loans = Loan.query.order_by(Loan.origination_date.desc()).all()

    total_balance = sum((l.current_balance or 0) for l in active_loans)
    total_original = sum((l.original_amount or 0) for l in active_loans)
    avg_rate = (sum((l.interest_rate or 0) for l in active_loans) / len(active_loans)
                if active_loans else 0)
    weighted_avg_rate = (
        sum((l.current_balance or 0) * (l.interest_rate or 0) for l in active_loans) / total_balance
        if total_balance > 0 else 0
    )

    # Delinquency
    delinquent = [l for l in active_loans if (l.days_late or 0) > 0]
    severe_delinquent = [l for l in active_loans if (l.days_late or 0) > 30]

    metrics = {
        'active_loan_count': len(active_loans),
        'total_balance': total_balance,
        'total_original': total_original,
        'avg_rate': round(avg_rate, 4),
        'weighted_avg_rate': round(weighted_avg_rate, 4),
        'delinquent_count': len(delinquent),
        'severe_delinquent_count': len(severe_delinquent),
        'delinquency_rate': round(len(delinquent) / len(active_loans), 4) if active_loans else 0,
    }

    return render_template('admin/portfolio.html',
                           active_loans=active_loans,
                           all_loans=all_loans,
                           metrics=metrics)


# ================================================================
# Deal detail (admin view)
# ================================================================
@admin_bp.route('/deal/<int:id>')
@login_required
def deal_detail(id):
    if not _require_admin():
        return redirect(url_for('landing'))

    deal = Deal.query.get_or_404(id)
    borrower = Borrower.query.get(deal.borrower_id)
    user = User.query.get(borrower.user_id) if borrower else None
    loan = Loan.query.filter_by(deal_id=deal.id).first()
    draws = Draw.query.filter_by(loan_id=loan.id).order_by(Draw.created_at.desc()).all() if loan else []
    payments = Payment.query.filter_by(loan_id=loan.id).order_by(Payment.due_date.desc()).all() if loan else []
    investments = Investment.query.filter_by(loan_id=loan.id).all() if loan else []

    return render_template('admin/deal_detail.html',
                           deal=deal,
                           borrower=borrower,
                           user=user,
                           loan=loan,
                           draws=draws,
                           payments=payments,
                           investments=investments)
