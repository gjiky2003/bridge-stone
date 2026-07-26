"""BridgeStone Capital — Investor Blueprint"""
import logging
from datetime import datetime, timezone
from flask import (
    Blueprint, render_template, redirect, url_for, request,
    flash, jsonify
)
from flask_login import login_required, current_user

from models import db, User, Borrower, Property, Deal, Loan, Draw, Payment, Investor, Investment

logger = logging.getLogger(__name__)

investor_bp = Blueprint('investor', __name__)


def _get_investor():
    """Return the Investor row for current_user, or None + flash + redirect."""
    if not current_user.is_authenticated or current_user.role != 'investor':
        flash('Access restricted to investors.', 'error')
        return None
    investor = Investor.query.filter_by(user_id=current_user.id).first()
    if not investor:
        flash('Investor profile not found.', 'error')
        return None
    return investor


# ================================================================
# Dashboard — portfolio value, returns, active investments
# ================================================================
@investor_bp.route('/dashboard')
@login_required
def dashboard():
    investor = _get_investor()
    if investor is None:
        return redirect(url_for('landing'))

    # Active investments
    active_investments = Investment.query.filter_by(
        investor_id=investor.id
    ).order_by(Investment.created_at.desc()).all()

    total_invested = sum((inv.amount or 0) for inv in active_investments)
    total_returns = sum((inv.total_return or 0) for inv in active_investments)
    portfolio_value = total_invested + total_returns
    overall_roi = round(total_returns / total_invested, 4) if total_invested > 0 else 0

    # Active count
    active_count = sum(1 for inv in active_investments if inv.status == 'active')

    # Recent investments (last 5)
    recent = sorted(active_investments, key=lambda i: i.created_at or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True)[:5]

    stats = {
        'total_invested': total_invested,
        'total_returns': total_returns,
        'portfolio_value': portfolio_value,
        'overall_roi': overall_roi,
        'active_count': active_count,
        'total_count': len(active_investments),
        'available_balance': investor.available_balance or 0,
        'total_committed': investor.total_committed or 0,
    }

    return render_template('investor/dashboard.html',
                           investor=investor,
                           stats=stats,
                           recent_investments=recent,
                           all_investments=active_investments)


# ================================================================
# Pipeline — new deals available for funding
# ================================================================
@investor_bp.route('/pipeline')
@login_required
def pipeline():
    investor = _get_investor()
    if investor is None:
        return redirect(url_for('landing'))

    # Deals that are approved/in_closing — available for funding
    available_deals = Deal.query.filter(
        Deal.status.in_(['approved', 'in_closing', 'active'])
    ).order_by(Deal.approved_at.desc()).all()

    # Already committed to by this investor
    my_commitments = Investment.query.filter_by(
        investor_id=investor.id
    ).all()
    committed_deal_ids = {inv.loan_id: inv for inv in my_commitments}

    # Enrich deals with loan info
    for deal in available_deals:
        deal._loan = Loan.query.filter_by(deal_id=deal.id).first()

    return render_template('investor/pipeline.html',
                           investor=investor,
                           available_deals=available_deals,
                           committed_deal_ids=committed_deal_ids)


# ================================================================
# Commit capital to a deal
# ================================================================
@investor_bp.route('/commit/<int:deal_id>', methods=['POST'])
@login_required
def commit(deal_id):
    investor = _get_investor()
    if investor is None:
        return redirect(url_for('landing'))

    deal = Deal.query.get_or_404(deal_id)

    if deal.status not in ('approved', 'in_closing', 'active'):
        flash('This deal is not currently open for investment.', 'error')
        return redirect(url_for('investor.pipeline'))

    try:
        amount = float(request.form.get('amount', 0) or 0)
    except ValueError:
        flash('Invalid investment amount.', 'error')
        return redirect(url_for('investor.pipeline'))

    if amount <= 0:
        flash('Investment amount must be positive.', 'error')
        return redirect(url_for('investor.pipeline'))

    if amount > (investor.available_balance or 0):
        flash(f'Insufficient balance. Available: ${investor.available_balance:,.2f}', 'error')
        return redirect(url_for('investor.pipeline'))

    # Find or create a loan for this deal
    loan = Loan.query.filter_by(deal_id=deal.id).first()
    if not loan:
        flash('No loan record exists for this deal yet.', 'error')
        return redirect(url_for('investor.pipeline'))

    # Check if there's already an investment for this loan
    existing = Investment.query.filter_by(
        investor_id=investor.id, loan_id=loan.id
    ).first()

    if existing:
        # Add to existing investment
        existing.amount = (existing.amount or 0) + amount
        flash(f'Increased investment in Deal #{deal.id} by ${amount:,.2f}.', 'success')
    else:
        inv = Investment(
            investor_id=investor.id,
            loan_id=loan.id,
            amount=amount,
            status='active',
        )
        db.session.add(inv)
        flash(f'Committed ${amount:,.2f} to Deal #{deal.id}.', 'success')

    # Update investor balances
    investor.total_committed = (investor.total_committed or 0) + amount
    investor.total_invested = (investor.total_invested or 0) + amount
    investor.available_balance = (investor.available_balance or 0) - amount
    db.session.commit()

    return redirect(url_for('investor.pipeline'))


# ================================================================
# Portfolio — all investments + returns
# ================================================================
@investor_bp.route('/portfolio')
@login_required
def portfolio():
    investor = _get_investor()
    if investor is None:
        return redirect(url_for('landing'))

    investments = Investment.query.filter_by(
        investor_id=investor.id
    ).order_by(Investment.created_at.desc()).all()

    total_invested = sum((inv.amount or 0) for inv in investments)
    total_returns = sum((inv.total_return or 0) for inv in investments)

    # Enrich each investment with loan and deal info
    enriched = []
    for inv in investments:
        loan = Loan.query.get(inv.loan_id)
        deal = Deal.query.get(loan.deal_id) if loan else None
        property_info = deal.property if deal else None
        enriched.append({
            'investment': inv,
            'loan': loan,
            'deal': deal,
            'property': property_info,
        })

    # Aggregate metrics
    active_investments = [inv for inv in investments if inv.status == 'active']
    completed_investments = [inv for inv in investments if inv.status != 'active']

    metrics = {
        'total_invested': total_invested,
        'total_returns': total_returns,
        'net_value': total_invested + total_returns,
        'overall_roi': round(total_returns / total_invested, 4) if total_invested > 0 else 0,
        'active_count': len(active_investments),
        'completed_count': len(completed_investments),
        'total_count': len(investments),
    }

    return render_template('investor/portfolio.html',
                           investor=investor,
                           enriched=enriched,
                           metrics=metrics)
