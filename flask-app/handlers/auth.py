from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, UserPlan, Payment
from datetime import datetime

auth = Blueprint('auth', __name__)

ADMIN_EMAIL = 'mukilarasu@admin.com'

@auth.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name            = request.form.get('name')
        email           = request.form.get('email')
        password        = request.form.get('password')
        business_name   = request.form.get('business_name')
        whatsapp_number = request.form.get('whatsapp_number')

        if User.query.filter_by(email=email).first():
            flash('Email already exists!')
            return redirect(url_for('auth.register'))

        new_user = User(
            name            = name,
            email           = email,
            password        = generate_password_hash(password),
            business_name   = business_name,
            whatsapp_number = whatsapp_number
        )
        db.session.add(new_user)
        db.session.commit()
        flash('Account created! Please login.')
        return redirect(url_for('auth.login'))

    return render_template('register.html')


@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form.get('email')
        password = request.form.get('password')

        # Block admin from regular login
        if email == ADMIN_EMAIL:
            flash('Admin must use the admin panel below.')
            return redirect(url_for('auth.login'))

        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password, password):
            flash('Invalid email or password!')
            return redirect(url_for('auth.login'))

        login_user(user)

        # New user with no plan → welcome page
        from handlers.dashboard import has_selected_plan
        if not has_selected_plan(user.id):
            return redirect(url_for('dashboard.welcome'))

        # ✅ Check if plan expired → redirect to upgrade with message
        plan = UserPlan.query.filter_by(user_id=user.id, is_active=True).first()
        if plan and plan.plan_name != 'free' and plan.expires_at and plan.expires_at < datetime.utcnow():
            flash('⚠️ Your plan has expired! Please renew to continue using your bot.', 'error')
            return redirect(url_for('dashboard.upgrade'))

        # ✅ Check if payment pending → show notice on dashboard
        pending = Payment.query.filter_by(user_id=user.id).filter(
            Payment.status.in_(['pending', 'submitted'])
        ).first()
        if pending:
            flash('⏳ Your payment is under review. Admin will activate your plan soon!', 'warning')

        return redirect(url_for('dashboard.index'))

    return render_template('login.html')


@auth.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))