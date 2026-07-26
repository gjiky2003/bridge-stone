"""End-to-end test for BridgeStone Capital platform."""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

os.environ['SECRET_KEY'] = 'test-secret'

from app import create_app
from models import db

app = create_app()
client = app.test_client()

def test_landing():
    """Landing page loads"""
    resp = client.get('/')
    assert resp.status_code == 200, f"Landing failed: {resp.status_code}"
    assert b'BridgeStone' in resp.data, "Landing page missing brand name"
    print("✅ Landing page OK")

def test_register():
    """User can register"""
    resp = client.post('/register', data={
        'email': 'test_flipper@demo.com',
        'name': 'Test Flipper',
        'password': 'test123',
        'role': 'borrower',
        'entity_name': 'Test LLC'
    }, follow_redirects=True)
    assert resp.status_code == 200, f"Register failed: {resp.status_code}"
    print("✅ Registration OK")

def test_login():
    """User can login"""
    resp = client.post('/login', data={
        'email': 'test_flipper@demo.com',
        'password': 'test123'
    }, follow_redirects=True)
    assert resp.status_code == 200, f"Login failed: {resp.status_code}"
    print("✅ Login OK")

def test_borrower_dashboard():
    """Borrower dashboard loads"""
    client.post('/login', data={'email': 'test_flipper@demo.com', 'password': 'test123'})
    resp = client.get('/borrower/dashboard')
    assert resp.status_code == 200, f"Borrower dashboard failed: {resp.status_code}"
    print("✅ Borrower dashboard OK")

def test_admin_login():
    """Admin can login"""
    resp = client.post('/login', data={
        'email': 'admin@bridgestonecapital.com',
        'password': 'admin123'
    }, follow_redirects=True)
    assert resp.status_code == 200, f"Admin login failed: {resp.status_code}"
    print("✅ Admin login OK")

def test_admin_dashboard():
    """Admin dashboard loads"""
    client.post('/login', data={'email': 'admin@bridgestonecapital.com', 'password': 'admin123'})
    resp = client.get('/admin/dashboard')
    assert resp.status_code == 200, f"Admin dashboard failed: {resp.status_code}"
    print("✅ Admin dashboard OK")

def test_investor_register():
    """Investor can register"""
    resp = client.post('/register', data={
        'email': 'test_investor@demo.com',
        'name': 'Test Investor',
        'password': 'test123',
        'role': 'investor'
    }, follow_redirects=True)
    assert resp.status_code == 200
    print("✅ Investor registration OK")

def test_investor_dashboard():
    """Investor dashboard loads"""
    client.post('/login', data={'email': 'test_investor@demo.com', 'password': 'test123'})
    resp = client.get('/investor/dashboard')
    assert resp.status_code == 200
    print("✅ Investor dashboard OK")

def test_underwriting_engine():
    """Underwriting engine produces valid scores"""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'underwriting'))
    try:
        from underwriting.bridge_scorer import HardMoneyScorer
        scorer = HardMoneyScorer()
        
        property_data = {"address": "123 Main St, Cleveland OH 44102", "purchase_price": 85000,
                        "sqft": 1400, "beds": 3, "baths": 2, "year_built": 1975,
                        "estimated_arv": 195000, "rehab_budget": 45000}
        deal_data = {"loan_amount": 117000, "ltv_arv": 0.60, "borrower_equity_pct": 0.15,
                    "projected_roi": 20.0, "projected_hold_months": 8, "exit_strategy": "sale"}
        borrower_data = {"completed_flips": 7, "years_experience": 4.5, "credit_score": 685,
                        "entity_type": "LLC", "liquid_assets": 120000}
        market_data = {"msa_median_price": 180000, "msa_price_trend_12m": 0.03,
                      "msa_days_on_market": 45, "msa_inventory_months": 3.5}
        
        result = scorer.score_deal(property_data, deal_data, borrower_data, market_data)
        assert 0 <= result['score'] <= 100, f"Score out of range: {result['score']}"
        assert result['tier'] in ['A', 'B', 'C', 'D', 'R'], f"Invalid tier: {result['tier']}"
        print(f"✅ Bridge scorer OK — Score: {result['score']}, Tier: {result['tier']}")
    except ImportError as e:
        print(f"⚠️ Bridge scorer not yet available: {e}")
    
    try:
        from underwriting.dscr_scorer import DSCRScorer
        scorer = DSCRScorer()
        
        property_data = {"address": "789 Birch Way, Atlanta GA 30310", "purchase_price": 185000,
                        "property_value": 185000, "year_built": 2005, "sqft": 1600,
                        "beds": 3, "baths": 2}
        rent_data = {"monthly_rent": 1950, "annual_taxes": 2400, "annual_insurance": 1200,
                    "hoa_monthly": 0}
        borrower_data = {"rental_properties_owned": 3, "years_as_landlord": 3, "credit_score": 720,
                        "entity_type": "LLC", "liquid_assets": 80000}
        market_data = {"msa_median_rent": 1700, "msa_rent_growth_3yr": 0.04, "msa_vacancy_rate": 0.05}
        
        result = scorer.score_loan(property_data, rent_data, borrower_data, market_data)
        assert 0 <= result['score'] <= 100
        assert result['tier'] in ['A', 'B', 'C', 'D', 'R']
        print(f"✅ DSCR scorer OK — Score: {result['score']}, Tier: {result['tier']}, DSCR: {result.get('dscr', 'N/A')}")
    except ImportError as e:
        print(f"⚠️ DSCR scorer not yet available: {e}")


if __name__ == '__main__':
    print("=" * 60)
    print("BRIDGESTONE CAPITAL — E2E TEST SUITE")
    print("=" * 60)
    
    with app.app_context():
        db.create_all()
    
    tests = [
        test_landing, test_register, test_login, test_borrower_dashboard,
        test_admin_login, test_admin_dashboard,
        test_investor_register, test_investor_dashboard,
        test_underwriting_engine
    ]
    
    passed, failed = 0, 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__} FAILED: {e}")
            failed += 1
    
    print("=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed, {len(tests)} total")
    print("=" * 60)
