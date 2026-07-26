"""BridgeStone Capital — Borrower Blueprint"""
import logging
from datetime import datetime, timezone, date
from flask import (
    Blueprint, render_template, redirect, url_for, request,
    flash, session, jsonify
)
from flask_login import login_required, current_user

from models import db, User, Borrower, Property, Deal, Loan, Draw, Payment, Collateral

logger = logging.getLogger(__name__)

borrower_bp = Blueprint('borrower', __name__)

# ---------------------------------------------------------------------------
# HardMoneyScorer — try AI scorer, fall back to rule-based
# ---------------------------------------------------------------------------
try:
    from underwriting.bridge_scorer import HardMoneyScorer
    _has_ai_scorer = True
except ImportError:
    logger.warning("HardMoneyScorer not available — using rule-based fallback")
    _has_ai_scorer = False


def _get_borrower():
    """Return the Borrower row for current_user, or None + flash + redirect."""
    if not current_user.is_authenticated or current_user.role != 'borrower':
        flash('Access restricted to borrowers.', 'error')
        return None
    borrower = Borrower.query.filter_by(user_id=current_user.id).first()
    if not borrower:
        flash('Borrower profile not found.', 'error')
        return None
    return borrower


def _rule_based_prescreen(address, product_type):
    """Fallback rule-based prescreen when AI scorer is unavailable."""
    score = 50.0
    reasons = []
    tips = []
    if product_type == 'bridge':
        reasons.append("Bridge loan request — standard evaluation")
        tips.append("Provide detailed rehab scope and contractor bids for best terms")
        score = 55.0
    elif product_type == 'dscr':
        reasons.append("DSCR loan request — cash-flow based evaluation")
        tips.append("Property with strong rental history preferred")
        score = 60.0
    # Simple address length heuristic (rough proxy for address quality)
    if address and len(address) > 20:
        score = min(score + 5, 95)
        reasons.append("Complete address provided")
    else:
        reasons.append("Please provide a full property address for accurate evaluation")
    return {
        'score': round(score, 1),
        'tier': 'B' if score >= 60 else 'C',
        'reasons': reasons,
        'tips': tips,
        'source': 'rule-based',
    }


def _run_prescreen(address, product_type):
    """Run prescreen via AI scorer or rule-based fallback."""
    if _has_ai_scorer:
        try:
            scorer = HardMoneyScorer()
            result = scorer.score(address, product_type)
            result['source'] = 'ai'
            return result
        except Exception as exc:
            logger.warning("AI scorer failed: %s — falling back to rule-based", exc)
    return _rule_based_prescreen(address, product_type)


# ================================================================
# Dashboard
# ================================================================
@borrower_bp.route('/dashboard')
@login_required
def dashboard():
    borrower = _get_borrower()
    if borrower is None:
        return redirect(url_for('landing'))

    deals = Deal.query.filter_by(borrower_id=borrower.id)\
                     .order_by(Deal.submitted_at.desc()).all()

    # Attach loan data for each deal
    deals_with_loans = []
    from datetime import date as dt_date
    today = dt_date.today()
    for d in deals:
        loan = Loan.query.filter_by(deal_id=d.id).first()
        deal_info = {
            'deal': d,
            'loan': loan,
            'daily_points_accrued': 0,
            'auto_draft_enabled': False,
        }
        if loan and loan.points_type == 'daily':
            rate = loan.daily_points_rate or d.daily_points_rate or 0
            if loan.origination_date and rate:
                days = max(0, (today - loan.origination_date).days)
                deal_info['daily_points_accrued'] = round(days * rate, 2)
        if loan:
            deal_info['auto_draft_enabled'] = loan.auto_draft_enabled or False
        deals_with_loans.append(deal_info)

    active_deals = [d for d in deals if d.status in ('active', 'in_closing', 'funded')]
    pending_deals = [d for d in deals if d.status in ('new', 'pre_screened', 'under_review')]
    closed_deals = [d for d in deals if d.status in ('approved', 'rejected', 'paid_off', 'defaulted')]

    # Aggregate stats
    total_funded = sum((d.loan_amount or 0) for d in deals if d.status in ('active', 'funded', 'paid_off'))

    return render_template('borrower/dashboard.html',
                           borrower=borrower,
                           deals=deals_with_loans,
                           active_deals=active_deals,
                           pending_deals=pending_deals,
                           closed_deals=closed_deals,
                           total_funded=total_funded,
                           stats={'active_count': len(active_deals),
                                  'pending_count': len(pending_deals),
                                  'total_funded': total_funded})


