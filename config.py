import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-change-in-production')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///bridge_stone.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # ATTOM API
    ATTOM_API_KEY = os.getenv('ATTOM_API_KEY', '')
    ATTOM_BASE_URL = 'https://api.gateway.attomdata.com/propertyapi/v1.0.0'
    
    # Stripe
    STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
    STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY', '')
    
    # App
    APP_NAME = 'BridgeStone Capital'
    APP_URL = os.getenv('APP_URL', 'http://localhost:5000')
    
    # Underwriting
    MODEL_PATH = os.path.join(os.path.dirname(__file__), 'underwriting', 'models')
    
    # Business rules
    MIN_BRIDGE_LOAN = 50000
    MAX_BRIDGE_LOAN = 250000
    MIN_DSCR_LOAN = 75000
    MAX_DSCR_LOAN = 400000
    MAX_LTV_ARV_BRIDGE = 0.70
    MAX_LTV_DSCR = 0.80
    MIN_DSCR_RATIO = 1.00
