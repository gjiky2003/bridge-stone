"""Seed the database with sample data for development and testing."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import create_app
from models import db, User, Borrower, Property, Deal, Loan, Investor, Investment
from datetime import datetime, timezone, timedelta

app = create_app()

SAMPLE_DEALS = [
    {"address": "1234 Elm St, Cleveland OH 44102", "product": "bridge", "purchase": 85000, "rehab": 45000, "arv": 195000},
    {"address": "567 Oak Ave, Indianapolis IN 46201", "product": "bridge", "purchase": 62000, "rehab": 35000, "arv": 145000},
    {"address": "890 Maple Dr, Birmingham AL 35203", "product": "bridge", "purchase": 95000, "rehab": 55000, "arv": 210000},
    {"address": "234 Pine Ln, Memphis TN 38104", "product": "bridge", "purchase": 72000, "rehab": 28000, "arv": 140000},
    {"address": "456 Cedar Ct, Columbus OH 43201", "product": "bridge", "purchase": 110000, "rehab": 60000, "arv": 250000},
    {"address": "789 Birch Way, Atlanta GA 30310", "product": "dscr", "purchase": 185000, "rehab": 0, "arv": 185000, "rent": 1950},
    {"address": "321 Walnut St, Charlotte NC 28202", "product": "dscr", "purchase": 220000, "rehab": 0, "arv": 220000, "rent": 2400},
    {"address": "654 Spruce Ave, Nashville TN 37206", "product": "dscr", "purchase": 175000, "rehab": 0, "arv": 175000, "rent": 1850},
    {"address": "987 Ash Blvd, Greenville SC 29601", "product": "dscr", "purchase": 155000, "rehab": 0, "arv": 155000, "rent": 1600},
    {"address": "147 Cherry Rd, Knoxville TN 37902", "product": "bridge", "purchase": 78000, "rehab": 32000, "arv": 160000},
]

with app.app_context():
    borrower_user = User.query.filter_by(email='borrower@demo.com').first()
    if not borrower_user:
        borrower_user = User(email='borrower@demo.com', name='James Carter', role='borrower')
        borrower_user.set_password('demo123')
        db.session.add(borrower_user)
        db.session.commit()
    
    b = Borrower.query.filter_by(user_id=borrower_user.id).first()
    if not b:
        b = Borrower(user_id=borrower_user.id, entity_name='Carter REI LLC', entity_type='LLC',
                     entity_state='OH', completed_flips=7, completed_rentals=2,
                     years_experience=4.5, credit_score=685, current_active_projects=2,
                     net_worth=450000, liquid_assets=120000, kyc_status='approved')
        db.session.add(b)
        db.session.commit()
    
    investor_user = User.query.filter_by(email='investor@demo.com').first()
    if not investor_user:
        investor_user = User(email='investor@demo.com', name='Sarah Morgan', role='investor')
        investor_user.set_password('demo123')
        db.session.add(investor_user)
        db.session.commit()
    
    inv = Investor.query.filter_by(user_id=investor_user.id).first()
    if not inv:
        inv = Investor(user_id=investor_user.id, accreditation_status='verified',
                      total_committed=500000, total_invested=250000,
                      available_balance=250000, preferred_return=0.08, profit_split=0.70)
        db.session.add(inv)
        db.session.commit()
    
    existing = Deal.query.count()
    if existing < 5:
        for sd in SAMPLE_DEALS[:6]:
            prop = Property(address=sd['address'], city=sd['address'].split(',')[1].strip().split(' ')[0],
                          state=sd['address'].split(' ')[-2], zip_code='44102',
                          property_type='SFR', year_built=random.randint(1950, 2005),
                          sqft=random.randint(900, 2200), beds=random.randint(2, 4),
                          baths=random.uniform(1, 2.5), purchase_price=sd['purchase'],
                          estimated_arv=sd['arv'], arv_low=sd['arv']*0.9, arv_high=sd['arv']*1.1,
                          arv_confidence=random.uniform(0.75, 0.92),
                          market_rent=sd.get('rent', 0))
            db.session.add(prop)
            db.session.flush()
            
            ltv = sd['purchase'] / sd['arv']
            deal = Deal(borrower_id=b.id, property_id=prop.id, product_type=sd['product'],
                       status=random.choice(['new', 'under_review', 'approved', 'active']),
                       loan_amount=sd['purchase'] + sd.get('rehab', 0) * 0.85,
                       purchase_price=sd['purchase'], rehab_budget=sd.get('rehab', 0),
                       arv_estimated=sd['arv'], monthly_rent=sd.get('rent', 0),
                       ltv_arv=ltv, borrower_equity_pct=0.15 + random.uniform(0, 0.1),
                       projected_profit=sd['arv'] - sd['purchase'] - sd.get('rehab', 0) - 15000,
                       projected_roi=random.uniform(8, 25),
                       projected_hold_months=random.randint(6, 12),
                       exit_strategy='sale' if sd['product'] == 'bridge' else 'rent',
                       deal_score=random.uniform(55, 92), risk_tier=random.choice(['A','B','B','C']))
            db.session.add(deal)
        
        db.session.commit()
        print(f"[SEED] Added {len(SAMPLE_DEALS[:6])} sample deals")
    
    print(f"[SEED] Database ready — Users: {User.query.count()}, Deals: {Deal.query.count()}")
    print(f"[SEED] Demo login: borrower@demo.com / demo123")
    print(f"[SEED] Demo login: investor@demo.com / demo123")
    print(f"[SEED] Admin login: admin@bridgestonecapital.com / admin123")