# ================================================================
# Apply — multi-step deal submission
# ================================================================
@borrower_bp.route('/apply', methods=['GET', 'POST'])
@login_required
def apply():
    borrower = _get_borrower()
    if borrower is None:
        return redirect(url_for('landing'))

    step = request.args.get('step', '1')

    if request.method == 'POST':
        action = request.form.get('action', 'next')

        if action == 'next':
            # ---- Step 1 → Step 2: product_type + property ----
            if step == '1':
                product_type = request.form.get('product_type')
                address = request.form.get('address')
                city = request.form.get('city')
                state = request.form.get('state')
                zip_code = request.form.get('zip_code')
                property_type = request.form.get('property_type')

                if not product_type or not address:
                    flash('Product type and property address are required.', 'error')
                    return redirect(url_for('borrower.apply', step='1'))

                session['apply_step1'] = {
                    'product_type': product_type,
                    'address': address,
                    'city': city,
                    'state': state,
                    'zip_code': zip_code,
                    'property_type': property_type,
                }
                flash('Step 1 saved. Now enter deal details.', 'info')
                return redirect(url_for('borrower.apply', step='2'))

            # ---- Step 2 → Step 3: deal details ----
            elif step == '2':
                deal_data = {}
                try:
                    deal_data['purchase_price'] = float(request.form.get('purchase_price', 0) or 0)
                    deal_data['loan_amount'] = float(request.form.get('loan_amount', 0) or 0)
                    deal_data['arv_estimated'] = float(request.form.get('arv_estimated', 0) or 0)
                except ValueError:
                    flash('Please enter valid numeric values for financial fields.', 'error')
                    return redirect(url_for('borrower.apply', step='2'))

                deal_data['exit_strategy'] = request.form.get('exit_strategy', 'sale')
                deal_data['projected_hold_months'] = int(request.form.get('projected_hold_months', 6) or 6)

                # Financing structure
                deal_data['financing_type'] = request.form.get('financing_type', 'down_payment')
                deal_data['points_type'] = request.form.get('points_type', 'upfront')

                # Cross-collateral fields
                if deal_data['financing_type'] == 'cross_collateral':
                    deal_data['collateral_address'] = request.form.get('collateral_address', '')
                    try:
                        deal_data['collateral_value'] = float(request.form.get('collateral_value', 0) or 0)
                    except ValueError:
                        deal_data['collateral_value'] = 0
                    deal_data['collateral_free_clear'] = request.form.get('collateral_free_clear') == 'on'

                step1 = session.get('apply_step1', {})
                product_type = step1.get('product_type', 'bridge')

                if product_type == 'bridge':
                    try:
                        deal_data['rehab_budget'] = float(request.form.get('rehab_budget', 0) or 0)
                    except ValueError:
                        deal_data['rehab_budget'] = 0
                    deal_data['rehab_scope'] = request.form.get('rehab_scope', '')
                    deal_data['rehab_complexity'] = int(request.form.get('rehab_complexity', 1) or 1)
                    deal_data['contractor_name'] = request.form.get('contractor_name', '')
                    try:
                        deal_data['contractor_bid_amount'] = float(request.form.get('contractor_bid_amount', 0) or 0)
                    except ValueError:
                        deal_data['contractor_bid_amount'] = 0
                elif product_type == 'dscr':
                    try:
                        deal_data['monthly_rent'] = float(request.form.get('monthly_rent', 0) or 0)
                    except ValueError:
                        deal_data['monthly_rent'] = 0

                session['apply_step2'] = deal_data
                flash('Step 2 saved. Now review your borrower info.', 'info')
                return redirect(url_for('borrower.apply', step='3'))

            # ---- Step 3 → Step 4: borrower info ----
            elif step == '3':
                borrower_info = {}
                borrower_info['entity_name'] = request.form.get('entity_name', '')
                borrower_info['entity_type'] = request.form.get('entity_type', 'LLC')
                borrower_info['entity_state'] = request.form.get('entity_state', '')
                try:
                    borrower_info['credit_score'] = int(request.form.get('credit_score', 0) or 0)
                except ValueError:
                    borrower_info['credit_score'] = 0
                try:
                    borrower_info['years_experience'] = float(request.form.get('years_experience', 0) or 0)
                except ValueError:
                    borrower_info['years_experience'] = 0
                borrower_info['liquid_assets'] = float(request.form.get('liquid_assets', 0) or 0)
                try:
                    borrower_info['completed_flips'] = int(request.form.get('completed_flips', 0) or 0)
                except ValueError:
                    borrower_info['completed_flips'] = 0
                try:
                    borrower_info['completed_rentals'] = int(request.form.get('completed_rentals', 0) or 0)
                except ValueError:
                    borrower_info['completed_rentals'] = 0

                # Update borrower profile
                if borrower_info['entity_name']:
                    borrower.entity_name = borrower_info['entity_name']
                if borrower_info['entity_type']:
                    borrower.entity_type = borrower_info['entity_type']
                if borrower_info['entity_state']:
                    borrower.entity_state = borrower_info['entity_state']
                if borrower_info['credit_score']:
                    borrower.credit_score = borrower_info['credit_score']
                if borrower_info['years_experience']:
                    borrower.years_experience = borrower_info['years_experience']
                if borrower_info['liquid_assets']:
                    borrower.liquid_assets = borrower_info['liquid_assets']
                if borrower_info['completed_flips']:
                    borrower.completed_flips = borrower_info['completed_flips']
                if borrower_info['completed_rentals']:
                    borrower.completed_rentals = borrower_info['completed_rentals']
                db.session.commit()

                flash('Step 3 saved. Review your application.', 'info')
                return redirect(url_for('borrower.apply', step='4'))

        elif action == 'back':
            prev_step = str(max(1, int(step) - 1))
            return redirect(url_for('borrower.apply', step=prev_step))

    # ---- GET: display form ----
    step1 = session.get('apply_step1', {})
    step2 = session.get('apply_step2', {})
    return render_template('borrower/apply.html',
                           borrower=borrower,
                           step=step,
                           step1=step1,
                           step2=step2)


