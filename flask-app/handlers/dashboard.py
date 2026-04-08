from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from models import (User, Bot, Service, BusinessInfo, UserPlan, Payment,
                    Booking, ConversationHistory, MessageCount, PLANS, BOT_LIMITS, db)
import requests

dashboard = Blueprint('dashboard', __name__)

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def has_selected_plan(user_id):
    plan = UserPlan.query.filter_by(user_id=user_id).first()
    return plan is not None

def get_user_plan(user_id):
    plan = UserPlan.query.filter_by(user_id=user_id, is_active=True).first()
    if not plan:
        return None
    if plan.plan_name != 'free' and plan.expires_at and plan.expires_at < datetime.utcnow():
        plan.plan_name   = 'free'
        plan.daily_limit = PLANS['free']['daily_limit']
        plan.expires_at  = None
        db.session.commit()
    return plan

def get_or_create_plan(user_id):
    plan = UserPlan.query.filter_by(user_id=user_id, is_active=True).first()
    if not plan:
        plan = UserPlan(user_id=user_id, plan_name='free',
                        daily_limit=PLANS['free']['daily_limit'], is_active=True)
        db.session.add(plan)
        db.session.commit()
    return plan

def activate_plan(user_id, plan_key):
    plan_config = PLANS[plan_key]
    user_plan = UserPlan.query.filter_by(user_id=user_id, is_active=True).first()
    if not user_plan:
        user_plan = UserPlan(user_id=user_id, is_active=True)
        db.session.add(user_plan)
    user_plan.plan_name   = plan_key
    user_plan.daily_limit = plan_config['daily_limit']
    user_plan.price       = plan_config['price']
    user_plan.expires_at  = datetime.utcnow() + timedelta(days=plan_config['days']) \
                            if plan_config['days'] > 0 else None
    db.session.commit()

def get_today_count(user_id):
    today     = str(date.today())
    msg_count = MessageCount.query.filter_by(user_id=user_id, date=today).first()
    return msg_count.count if msg_count else 0

def has_pending_payment(user_id):
    pending = Payment.query.filter_by(user_id=user_id).filter(
        Payment.status.in_(['pending', 'submitted'])
    ).first()
    return pending is not None

# ─────────────────────────────────────────────
# HOME
# ─────────────────────────────────────────────
@dashboard.route('/')
@login_required
def index():
    if not has_selected_plan(current_user.id):
        return redirect(url_for('dashboard.welcome'))

    # ✅ Payment pending → show notice
    if has_pending_payment(current_user.id):
        flash('⏳ Your payment is under review. Admin will activate your plan soon!', 'warning')

    bots     = Bot.query.filter_by(user_id=current_user.id).all()
    services = Service.query.filter_by(user_id=current_user.id).all()
    business = BusinessInfo.query.filter_by(user_id=current_user.id).first()
    bookings = Booking.query.filter_by(user_id=current_user.id)\
                   .order_by(Booking.created_at.desc()).limit(5).all()

    plan        = get_or_create_plan(current_user.id)
    today_count = get_today_count(current_user.id)
    bot_limit   = BOT_LIMITS.get(plan.plan_name, 1)
    bot_count   = len(bots)
    can_create  = bot_count < bot_limit

    days_left = None
    if plan.expires_at:
        days_left = max(0, (plan.expires_at - datetime.utcnow()).days)

    return render_template('dashboard.html',
        bots=bots, user=current_user, services=services,
        business=business, bookings=bookings,
        plan=plan, today_count=today_count,
        daily_limit=plan.daily_limit,
        bot_limit=bot_limit, bot_count=bot_count,
        can_create=can_create, days_left=days_left)

# ─────────────────────────────────────────────
# WELCOME
# ─────────────────────────────────────────────
@dashboard.route('/welcome')
@login_required
def welcome():
    if has_selected_plan(current_user.id):
        return redirect(url_for('dashboard.index'))
    return render_template('welcome.html', user=current_user)

# ─────────────────────────────────────────────
# CHOOSE PLAN
# ─────────────────────────────────────────────
@dashboard.route('/choose-plan')
@login_required
def choose_plan():
    if has_selected_plan(current_user.id):
        return redirect(url_for('dashboard.index'))
    return render_template('choose_plan.html', plans=PLANS, user=current_user)

