from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required, current_user, login_user, logout_user
from models import db, User, UserPlan, Payment, Bot, Booking, ConversationHistory
from datetime import datetime, timedelta
from functools import wraps

admin = Blueprint('admin', __name__, url_prefix='/admin')

ADMIN_EMAIL    = 'mukilarasu@admin.com'
ADMIN_PASSWORD = 'admin@Muki123'

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('admin.admin_login'))
        if current_user.email != ADMIN_EMAIL:
            flash('Admin access denied!')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function


@admin.route('/login', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated and current_user.email == ADMIN_EMAIL:
        return redirect(url_for('admin.dashboard'))

    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        if password == ADMIN_PASSWORD:
            admin_user = User.query.filter_by(email=ADMIN_EMAIL).first()
            if admin_user:
                login_user(admin_user)
                return redirect(url_for('admin.dashboard'))
            else:
                flash('Admin user not found. Restart Flask.')
        else:
            flash('Wrong password!')

    return render_template('Admin_login.html')


@admin.route('/dashboard')
@admin_required
def dashboard():
    total_users = User.query.filter(User.email != ADMIN_EMAIL).count()

    total_revenue = db.session.query(db.func.sum(Payment.amount)).filter(
        Payment.status == 'completed'
    ).scalar() or 0

    active_users = UserPlan.query.filter(
        UserPlan.is_active == True,
        UserPlan.plan_name != 'free'
    ).count()

    total_bots = Bot.query.count()

    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
    revenue_this_month = db.session.query(db.func.sum(Payment.amount)).filter(
        Payment.status       == 'completed',
        Payment.completed_at >= month_start
    ).scalar() or 0

    pending_count = Payment.query.filter(
        Payment.status.in_(['pending', 'submitted'])
    ).count()

    recent_payments = Payment.query.filter_by(status='completed') \
        .order_by(Payment.completed_at.desc()).limit(10).all()

    payment_data = []
    for p in recent_payments:
        user = User.query.get(p.user_id)
        payment_data.append({
            'user_name':  user.name  if user else 'Unknown',
            'user_email': user.email if user else 'N/A',
            'plan':       p.plan_name.upper(),
            'amount':     p.amount,
            'date':       p.completed_at.strftime('%d-%m-%Y %H:%M') if p.completed_at else 'N/A'
        })

    return render_template('admin_dashboard.html',
        total_users        = total_users,
        total_revenue      = total_revenue,
        active_users       = active_users,
        total_bots         = total_bots,
        revenue_this_month = revenue_this_month,
        pending_count      = pending_count,
        recent_payments    = payment_data
    )


@admin.route('/users')
@admin_required
def users():
    all_users = User.query.filter(User.email != ADMIN_EMAIL).all()

    user_data = []
    for user in all_users:
        plan      = UserPlan.query.filter_by(user_id=user.id, is_active=True).first()
        bot_count = Bot.query.filter_by(user_id=user.id).count()
        total_spent = db.session.query(db.func.sum(Payment.amount)).filter(
            Payment.user_id == user.id,
            Payment.status  == 'completed'
        ).scalar() or 0

        # FIX 3: payments count was missing — added here
        payments_count = Payment.query.filter_by(user_id=user.id).count()

        user_data.append({
            'id':          user.id,
            'name':        user.name,
            'email':       user.email,
            'business':    user.business_name    or 'N/A',
            'plan':        plan.plan_name.upper() if plan else 'FREE',
            'daily_limit': plan.daily_limit       if plan else 5,
            'bots':        bot_count,
            'payments':    payments_count,
            'total_spent': total_spent,
            'joined':      user.created_at.strftime('%d-%m-%Y') if user.created_at else 'N/A'
        })

    return render_template('admin_users.html', users=user_data)


@admin.route('/users/<int:user_id>')
@admin_required
def user_details(user_id):
    user = User.query.get_or_404(user_id)

    if user.email == ADMIN_EMAIL:
        flash('Cannot view admin user')
        return redirect(url_for('admin.users'))

    plan          = UserPlan.query.filter_by(user_id=user_id, is_active=True).first()
    bots          = Bot.query.filter_by(user_id=user_id).all()
    payments      = Payment.query.filter_by(user_id=user_id).order_by(Payment.created_at.desc()).all()
    bookings      = Booking.query.filter_by(user_id=user_id).count()
    conversations = ConversationHistory.query.filter_by(user_id=user_id).count()

    payment_data = []
    for p in payments:
        payment_data.append({
            'id':        p.id,
            'plan':      p.plan_name.upper(),
            'amount':    p.amount,
            'status':    p.status.upper(),
            'date':      p.created_at.strftime('%d-%m-%Y %H:%M') if p.created_at else 'N/A',
            'completed': p.completed_at.strftime('%d-%m-%Y %H:%M') if p.completed_at else 'Pending'
        })

    total_spent = sum(p['amount'] for p in payment_data if p['status'] == 'COMPLETED')

    return render_template('admin_user_details.html',
        user          = user,
        plan          = plan,
        bots          = bots,
        bookings      = bookings,
        conversations = conversations,
        payments      = payment_data,
        total_spent   = total_spent
    )


@admin.route('/revenue')
@admin_required
def revenue():
    total_revenue = db.session.query(db.func.sum(Payment.amount)).filter(
        Payment.status == 'completed'
    ).scalar() or 0

    revenue_by_plan = db.session.query(
        Payment.plan_name,
        db.func.count(Payment.id).label('count'),
        db.func.sum(Payment.amount).label('total')
    ).filter(Payment.status == 'completed').group_by(Payment.plan_name).all()

    plan_revenue = [
        {'plan': name.upper(), 'count': count, 'total': total or 0}
        for name, count, total in revenue_by_plan
    ]

    monthly_revenue = []
    for i in range(5, -1, -1):
        month_date  = datetime.utcnow() - timedelta(days=30 * i)
        month_start = month_date.replace(day=1, hour=0, minute=0, second=0)
        month_end   = (month_start + timedelta(days=32)).replace(day=1)
        month_total = db.session.query(db.func.sum(Payment.amount)).filter(
            Payment.status       == 'completed',
            Payment.completed_at >= month_start,
            Payment.completed_at <  month_end
        ).scalar() or 0
        monthly_revenue.append({'month': month_start.strftime('%b %Y'), 'revenue': month_total})

    all_payments = Payment.query.filter_by(status='completed') \
        .order_by(Payment.completed_at.desc()).all()

    payment_list = []
    for p in all_payments:
        user = User.query.get(p.user_id)
        payment_list.append({
            'user_name':  user.name  if user else 'Unknown',
            'user_email': user.email if user else 'N/A',
            'plan':       p.plan_name.upper(),
            'amount':     p.amount,
            'date':       p.completed_at.strftime('%d-%m-%Y %H:%M') if p.completed_at else 'N/A'
        })

    return render_template('admin_revenue.html',
        total_revenue   = total_revenue,
        plan_revenue    = plan_revenue,
        monthly_revenue = monthly_revenue,
        payments        = payment_list
    )


@admin.route('/support')
@admin_required
def support():
    # FIX 4: render correct template name (no space, lowercase)
    pending = Payment.query.filter(
        Payment.status.in_(['pending', 'submitted'])
    ).order_by(Payment.created_at.desc()).all()

    ticket_data = []
    for p in pending:
        user = User.query.get(p.user_id)
        ticket_data.append({
            'id':         p.id,
            'user_name':  user.name  if user else 'Unknown',
            'user_email': user.email if user else 'N/A',
            'subject':    f'Payment {p.status.upper()} for {p.plan_name.upper()} plan',
            'amount':     p.amount,
            'order_id':   p.rz_order_id,
            'plan':       p.plan_name,
            'created':    p.created_at.strftime('%d-%m-%Y %H:%M') if p.created_at else 'N/A',
            'status':     p.status.upper()
        })

    return render_template('admin_support.html', tickets=ticket_data)


@admin.route('/support/<int:payment_id>/resolve', methods=['POST'])
@admin_required
def resolve_payment(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    payment.status            = 'completed'
    payment.rz_payment_status = 'SUCCESS'
    payment.completed_at      = datetime.utcnow()
    db.session.commit()

    from handlers.dashboard import activate_plan
    activate_plan(payment.user_id, payment.plan_name)

    user = User.query.get(payment.user_id)
    flash(f'✅ Payment approved! {payment.plan_name.upper()} plan activated for {user.name if user else "user"}')
    return redirect(url_for('admin.support'))


@admin.route('/support/<int:payment_id>/reject', methods=['POST'])
@admin_required
def reject_payment(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    payment.status            = 'failed'
    payment.rz_payment_status = 'FAILED'
    db.session.commit()
    user = User.query.get(payment.user_id)
    flash(f'❌ Payment rejected for {user.name if user else "user"}')
    return redirect(url_for('admin.support'))


@admin.route('/manage/<int:user_id>/reset-plan', methods=['POST'])
@admin_required
def reset_user_plan(user_id):
    user = User.query.get_or_404(user_id)
    plan = UserPlan.query.filter_by(user_id=user_id, is_active=True).first()
    if plan:
        plan.plan_name   = 'free'
        plan.daily_limit = 5
        plan.expires_at  = None
        db.session.commit()
        flash(f'✅ Reset {user.name} to Free plan')
    return redirect(url_for('admin.user_details', user_id=user_id))


@admin.route('/manage/<int:user_id>/upgrade-plan/<plan_name>', methods=['POST'])
@admin_required
def upgrade_user_plan(user_id, plan_name):
    from models import PLANS
    if plan_name not in PLANS:
        flash('Invalid plan')
        return redirect(url_for('admin.user_details', user_id=user_id))

    plan_config = PLANS[plan_name]
    plan        = UserPlan.query.filter_by(user_id=user_id, is_active=True).first()
    if not plan:
        plan = UserPlan(user_id=user_id, is_active=True)
        db.session.add(plan)

    plan.plan_name   = plan_name
    plan.daily_limit = plan_config['daily_limit']
    plan.expires_at  = datetime.utcnow() + timedelta(days=plan_config['days']) \
                       if plan_config['days'] > 0 else None
    db.session.commit()
    flash(f'✅ Upgraded to {plan_name.upper()} plan')
    return redirect(url_for('admin.user_details', user_id=user_id))


@admin.route('/manage/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.email == ADMIN_EMAIL:
        flash('Cannot delete admin user')
        return redirect(url_for('admin.users'))

    user_name = user.name
    Bot.query.filter_by(user_id=user_id).delete()
    UserPlan.query.filter_by(user_id=user_id).delete()
    Payment.query.filter_by(user_id=user_id).delete()
    Booking.query.filter_by(user_id=user_id).delete()
    ConversationHistory.query.filter_by(user_id=user_id).delete()
    db.session.delete(user)
    db.session.commit()
    flash(f'✅ Deleted user {user_name}')
    return redirect(url_for('admin.users'))


@admin.route('/logout')
@admin_required
def admin_logout():
    logout_user()
    return redirect(url_for('admin.admin_login'))