# ================================================================
# Pre-screen — AJAX endpoint
# ================================================================
@borrower_bp.route('/pre-screen', methods=['POST'])
@login_required
def pre_screen():
    borrower = _get_borrower()
    if borrower is None:
        return jsonify({'error': 'Unauthorized'}), 403

    address = (request.form.get('address') or request.json.get('address', '')).strip()
    product_type = (request.form.get('product_type') or request.json.get('product_type', 'bridge')).strip()

    if not address:
        return jsonify({'error': 'Address is required'}), 400
    if product_type not in ('bridge', 'dscr'):
        return jsonify({'error': 'Invalid product type'}), 400

    result = _run_prescreen(address, product_type)
    return jsonify(result)


# ================================================================
# Deal detail
# ================================================================
@borrower_bp.route('/deals/<int:id>')
@login_required
def deal_detail(id):
    borrower = _get_borrower()
    if borrower is None:
        return redirect(url_for('landing'))

    deal = Deal.query.filter_by(id=id, borrower_id=borrower.id).first_or_404()

    timeline = []
    if deal.submitted_at:
        timeline.append(('Submitted', deal.submitted_at))
    if deal.pre_screened_at:
        timeline.append(('Pre-screened', deal.pre_screened_at))
    if deal.reviewed_at:
        timeline.append(('Reviewed', deal.reviewed_at))
    if deal.approved_at:
        timeline.append(('Approved', deal.approved_at))
    if deal.closed_at:
        timeline.append(('Closed', deal.closed_at))
    if deal.funded_at:
        timeline.append(('Funded', deal.funded_at))
    if deal.paid_off_at:
        timeline.append(('Paid Off', deal.paid_off_at))

    # Loan info if exists
    loan = Loan.query.filter_by(deal_id=deal.id).first()
    draws = Draw.query.filter_by(loan_id=loan.id).order_by(Draw.created_at.desc()).all() if loan else []

    return render_template('borrower/deal_detail.html',
                           borrower=borrower,
                           deal=deal,
                           loan=loan,
                           draws=draws,
                           timeline=timeline)