# ─────────────────────────────────────────────
# SELECT PLAN
# ─────────────────────────────────────────────
@dashboard.route('/select-plan/<plan_name>', methods=['POST'])
@login_required
def select_plan(plan_name):
    if plan_name not in PLANS:
        flash('Invalid plan!', 'error')
        return redirect(url_for('dashboard.choose_plan'))

    existing = UserPlan.query.filter_by(user_id=current_user.id).first()

    if plan_name == 'free':
        if not existing:
            user_plan = UserPlan(
                user_id=current_user.id, plan_name='free',
                daily_limit=PLANS['free']['daily_limit'], is_active=True
            )
            db.session.add(user_plan)
            db.session.commit()
            flash('✅ Free plan activated! Welcome to Botify!', 'success')
        return redirect(url_for('dashboard.index'))
    else:
        if not existing:
            user_plan = UserPlan(
                user_id=current_user.id, plan_name='free',
                daily_limit=PLANS['free']['daily_limit'], is_active=True
            )
            db.session.add(user_plan)
            db.session.commit()
        return redirect(url_for('dashboard.upgrade') + f'?plan={plan_name}')

# ─────────────────────────────────────────────
# UPGRADE PAGE
# ─────────────────────────────────────────────
@dashboard.route('/upgrade')
@login_required
def upgrade():
    plan = get_or_create_plan(current_user.id)
    return render_template('upgrade.html', user=current_user, plan=plan, plans=PLANS)

# ─────────────────────────────────────────────
# CREATE BOT
# ─────────────────────────────────────────────
@dashboard.route('/create-bot', methods=['GET', 'POST'])
@login_required
def create_bot():
    plan      = get_or_create_plan(current_user.id)
    bot_limit = BOT_LIMITS.get(plan.plan_name, 1)
    bot_count = Bot.query.filter_by(user_id=current_user.id).count()

    if bot_count >= bot_limit:
        flash(f'❌ Your {plan.plan_name.title()} plan allows only {bot_limit} bot(s). Upgrade!', 'error')
        return redirect(url_for('dashboard.upgrade'))

    if request.method == 'POST':
        new_bot = Bot(
            user_id=current_user.id,
            bot_name=request.form.get('bot_name'),
            welcome_message=request.form.get('welcome_message'),
            features=','.join(request.form.getlist('features')),
            is_active=False
        )
        db.session.add(new_bot)
        db.session.commit()
        flash('✅ Bot created! Now connect it to WhatsApp.', 'success')
        return redirect(url_for('dashboard.index'))

    return render_template('create_bot.html',
        user=current_user, plan=plan,
        bot_limit=bot_limit, bot_count=bot_count)

# ─────────────────────────────────────────────
# CONNECT BOT
# ─────────────────────────────────────────────
@dashboard.route('/connect/<int:bot_id>')
@login_required
def connect(bot_id):
    bot = Bot.query.get_or_404(bot_id)
    if bot.user_id != current_user.id:
        flash('❌ Unauthorized!', 'error')
        return redirect(url_for('dashboard.index'))
    return render_template('connect.html', bot=bot, user=current_user)

# ─────────────────────────────────────────────
# BUSINESS INFO
# ─────────────────────────────────────────────
@dashboard.route('/business-info', methods=['GET', 'POST'])
@login_required
def business_info():
    business = BusinessInfo.query.filter_by(user_id=current_user.id).first()
    if request.method == 'POST':
        if not business:
            business = BusinessInfo(user_id=current_user.id)
            db.session.add(business)
        business.address    = request.form.get('address')
        business.timings    = request.form.get('timings')
        business.website    = request.form.get('website')
        business.extra_info = request.form.get('extra_info')
        db.session.commit()
        flash('✅ Business info updated!', 'success')
        return redirect(url_for('dashboard.index'))
    return render_template('business_info.html', user=current_user, business=business)

# ─────────────────────────────────────────────
# ADD / DELETE SERVICE
# ─────────────────────────────────────────────
@dashboard.route('/add-service', methods=['GET', 'POST'])
@login_required
def add_service():
    if request.method == 'POST':
        service = Service(
            user_id=current_user.id,
            service_name=request.form.get('service_name'),
            price=request.form.get('price'),
            description=request.form.get('description')
        )
        db.session.add(service)
        db.session.commit()
        flash('✅ Service added!', 'success')
        return redirect(url_for('dashboard.index'))
    return render_template('add_service.html', user=current_user)

@dashboard.route('/delete-service/<int:service_id>')
@login_required
def delete_service(service_id):
    service = Service.query.filter_by(id=service_id, user_id=current_user.id).first()
    if service:
        db.session.delete(service)
        db.session.commit()
        flash('✅ Service deleted!', 'success')
    return redirect(url_for('dashboard.index'))

# ─────────────────────────────────────────────
# BOOKING DETAILS
# ─────────────────────────────────────────────
@dashboard.route('/booking/<int:booking_id>')
@login_required
def booking_details(booking_id):
    booking = Booking.query.filter_by(id=booking_id, user_id=current_user.id).first()
    if not booking:
        flash('Booking not found!', 'error')
        return redirect(url_for('dashboard.index'))
    conversations = ConversationHistory.query.filter_by(
        user_id=current_user.id, customer_phone=booking.customer_phone
    ).order_by(ConversationHistory.timestamp.asc()).all()
    return render_template('booking_details.html',
        booking=booking, conversations=conversations, user=current_user)

