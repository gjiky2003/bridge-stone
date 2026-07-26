"""BridgeStone Capital — Flask Application"""
import os, sys
from flask import Flask, render_template, redirect, url_for, request, flash, g, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from config import Config
from models import db, User

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    
    login_manager = LoginManager()
    login_manager.login_view = 'login'
    login_manager.init_app(app)
    
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    
    # ==========================================
    # AUTH ROUTES
    # ==========================================
    @app.route('/')
    def landing():
        if current_user.is_authenticated:
            role = current_user.role
            if role == 'admin': return redirect(url_for('admin.dashboard'))
            if role == 'investor': return redirect(url_for('investor.dashboard'))
            return redirect(url_for('borrower.dashboard'))
        return render_template('landing.html')
    
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            user = User.query.filter_by(email=request.form['email']).first()
            if user and user.check_password(request.form['password']):
                login_user(user)
                flash('Welcome back!', 'success')
                role = user.role
                if role == 'admin': return redirect(url_for('admin.dashboard'))
                if role == 'investor': return redirect(url_for('investor.dashboard'))
                return redirect(url_for('borrower.dashboard'))
            flash('Invalid email or password.', 'error')
        return render_template('auth/login.html')
    
    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            role = request.form.get('role', 'borrower')
            if User.query.filter_by(email=request.form['email']).first():
                flash('Email already registered.', 'error')
                return redirect(url_for('register'))
            user = User(email=request.form['email'], name=request.form['name'], role=role)
            user.set_password(request.form['password'])
            db.session.add(user)
            db.session.commit()
            
            # Create borrower or investor profile
            if role == 'borrower':
                from models import Borrower
                b = Borrower(user_id=user.id, entity_name=request.form.get('entity_name', ''))
                db.session.add(b)
            elif role == 'investor':
                from models import Investor
                inv = Investor(user_id=user.id)
                db.session.add(inv)
            elif role == 'admin':
                pass
            
            db.session.commit()
            login_user(user)
            flash('Account created! Welcome to BridgeStone Capital.', 'success')
            if role == 'investor': return redirect(url_for('investor.dashboard'))
            if role == 'admin': return redirect(url_for('admin.dashboard'))
            return redirect(url_for('borrower.dashboard'))
        return render_template('auth/register.html')
    
    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('landing'))
    
    # ==========================================
    # REGISTER BLUEPRINTS
    # ==========================================
    from routes.borrower import borrower_bp
    from routes.admin import admin_bp
    from routes.investor import investor_bp
    
    app.register_blueprint(borrower_bp, url_prefix='/borrower')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(investor_bp, url_prefix='/investor')
    
    # ==========================================
    # DB INIT
    # ==========================================
    with app.app_context():
        db.create_all()
        # Create default admin if none exists
        if not User.query.filter_by(role='admin').first():
            admin = User(email='admin@bridgestonecapital.com', name='Admin', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("[INIT] Default admin created: admin@bridgestonecapital.com / admin123")
    
    return app


if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