# ================================================================
# Submit deal — triggers underwriting
# ================================================================
@borrower_bp.route('/deals/<int:id>/submit', methods=['POST'])
@login_required
def submit_deal(id):
    borrower = _get_borrower()
    if borrower is None:
        return redirect(url_for('landing'))

    deal = Deal.query.filter_by(id=id, borrower_id=borrower.id).first_or_404()

    if deal.status not in ('new', 'pre_screened'):
        flash('This deal has already been submitted.', 'error')
        return redirect(url_for('borrower.deal_detail', id=deal.id))

    # Pull session data if this is a fresh deal from the wizard
    step1 = session.get('apply_step1', {})
    step2 = session.get('apply_step2', {})

    if step1:
        deal.product_type = step1.get('product_type', deal.product_type)
        # Create/update property
        prop = deal.property
        if prop is None:
            prop = Property(
                address=step1.get('address', ''),
                city=step1.get('city', ''),
                state=step1.get('state', ''),
                zip_code=step1.get('zip_code', ''),
                property_type=step1.get('property_type', ''),
            )
            db.session.add(prop)
            deal.property = prop
        else:
            prop.address = step1.get('address', prop.address)
            prop.city = step1.get('city', prop.city)
            prop.state = step1.get('state', prop.state)
            prop.zip_code = step1.get('zip_code', prop.zip_code)
            prop.property_type = step1.get('property_type', prop.property_type)

    if step2:
        deal.loan_amount = step2.get('loan_amount', deal.loan_amount)
        deal.purchase_price = step2.get('purchase_price', deal.purchase_price)
        deal.arv_estimated = step2.get('arv_estimated', deal.arv_estimated)
        deal.exit_strategy = step2.get('exit_strategy', deal.exit_strategy)
        deal.projected_hold_months = step2.get('projected_hold_months', deal.projected_hold_months)
        if deal.product_type == 'bridge':
            deal.rehab_budget = step2.get('rehab_budget', deal.rehab_budget)
            deal.rehab_scope = step2.get('rehab_scope', deal.rehab_scope)
            deal.rehab_complexity = step2.get('rehab_complexity', deal.rehab_complexity)
            deal.contractor_name = step2.get('contractor_name', deal.contractor_name)
            deal.contractor_bid_amount = step2.get('contractor_bid_amount', deal.contractor_bid_amount)
        elif deal.product_type == 'dscr':
            deal.monthly_rent = step2.get('monthly_rent', deal.monthly_rent)

        # Financing structure fields
        deal.financing_type = step2.get('financing_type', deal.financing_type or 'down_payment')
        deal.points_type = step2.get('points_type', deal.points_type or 'upfront')

        # Daily points rate from pricing engine
        if deal.points_type == 'daily':
            from underwriting.pricing import PointsCalculator
            deal.daily_points_rate = PointsCalculator.suggest_daily_rate(
                loan_amount, deal.risk_tier or 'C'
            )
            deal.daily_points_amount = PointsCalculator.calc_daily_points(
                loan_amount, deal.daily_points_rate, 30
            )

        # Cross-collateral: create Collateral record
        if step2.get('financing_type') == 'cross_collateral':
            collat_addr = step2.get('collateral_address', '').strip()
            if collat_addr:
                from underwriting.pricing import CollateralAnalyzer
                existing_collateral = Collateral.query.filter_by(deal_id=deal.id).first()
                if existing_collateral:
                    existing_collateral.property.address = collat_addr
                else:
                    collat_prop = Property(address=collat_addr)
                    db.session.add(collat_prop)
                    db.session.flush()
                    collateral = Collateral(
                        deal_id=deal.id,
                        property_id=collat_prop.id,
                        estimated_value=step2.get('collateral_value', 0) or 0,
                        is_free_and_clear=CollateralAnalyzer.verify_free_and_clear(collat_addr),
                        available_equity=CollateralAnalyzer.estimate_available_equity(collat_addr),
                    )
                    db.session.add(collateral)

    # Calculate basic ratios
    purchase_price = deal.purchase_price or 0
    loan_amount = deal.loan_amount or 0
    arv = deal.arv_estimated or 0
    rehab = deal.rehab_budget or 0

    if purchase_price > 0 and loan_amount > 0:
        deal.ltv_purchase = round(loan_amount / purchase_price, 4)
    if arv > 0 and loan_amount > 0:
        deal.ltv_arv = round(loan_amount / arv, 4)
    total_cost = purchase_price + rehab
    if total_cost > 0 and loan_amount > 0:
        deal.ltc_ratio = round(loan_amount / total_cost, 4)

    # Borrower equity
    if total_cost > 0 and loan_amount > 0:
        equity = total_cost - loan_amount
        deal.borrower_equity_pct = round(max(equity / total_cost, 0), 4)

    # DSCR
    if deal.product_type == 'dscr' and deal.monthly_rent and deal.monthly_rent > 0 and loan_amount > 0:
        annual_rate = 0.10  # placeholder
        monthly_payment = (loan_amount * annual_rate) / 12
        if monthly_payment > 0:
            deal.dscr_ratio = round(deal.monthly_rent / monthly_payment, 4)

    # Projected profit
    if arv > 0 and purchase_price > 0:
        deal.projected_profit = round(arv - purchase_price - rehab, 2)
        if purchase_price > 0:
            deal.projected_roi = round(deal.projected_profit / purchase_price, 4)

    # ---- AI Underwriting ----
    try:
        address = (deal.property.address if deal.property else '')
        result = _run_prescreen(address, deal.product_type)
        deal.deal_score = result.get('score', 0)
        deal.risk_tier = result.get('tier', 'C')
        deal.property_score = result.get('score', 0) if result.get('source') == 'ai' else 50.0
        deal.market_score = result.get('score', 0) if result.get('source') == 'ai' else 45.0
        deal.borrower_score = min(100, max(0,
            (borrower.credit_score or 650) / 8.5
            + (borrower.years_experience or 0) * 2
            + (borrower.completed_flips or 0) * 1.5
        ))
    except Exception as exc:
        logger.warning("Underwriting failed during submit: %s", exc)

    deal.status = 'under_review'
    deal.submitted_at = datetime.now(timezone.utc)
    db.session.commit()

    # Clear wizard session data
    session.pop('apply_step1', None)
    session.pop('apply_step2', None)

    flash('Deal submitted! Our team will review it shortly.', 'success')
    return redirect(url_for('borrower.deal_detail', id=deal.id))