# ─────────────────────────────────────────────
# API: CREATE ORDER
# ─────────────────────────────────────────────
@dashboard.route('/api/create-order', methods=['POST'])
@login_required
def create_order():
    try:
        plan_key = request.get_json().get('plan')
        if plan_key not in PLANS or plan_key == 'free':
            return jsonify({'success': False, 'error': 'Invalid plan'})

        plan_config = PLANS[plan_key]
        amount      = plan_config['price']
        order_id    = f"ORD_{current_user.id}_{int(datetime.utcnow().timestamp())}"
        upi_link    = f"upi://pay?pa=mukilarasu55@oksbi&pn=Botify&am={amount}&tn=Botify+Payment&tr={order_id}"

        payment = Payment(
            user_id=current_user.id, plan_name=plan_key,
            amount=amount, cf_order_id=order_id, status='pending'
        )
        db.session.add(payment)
        db.session.commit()

        return jsonify({'success': True, 'upi_link': upi_link,
                        'order_id': order_id, 'amount': amount, 'plan': plan_key})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# API: VERIFY PAYMENT
# ─────────────────────────────────────────────
@dashboard.route('/api/verify-payment', methods=['POST'])
@login_required
def verify_payment():
    try:
        data     = request.get_json()
        order_id = data.get('order_id')

        payment = Payment.query.filter_by(
            cf_order_id=order_id, user_id=current_user.id).first()
        if not payment:
            return jsonify({'success': False, 'error': 'Payment not found'})

        payment.status = 'submitted'
        db.session.commit()

        return jsonify({'success': True,
                        'message': '✅ Payment submitted! Admin will confirm within 1 hour.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# API: PAYMENT STATUS
# ─────────────────────────────────────────────
@dashboard.route('/api/payment-status')
@login_required
def payment_status():
    try:
        plan   = get_or_create_plan(current_user.id)
        latest = Payment.query.filter_by(user_id=current_user.id)\
            .order_by(Payment.created_at.desc()).first()
        return jsonify({
            'payment_status':  latest.status if latest else 'none',
            'current_plan':    plan.plan_name,
            'plan_activated':  plan.plan_name != 'free'
        })
    except Exception as e:
        return jsonify({'payment_status': 'none', 'current_plan': 'free', 'plan_activated': False})

# ─────────────────────────────────────────────
# API: MESSAGE COUNT
# ─────────────────────────────────────────────
@dashboard.route('/api/msg-count')
@login_required
def get_msg_count():
    plan        = get_or_create_plan(current_user.id)
    today_count = get_today_count(current_user.id)
    pct         = round((today_count / plan.daily_limit) * 100) if plan.daily_limit > 0 else 0
    return jsonify({
        'today_count': today_count,
        'daily_limit': plan.daily_limit,
        'plan_name':   plan.plan_name,
        'pct':         pct,
        'remaining':   max(0, plan.daily_limit - today_count)
    })

# ─────────────────────────────────────────────
# API: CHECK LIMIT (called by Node.js)
# ✅ Plan expired → block bot completely
# ✅ Free plan → only 5 msgs/day
# ─────────────────────────────────────────────
@dashboard.route('/api/check-limit/<int:user_id>')
def check_limit(user_id):
    try:
        today = str(date.today())

        # Get raw plan without auto-expiry reset
        raw_plan = UserPlan.query.filter_by(user_id=user_id, is_active=True).first()

        # ✅ Plan expired → block completely
        if raw_plan and raw_plan.plan_name != 'free' and raw_plan.expires_at and raw_plan.expires_at < datetime.utcnow():
            return jsonify({
                'allowed': False,
                'count':   0,
                'limit':   0,
                'plan':    'expired',
                'message': '❌ Your plan has expired. Please renew to continue using the bot.'
            })

        plan = get_or_create_plan(user_id)

        msg_count = MessageCount.query.filter_by(user_id=user_id, date=today).first()
        if not msg_count:
            msg_count = MessageCount(user_id=user_id, date=today, count=0)
            db.session.add(msg_count)
            db.session.flush()

        if msg_count.count >= plan.daily_limit:
            db.session.rollback()
            return jsonify({'allowed': False, 'count': msg_count.count,
                            'limit': plan.daily_limit, 'plan': plan.plan_name})

        msg_count.count += 1
        db.session.commit()
        return jsonify({'allowed': True, 'count': msg_count.count,
                        'limit': plan.daily_limit, 'plan': plan.plan_name})
    except Exception as e:
        db.session.rollback()
        return jsonify({'allowed': True, 'count': 0, 'limit': 999})

# ─────────────────────────────────────────────
# API: START BOT
# ─────────────────────────────────────────────
@dashboard.route('/api/start-bot/<int:user_id>', methods=['POST'])
@login_required
def start_bot(user_id):
    try:
        bot      = Bot.query.filter_by(user_id=user_id).first()
        services = Service.query.filter_by(user_id=user_id).all()
        business = BusinessInfo.query.filter_by(user_id=user_id).first()

        services_text = '\n'.join([
            f"• {s.service_name} - ₹{s.price}\n  {s.description or ''}"
            for s in services
        ]) or "No services listed yet"

        botConfig = {
            'user_id':         user_id,
            'bot_name':        bot.bot_name        if bot else '',
            'welcome_message': bot.welcome_message if bot else '',
            'features':        bot.features        if bot else '',
            'business_name':   current_user.business_name   or '',
            'whatsapp_number': current_user.whatsapp_number or '',
            'services':        services_text,
            'address':         business.address    if business else 'Not provided',
            'timings':         business.timings    if business else 'Not provided',
            'extra_info':      business.extra_info if business else ''
        }

        res = requests.post(f'http://localhost:3000/start/{user_id}',
                            json=botConfig, timeout=5)
        return jsonify(res.json())
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# API: QR CODE
# ─────────────────────────────────────────────
@dashboard.route('/api/qr/<int:user_id>')
@login_required
def get_qr(user_id):
    try:
        res  = requests.get(f'http://localhost:3000/qr/{user_id}', timeout=5)
        data = res.json()
        if data.get('status') == 'connected':
            bot = Bot.query.filter_by(user_id=user_id).first()
            if bot:
                bot.is_active = True
                db.session.commit()
        return jsonify(data)
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)})

