"""BridgeStone Capital — Database Models"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(255))
    role = db.Column(db.String(50), default='borrower')  # borrower, admin, investor
    entity_name = db.Column(db.String(255))
    phone = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    borrowers = db.relationship('Borrower', backref='user', uselist=False)
    investors = db.relationship('Investor', backref='user', uselist=False)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Borrower(db.Model):
    __tablename__ = 'borrowers'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    entity_name = db.Column(db.String(255))
    entity_type = db.Column(db.String(50))  # LLC, S-Corp, LP
    entity_state = db.Column(db.String(2))
    completed_flips = db.Column(db.Integer, default=0)
    completed_rentals = db.Column(db.Integer, default=0)
    years_experience = db.Column(db.Float, default=0)
    credit_score = db.Column(db.Integer)
    current_active_projects = db.Column(db.Integer, default=0)
    net_worth = db.Column(db.Float)
    liquid_assets = db.Column(db.Float)
    kyc_status = db.Column(db.String(50), default='pending')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    deals = db.relationship('Deal', backref='borrower', lazy=True)


class Property(db.Model):
    __tablename__ = 'properties'
    id = db.Column(db.Integer, primary_key=True)
    address = db.Column(db.String(500), nullable=False)
    city = db.Column(db.String(255))
    state = db.Column(db.String(2))
    zip_code = db.Column(db.String(10))
    property_type = db.Column(db.String(50))  # SFR, Condo, Townhouse, 2-4 Unit
    year_built = db.Column(db.Integer)
    sqft = db.Column(db.Integer)
    beds = db.Column(db.Integer)
    baths = db.Column(db.Float)
    lot_size = db.Column(db.Float)
    estimated_arv = db.Column(db.Float)
    arv_low = db.Column(db.Float)
    arv_high = db.Column(db.Float)
    arv_confidence = db.Column(db.Float)
    purchase_price = db.Column(db.Float)
    market_rent = db.Column(db.Float)  # For DSCR
    rent_low = db.Column(db.Float)
    rent_high = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Deal(db.Model):
    __tablename__ = 'deals'
    id = db.Column(db.Integer, primary_key=True)
    borrower_id = db.Column(db.Integer, db.ForeignKey('borrowers.id'))
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'))
    product_type = db.Column(db.String(50), nullable=False)  # 'bridge' or 'dscr'
    status = db.Column(db.String(50), default='new')  # new, pre_screened, under_review, approved, rejected, in_closing, active, paid_off, defaulted
    
    # Loan request
    loan_amount = db.Column(db.Float)
    purchase_price = db.Column(db.Float)
    rehab_budget = db.Column(db.Float)  # Bridge only
    arv_estimated = db.Column(db.Float)
    monthly_rent = db.Column(db.Float)  # DSCR only
    
    # Calculated ratios
    ltv_purchase = db.Column(db.Float)
    ltv_arv = db.Column(db.Float)
    ltc_ratio = db.Column(db.Float)
    dscr_ratio = db.Column(db.Float)  # DSCR only
    borrower_equity_pct = db.Column(db.Float)
    projected_profit = db.Column(db.Float)
    projected_roi = db.Column(db.Float)
    projected_hold_months = db.Column(db.Integer)
    exit_strategy = db.Column(db.String(100))  # sale, refinance, rent
    
    # Rehab details (Bridge)
    rehab_scope = db.Column(db.Text)
    rehab_complexity = db.Column(db.Integer)  # 1-5
    contractor_bid_amount = db.Column(db.Float)
    contractor_name = db.Column(db.String(255))
    
    # AI Scores
    deal_score = db.Column(db.Float)  # 0-100
    property_score = db.Column(db.Float)
    market_score = db.Column(db.Float)
    borrower_score = db.Column(db.Float)
    risk_tier = db.Column(db.String(2))  # A, B, C, D, R
    
    # Financing — cross-collateral & daily points
    financing_type = db.Column(db.String(50), default='down_payment')  # 'down_payment' or 'cross_collateral'
    down_payment_pct = db.Column(db.Float)  # borrower cash down payment percentage
    points_type = db.Column(db.String(20), default='upfront')  # 'upfront' or 'daily'
    daily_points_amount = db.Column(db.Float)  # estimated daily points dollar amount
    # Cross-collateral fields
    collateral_address = db.Column(db.String(500))
    collateral_value = db.Column(db.Float)
    collateral_free_clear = db.Column(db.Boolean, default=False)

    # Decision
    approved_rate = db.Column(db.Float)
    approved_points = db.Column(db.Float)
    approved_term_months = db.Column(db.Integer)
    daily_points_rate = db.Column(db.Float)  # $/day for daily-accruing points
    underwriter_notes = db.Column(db.Text)
    decision_reason = db.Column(db.Text)
    
    # Documents
    scope_of_work_url = db.Column(db.String(500))
    contractor_bid_url = db.Column(db.String(500))
    llc_docs_url = db.Column(db.String(500))
    insurance_url = db.Column(db.String(500))
    title_report_url = db.Column(db.String(500))
    inspection_report_url = db.Column(db.String(500))
    
    # Timestamps
    submitted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    pre_screened_at = db.Column(db.DateTime)
    reviewed_at = db.Column(db.DateTime)
    approved_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)
    funded_at = db.Column(db.DateTime)
    paid_off_at = db.Column(db.DateTime)
    
    property = db.relationship('Property', backref='deals')
    loan = db.relationship('Loan', backref='deal', uselist=False)


class Loan(db.Model):
    __tablename__ = 'loans'
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'), unique=True)
    borrower_id = db.Column(db.Integer, db.ForeignKey('borrowers.id'))
    product_type = db.Column(db.String(50))
    status = db.Column(db.String(50), default='active')
    
    original_amount = db.Column(db.Float)
    current_balance = db.Column(db.Float)
    interest_rate = db.Column(db.Float)
    origination_points = db.Column(db.Float)
    term_months = db.Column(db.Integer)
    monthly_payment = db.Column(db.Float)
    
    origination_date = db.Column(db.Date)
    maturity_date = db.Column(db.Date)
    next_payment_date = db.Column(db.Date)
    last_payment_date = db.Column(db.Date)
    days_late = db.Column(db.Integer, default=0)
    
    # Daily points tracking
    daily_points_accrued = db.Column(db.Float, default=0.0)
    payoff_date = db.Column(db.Date)
    
    # Auto-draft / ACH
    auto_draft_enabled = db.Column(db.Boolean, default=False)
    auto_draft_day = db.Column(db.Integer)  # day of month (1-28)
    
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Draw(db.Model):
    __tablename__ = 'draws'
    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey('loans.id'))
    draw_number = db.Column(db.Integer)
    amount_requested = db.Column(db.Float)
    amount_approved = db.Column(db.Float)
    amount_disbursed = db.Column(db.Float)
    status = db.Column(db.String(50), default='requested')
    scope_completed = db.Column(db.Text)
    inspector_notes = db.Column(db.Text)
    disbursed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey('loans.id'))
    payment_type = db.Column(db.String(50))  # interest, payoff, origination, late_fee, extension
    amount = db.Column(db.Float)
    status = db.Column(db.String(50), default='pending')
    due_date = db.Column(db.Date)
    paid_date = db.Column(db.Date)
    stripe_payment_id = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Investor(db.Model):
    __tablename__ = 'investors'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    accreditation_status = db.Column(db.String(50), default='pending')
    total_committed = db.Column(db.Float, default=0)
    total_invested = db.Column(db.Float, default=0)
    available_balance = db.Column(db.Float, default=0)
    preferred_return = db.Column(db.Float, default=0.08)
    profit_split = db.Column(db.Float, default=0.70)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Investment(db.Model):
    __tablename__ = 'investments'
    id = db.Column(db.Integer, primary_key=True)
    investor_id = db.Column(db.Integer, db.ForeignKey('investors.id'))
    loan_id = db.Column(db.Integer, db.ForeignKey('loans.id'))
    amount = db.Column(db.Float)
    status = db.Column(db.String(50), default='active')
    total_return = db.Column(db.Float, default=0)
    irr = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Collateral(db.Model):
    """Cross-collateral property linking a deal to additional collateral properties."""
    __tablename__ = 'collateral'
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deals.id'), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False)
    collateral_type = db.Column(db.String(50), default='real_estate')  # real_estate, vehicle, other
    estimated_value = db.Column(db.Float)
    lien_position = db.Column(db.Integer, default=1)  # 1 = first lien
    is_free_and_clear = db.Column(db.Boolean, default=None)  # None = unverified
    available_equity = db.Column(db.Float)
    verified_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    property = db.relationship('Property', backref='collaterals')