# ================================================================
# Draws
# ================================================================
@borrower_bp.route('/draws/<int:loan_id>')
@login_required
def draws(loan_id):
    borrower = _get_borrower()
    if borrower is None:
        return redirect(url_for('landing'))

    loan = Loan.query.filter_by(id=loan_id, borrower_id=borrower.id).first_or_404()
    draws_list = Draw.query.filter_by(loan_id=loan.id)\
                          .order_by(Draw.draw_number.asc()).all()
    total_disbursed = sum((d.amount_disbursed or 0) for d in draws_list if d.status == 'disbursed')
    remaining = (loan.original_amount or 0) - total_disbursed

    return render_template('borrower/draws.html',
                           borrower=borrower,
                           loan=loan,
                           draws=draws_list,
                           total_disbursed=total_disbursed,
                           remaining=remaining)


@borrower_bp.route('/draws/<int:loan_id>/request', methods=['POST'])
@login_required
def request_draw(loan_id):
    borrower = _get_borrower()
    if borrower is None:
        return redirect(url_for('landing'))

    loan = Loan.query.filter_by(id=loan_id, borrower_id=borrower.id).first_or_404()

    if loan.status != 'active':
        flash('Draw requests are only available on active loans.', 'error')
        return redirect(url_for('borrower.draws', loan_id=loan.id))

    try:
        amount = float(request.form.get('amount', 0) or 0)
    except ValueError:
        flash('Invalid amount.', 'error')
        return redirect(url_for('borrower.draws', loan_id=loan.id))

    if amount <= 0:
        flash('Draw amount must be positive.', 'error')
        return redirect(url_for('borrower.draws', loan_id=loan.id))

    # Determine next draw number
    last_draw = Draw.query.filter_by(loan_id=loan.id)\
                          .order_by(Draw.draw_number.desc()).first()
    next_number = (last_draw.draw_number + 1) if last_draw else 1

    draw = Draw(
        loan_id=loan.id,
        draw_number=next_number,
        amount_requested=amount,
        scope_completed=request.form.get('scope_completed', ''),
        status='requested',
    )
    db.session.add(draw)
    db.session.commit()

    flash(f'Draw #{next_number} requested for ${amount:,.2f}.', 'success')
    return redirect(url_for('borrower.draws', loan_id=loan.id))