# ─────────────────────────────────────────────
# API: DISCONNECT BOT
# ─────────────────────────────────────────────
@dashboard.route('/api/disconnect/<int:user_id>')
@login_required
def disconnect_bot(user_id):
    try:
        bot = Bot.query.filter_by(user_id=user_id).first()
        if bot:
            bot.is_active = False
            db.session.commit()
        try:
            requests.get(f'http://localhost:3000/disconnect/{user_id}', timeout=5)
        except:
            pass
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# API: LOG MESSAGE (called by Node.js)
# ─────────────────────────────────────────────
@dashboard.route('/api/log-message/<int:user_id>', methods=['POST'])
def log_message(user_id):
    try:
        data         = request.get_json()
        conversation = ConversationHistory(
            user_id=user_id,
            customer_phone=data.get('customer_phone'),
            customer_name=data.get('customer_name'),
            message_text=data.get('message_text'),
            sender=data.get('sender'),
            timestamp=datetime.utcnow()
        )
        db.session.add(conversation)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# API: SAVE BOOKING (called by Node.js)
# ─────────────────────────────────────────────
@dashboard.route('/api/save-booking/<int:user_id>', methods=['POST'])
def save_booking(user_id):
    try:
        data    = request.get_json()
        booking = Booking(
            user_id=user_id,
            customer_name=data.get('customer_name'),
            customer_phone=data.get('customer_phone'),
            service=data.get('service'),
            date_time=data.get('date_time'),
            status='pending'
        )
        db.session.add(booking)
        db.session.commit()
        return jsonify({'success': True, 'booking_id': booking.id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# PAYMENT SUCCESS / FAILED
# ─────────────────────────────────────────────
@dashboard.route('/payment/success')
@login_required
def payment_success():
    plan_key  = request.args.get('plan', 'starter')
    user_plan = get_or_create_plan(current_user.id)
    return render_template('payment_success.html',
        plan=PLANS.get(plan_key, PLANS['starter']), user_plan=user_plan)

@dashboard.route('/payment/failed')
@login_required
def payment_failed():
    return render_template('payment_failed.html')

# ─────────────────────────────────────────────
# API: ACTIVE BOTS (called by Node.js on startup)
# ─────────────────────────────────────────────
@dashboard.route('/api/active-bots')
def active_bots():
    try:
        active = Bot.query.filter_by(is_active=True).all()
        bots_list = []
        for bot in active:
            user = User.query.get(bot.user_id)
            bots_list.append({
                'user_id':         bot.user_id,
                'bot_name':        bot.bot_name or '',
                'business_name':   user.business_name   if user else '',
                'whatsapp_number': user.whatsapp_number if user else '',
            })
        return jsonify({'bots': bots_list})
    except Exception as e:
        return jsonify({'bots': []})