# ================================================================
# Payments
# ================================================================
@borrower_bp.route('/payments/<int:loan_id>')
@login_required
def payments(loan_id):
    borrower = _get_borrower()
    if borrower is None:
        return redirect(url_for('landing'))

    loan = Loan.query.filter_by(id=loan_id, borrower_id=borrower.id).first_or_404()
    payments_list = Payment.query.filter_by(loan_id=loan.id)\
                                 .order_by(Payment.due_date.desc()).all()

    total_paid = sum(p.amount for p in payments_list if p.status == 'paid')
    total_due = sum(p.amount for p in payments_list if p.status == 'pending')

    return render_template('borrower/payments.html',
                           borrower=borrower,
                           loan=loan,
                           payments=payments_list,
                           total_paid=total_paid,
                           total_due=total_due)


# ================================================================
# Payoff Calculator
# ================================================================
@borrower_bp.route('/loans/<int:loan_id>/payoff')
@login_required
def payoff(loan_id):
    borrower = _get_borrower()
    if borrower is None:
        return redirect(url_for('landing'))

    loan = Loan.query.filter_by(id=loan_id, borrower_id=borrower.id).first_or_404()
    deal = Deal.query.get(loan.deal_id)

    # Use PointsCalculator for accurate math
    from underwriting.pricing import PointsCalculator
    from automation.origination import OriginationAutomator

    payoff_date_str = request.args.get('payoff_date', date.today().isoformat())
    try:
        payoff_date_obj = date.fromisoformat(payoff_date_str)
    except (ValueError, TypeError):
        payoff_date_obj = date.today()

    # Generate full payoff statement
    payoff = OriginationAutomator.generate_payoff_statement(loan, payoff_date_obj)

    # Daily points info for display
    daily_points_rate = payoff.get('daily_points_rate', 0)
    daily_points_accrued = payoff.get('daily_points_accrued', 0)
    per_diem = payoff.get('per_diem_after', 0)

    # Points type from deal
    points_type = deal.points_type if deal else 'upfront'

    return render_template('borrower/payoff.html',
                           borrower=borrower,
                           loan=loan,
                           deal=deal,
                           payoff=payoff,
                           today=date.today().isoformat(),
                           daily_points_rate=daily_points_rate,
                           daily_points_accrued=daily_points_accrued,
                           per_diem=per_diem,
                           points_type=points_type)


# ================================================================
# Auto-Draft / ACH Setup
# ================================================================
@borrower_bp.route('/loans/<int:loan_id>/autopay', methods=['GET', 'POST'])
@login_required
def autopay(loan_id):
    borrower = _get_borrower()
    if borrower is None:
        return redirect(url_for('landing'))

    loan = Loan.query.filter_by(id=loan_id, borrower_id=borrower.id).first_or_404()
    deal = Deal.query.get(loan.deal_id)

    if request.method == 'POST':
        auto_draft_enabled = request.form.get('auto_draft_enabled') == 'on'
        auto_draft_day = int(request.form.get('auto_draft_day', 5) or 5)

        # Clamp day between 1-28
        auto_draft_day = max(1, min(28, auto_draft_day))

        loan.auto_draft_enabled = auto_draft_enabled
        if auto_draft_enabled:
            loan.auto_draft_day = auto_draft_day
        else:
            loan.auto_draft_day = None
        db.session.commit()

        if auto_draft_enabled:
            flash(f'Auto-draft enabled. Monthly payments will be drafted on day {auto_draft_day} of each month.', 'success')
        else:
            flash('Auto-draft disabled.', 'info')
        return redirect(url_for('borrower.payments', loan_id=loan.id))

    return render_template('borrower/autopay.html',
                           borrower=borrower,
                           loan=loan,
                           deal=deal)
