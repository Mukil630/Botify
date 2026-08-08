# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from models import (User, Bot, Service, BusinessInfo, UserPlan, Payment,
                    Booking, ConversationHistory, MessageCount, Order,
                    BroadcastLog, Feedback, PLANS, BOT_LIMITS, db)
import requests, json, os, hmac, hashlib
from sqlalchemy import func as sqlfunc


from functools import wraps

dashboard = Blueprint('dashboard', __name__)

def require_api_token(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        secret = os.environ.get('SECRET_KEY', 'botify-super-secret-key-2025')
        if not token or token != secret:
            return jsonify({'success': False, 'error': 'Unauthorized API Request'}), 401
        return f(*args, **kwargs)
    return decorated_function

BAILEYS_URL = os.environ.get('BAILEYS_URL', 'https://luminous-kindness-production.up.railway.app')

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def has_selected_plan(user_id):
    return UserPlan.query.filter_by(user_id=user_id).first() is not None

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
    else:
        config = PLANS.get(plan.plan_name)
        if config and plan.daily_limit != config['daily_limit']:
            plan.daily_limit = config['daily_limit']
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
    return Payment.query.filter_by(user_id=user_id).filter(
        Payment.status.in_(['pending', 'submitted'])
    ).first() is not None

# ─────────────────────────────────────────────
# HOME
# ─────────────────────────────────────────────
@dashboard.route('/')
@login_required
def index():
    if not has_selected_plan(current_user.id):
        return redirect(url_for('dashboard.welcome'))

    if has_pending_payment(current_user.id):
        flash('Your payment is under review. Admin will activate your plan soon!', 'warning')

    bots     = Bot.query.filter_by(user_id=current_user.id).all()
    services = Service.query.filter_by(user_id=current_user.id).order_by(Service.sort_order, Service.created_at).all()
    business = BusinessInfo.query.filter_by(user_id=current_user.id).first()
    bookings = Booking.query.filter_by(user_id=current_user.id)\
                   .order_by(Booking.created_at.desc()).limit(5).all()
    orders   = Order.query.filter_by(user_id=current_user.id)\
                   .order_by(Order.created_at.desc()).limit(5).all()

    plan        = get_or_create_plan(current_user.id)
    today_count = get_today_count(current_user.id)
    bot_limit   = BOT_LIMITS.get(plan.plan_name, 1)
    bot_count   = len(bots)
    can_create  = bot_count < bot_limit

    today_orders    = Order.query.filter_by(user_id=current_user.id)\
                         .filter(db.func.date(Order.created_at) == date.today()).count()
    pending_orders  = Order.query.filter_by(user_id=current_user.id, status='pending').count()
    total_bookings  = Booking.query.filter_by(user_id=current_user.id).count()

    feedbacks = Feedback.query.filter_by(user_id=current_user.id).all()
    avg_rating = round(sum(f.rating for f in feedbacks) / len(feedbacks), 1) if feedbacks else 0

    days_left = None
    if plan.expires_at:
        days_left = max(0, (plan.expires_at - datetime.utcnow()).days)

    return render_template('dashboard.html',
        bots=bots, user=current_user, services=services,
        business=business, bookings=bookings, orders=orders,
        plan=plan, today_count=today_count,
        daily_limit=plan.daily_limit,
        bot_limit=bot_limit, bot_count=bot_count,
        can_create=can_create, days_left=days_left,
        today_orders=today_orders, pending_orders=pending_orders,
        total_bookings=total_bookings, avg_rating=avg_rating)

# ─────────────────────────────────────────────
# WELCOME / CHOOSE PLAN
# ─────────────────────────────────────────────
@dashboard.route('/welcome')
@login_required
def welcome():
    if has_selected_plan(current_user.id):
        return redirect(url_for('dashboard.index'))
    return render_template('welcome.html', user=current_user)

@dashboard.route('/choose-plan')
@login_required
def choose_plan():
    if has_selected_plan(current_user.id):
        return redirect(url_for('dashboard.index'))
    return render_template('choose_plan.html', plans=PLANS, user=current_user)

@dashboard.route('/select-plan/<plan_name>', methods=['POST'])
@login_required
def select_plan(plan_name):
    if plan_name not in PLANS:
        flash('Invalid plan!', 'error')
        return redirect(url_for('dashboard.upgrade'))

    existing = UserPlan.query.filter_by(user_id=current_user.id, is_active=True).first()

    if plan_name == 'free':
        if not existing:
            db.session.add(UserPlan(
                user_id=current_user.id, plan_name='free',
                daily_limit=PLANS['free']['daily_limit'], is_active=True
            ))
            db.session.commit()
            flash('Free plan activated! Welcome to Botify!', 'success')
        else:
            existing.plan_name = 'free'
            existing.daily_limit = PLANS['free']['daily_limit']
            existing.expires_at = None
            db.session.commit()
            flash('Successfully downgraded to the Free Plan!', 'success')
        return redirect(url_for('dashboard.index'))
    else:
        if not existing:
            db.session.add(UserPlan(
                user_id=current_user.id, plan_name='free',
                daily_limit=PLANS['free']['daily_limit'], is_active=True
            ))
            db.session.commit()
        return redirect(url_for('dashboard.upgrade') + f'?plan={plan_name}')

@dashboard.route('/upgrade')
@login_required
def upgrade():
    plan = get_or_create_plan(current_user.id)
    return render_template('upgrade.html', user=current_user, plan=plan, plans=PLANS)

# ─────────────────────────────────────────────
# CREATE / CONNECT BOT
# ─────────────────────────────────────────────
@dashboard.route('/create-bot', methods=['GET', 'POST'])
@login_required
def create_bot():
    plan      = get_or_create_plan(current_user.id)
    bot_limit = BOT_LIMITS.get(plan.plan_name, 1)
    bot_count = Bot.query.filter_by(user_id=current_user.id).count()

    if bot_count >= bot_limit:
        flash(f'Your {plan.plan_name.title()} plan allows only {bot_limit} bot(s). Upgrade!', 'error')
        return redirect(url_for('dashboard.upgrade'))

    if request.method == 'POST':
        db.session.add(Bot(
            user_id=current_user.id,
            bot_name=request.form.get('bot_name'),
            welcome_message=request.form.get('welcome_message'),
            features=','.join(request.form.getlist('features')),
            personality=request.form.get('personality', ''),
            is_active=False
        ))
        db.session.commit()
        flash('Bot created! Now connect it to WhatsApp.', 'success')
        return redirect(url_for('dashboard.index'))

    return render_template('create_bot.html',
        user=current_user, plan=plan,
        bot_limit=bot_limit, bot_count=bot_count)

@dashboard.route('/connect/<int:bot_id>')
@login_required
def connect(bot_id):
    bot = Bot.query.get_or_404(bot_id)
    if bot.user_id != current_user.id:
        flash('Unauthorized!', 'error')
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
        business.address       = request.form.get('address')
        business.timings       = request.form.get('timings')
        business.website       = request.form.get('website')
        business.contact_phone = request.form.get('contact_phone')
        business.contact_email = request.form.get('contact_email')
        business.extra_info    = request.form.get('extra_info')
        business.upi_id        = request.form.get('upi_id')

        upload_dir = os.path.join('static', 'business_assets')
        os.makedirs(upload_dir, exist_ok=True)

        if 'welcome_image' in request.files and request.files['welcome_image'].filename:
            img = request.files['welcome_image']
            filename = f"welcome_{current_user.id}_{int(datetime.utcnow().timestamp())}_{img.filename}"
            img.save(os.path.join(upload_dir, filename))
            business.welcome_image = f"/static/business_assets/{filename}"

        if 'menu_image' in request.files and request.files['menu_image'].filename:
            img = request.files['menu_image']
            filename = f"menu_{current_user.id}_{int(datetime.utcnow().timestamp())}_{img.filename}"
            img.save(os.path.join(upload_dir, filename))
            business.menu_image = f"/static/business_assets/{filename}"

        if 'thanks_image' in request.files and request.files['thanks_image'].filename:
            img = request.files['thanks_image']
            filename = f"thanks_{current_user.id}_{int(datetime.utcnow().timestamp())}_{img.filename}"
            img.save(os.path.join(upload_dir, filename))
            business.thanks_image = f"/static/business_assets/{filename}"

        db.session.commit()
        flash('Business info updated!', 'success')
        return redirect(url_for('dashboard.index'))
    return render_template('business_info.html', user=current_user, business=business)

# ─────────────────────────────────────────────
# MENU / SERVICES
# ─────────────────────────────────────────────
@dashboard.route('/menu')
@login_required
def menu():
    services = Service.query.filter_by(user_id=current_user.id)\
                   .order_by(Service.sort_order, Service.created_at).all()
    categories = list(dict.fromkeys(s.category or 'General' for s in services))
    return render_template('menu.html', user=current_user, services=services, categories=categories)

@dashboard.route('/add-service', methods=['GET', 'POST'])
@login_required
def add_service():
    if request.method == 'POST':
        image_url = ''
        if 'image' in request.files and request.files['image'].filename:
            img = request.files['image']
            upload_dir = os.path.join('static', 'menu_images')
            os.makedirs(upload_dir, exist_ok=True)
            filename = f"item_{current_user.id}_{int(datetime.utcnow().timestamp())}_{img.filename}"
            img.save(os.path.join(upload_dir, filename))
            image_url = f"/static/menu_images/{filename}"

        service = Service(
            user_id=current_user.id,
            service_name=request.form.get('service_name'),
            price=request.form.get('price'),
            description=request.form.get('description'),
            category=request.form.get('category', 'General'),
            image_url=image_url,
            is_available=True
        )
        db.session.add(service)
        db.session.commit()
        flash('Service added!', 'success')
        return redirect(url_for('dashboard.menu'))
    return render_template('add_service.html', user=current_user)

@dashboard.route('/toggle-service/<int:service_id>', methods=['POST'])
@login_required
def toggle_service(service_id):
    service = Service.query.filter_by(id=service_id, user_id=current_user.id).first()
    if service:
        service.is_available = not service.is_available
        db.session.commit()
        return jsonify({'success': True, 'is_available': service.is_available})
    return jsonify({'success': False})

@dashboard.route('/delete-service/<int:service_id>')
@login_required
def delete_service(service_id):
    service = Service.query.filter_by(id=service_id, user_id=current_user.id).first()
    if service:
        db.session.delete(service)
        db.session.commit()
        flash('Service deleted!', 'success')
    return redirect(url_for('dashboard.menu'))

# ─────────────────────────────────────────────
# ORDERS PAGE
# ─────────────────────────────────────────────
@dashboard.route('/orders')
@login_required
def orders():
    status_filter = request.args.get('status', 'all')
    query = Order.query.filter_by(user_id=current_user.id)
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    all_orders = query.order_by(Order.created_at.desc()).all()

    for o in all_orders:
        try:
            o.items_list = json.loads(o.items) if o.items else []
        except:
            o.items_list = []

    counts = {
        'all':       Order.query.filter_by(user_id=current_user.id).count(),
        'pending':   Order.query.filter_by(user_id=current_user.id, status='pending').count(),
        'confirmed': Order.query.filter_by(user_id=current_user.id, status='confirmed').count(),
        'preparing': Order.query.filter_by(user_id=current_user.id, status='preparing').count(),
        'ready':     Order.query.filter_by(user_id=current_user.id, status='ready').count(),
        'delivered': Order.query.filter_by(user_id=current_user.id, status='delivered').count(),
    }
    return render_template('orders.html', orders=all_orders, counts=counts,
                           status_filter=status_filter, user=current_user)

@dashboard.route('/api/update-order-status/<int:order_id>', methods=['POST'])
@login_required
def update_order_status(order_id):
    order = Order.query.filter_by(id=order_id, user_id=current_user.id).first()
    if not order:
        return jsonify({'success': False, 'error': 'Not found'})
    new_status = request.get_json().get('status')
    if new_status in ['pending', 'confirmed', 'preparing', 'ready', 'delivered', 'cancelled']:
        order.status = new_status
        db.session.commit()

        if new_status == 'ready' or new_status == 'delivered':
            try:
                items_list = json.loads(order.items) if order.items else []
                requests.post(
                    f'{BAILEYS_URL}/send-order-update/{current_user.id}',
                    json={
                        'customer_phone': order.customer_phone,
                        'customer_name': order.customer_name,
                        'order_id': order.id,
                        'status': new_status,
                        'items': items_list,
                        'total': order.total_amount
                    },
                    timeout=5
                )
                if new_status == 'ready':
                    requests.post(
                        f'{BAILEYS_URL}/send-bill/{current_user.id}',
                        json={
                            'order_id': order.id,
                            'customer_phone': order.customer_phone,
                            'customer_name': order.customer_name
                        },
                        timeout=10
                    )
            except Exception as e:
                print(f'Error sending WhatsApp update: {e}')

        return jsonify({'success': True, 'status': new_status})
    return jsonify({'success': False, 'error': 'Invalid status'})

# ─────────────────────────────────────────────
# BOOKINGS PAGE
# ─────────────────────────────────────────────
@dashboard.route('/bookings')
@login_required
def bookings():
    all_bookings = Booking.query.filter_by(user_id=current_user.id)\
                       .order_by(Booking.created_at.desc()).all()
    return render_template('bookings.html', bookings=all_bookings, user=current_user)

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
# ANALYTICS PAGE
# ─────────────────────────────────────────────
@dashboard.route('/analytics')
@login_required
def analytics():
    uid = current_user.id

    msg_data = []
    for i in range(6, -1, -1):
        d     = (date.today() - timedelta(days=i)).isoformat()
        count = MessageCount.query.filter_by(user_id=uid, date=d).first()
        msg_data.append({'date': d, 'count': count.count if count else 0})

    order_data = []
    for i in range(6, -1, -1):
        d     = date.today() - timedelta(days=i)
        count = Order.query.filter_by(user_id=uid)\
                    .filter(db.func.date(Order.created_at) == d).count()
        revenue = db.session.query(db.func.sum(Order.total_amount))\
                      .filter_by(user_id=uid)\
                      .filter(db.func.date(Order.created_at) == d)\
                      .scalar() or 0
        order_data.append({'date': d.isoformat(), 'count': count, 'revenue': revenue})

    total_messages = sum(m['count'] for m in msg_data)
    total_orders   = Order.query.filter_by(user_id=uid).count()
    total_revenue  = db.session.query(db.func.sum(Order.total_amount))\
                         .filter_by(user_id=uid).scalar() or 0
    total_bookings = Booking.query.filter_by(user_id=uid).count()

    customers = db.session.query(ConversationHistory.customer_phone, ConversationHistory.customer_name)\
                    .filter_by(user_id=uid).distinct(ConversationHistory.customer_phone).all()

    feedbacks  = Feedback.query.filter_by(user_id=uid).order_by(Feedback.created_at.desc()).all()
    avg_rating = round(sum(f.rating for f in feedbacks) / len(feedbacks), 1) if feedbacks else 0

    from sqlalchemy import func
    top_services = db.session.query(Order.items).filter_by(user_id=uid).all()
    service_counts = {}
    for row in top_services:
        try:
            items = json.loads(row.items or '[]')
            for item in items:
                name = item.get('name', '')
                service_counts[name] = service_counts.get(name, 0) + item.get('qty', 1)
        except:
            pass
    top_services_list = sorted(service_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    return render_template('analytics.html',
        user=current_user,
        msg_data=json.dumps(msg_data),
        order_data=json.dumps(order_data),
        total_messages=total_messages,
        total_orders=total_orders,
        total_revenue=total_revenue,
        total_bookings=total_bookings,
        customers=customers,
        feedbacks=feedbacks,
        avg_rating=avg_rating,
        top_services=top_services_list)

# ─────────────────────────────────────────────
# BROADCAST PAGE
# ─────────────────────────────────────────────
@dashboard.route('/broadcast')
@login_required
def broadcast():
    history = BroadcastLog.query.filter_by(user_id=current_user.id)\
                  .order_by(BroadcastLog.sent_at.desc()).limit(20).all()
    customers = db.session.query(
        ConversationHistory.customer_phone,
        ConversationHistory.customer_name
    ).filter_by(user_id=current_user.id)\
     .distinct(ConversationHistory.customer_phone).all()

    return render_template('broadcast.html', user=current_user,
                           history=history, customer_count=len(customers))

@dashboard.route('/api/send-broadcast', methods=['POST'])
@login_required
def send_broadcast():
    try:
        data    = request.get_json()
        message = data.get('message', '').strip()
        if not message:
            return jsonify({'success': False, 'error': 'Message is empty'})

        customers = db.session.query(
            ConversationHistory.customer_phone,
            ConversationHistory.customer_name
        ).filter_by(user_id=current_user.id)\
         .distinct(ConversationHistory.customer_phone).all()

        if not customers:
            return jsonify({'success': False, 'error': 'No customers found to broadcast to'})

        phones = [c.customer_phone for c in customers]

        res = requests.post(
            f'{BAILEYS_URL}/broadcast/{current_user.id}',
            json={'message': message, 'phones': phones},
            timeout=10
        )

        log = BroadcastLog(
            user_id=current_user.id,
            message=message,
            recipient_count=len(phones),
            status='sent'
        )
        db.session.add(log)
        db.session.commit()

        return jsonify({'success': True, 'sent_to': len(phones)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# FEEDBACK
# ─────────────────────────────────────────────
@dashboard.route('/feedback')
@login_required
def feedback():
    feedbacks = Feedback.query.filter_by(user_id=current_user.id).order_by(Feedback.created_at.desc()).all()
    avg_rating = round(sum(f.rating for f in feedbacks) / len(feedbacks), 1) if feedbacks else 0
    return render_template('feedback.html', user=current_user, feedbacks=feedbacks, avg_rating=avg_rating)

@dashboard.route('/api/save-feedback/<int:user_id>', methods=['POST'])
@require_api_token
def save_feedback(user_id):
    try:
        data = request.get_json()
        fb = Feedback(
            user_id=user_id,
            customer_phone=data.get('customer_phone'),
            customer_name=data.get('customer_name'),
            rating=int(data.get('rating', 0)),
            comment=data.get('comment', '')
        )
        db.session.add(fb)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# NODE.JS APIs
# ─────────────────────────────────────────────
@dashboard.route('/api/get-menu/<int:user_id>')
@require_api_token
def get_menu(user_id):
    try:
        services = Service.query.filter_by(user_id=user_id, is_available=True)\
                       .order_by(Service.category, Service.sort_order).all()
        menu = [{'name': s.service_name, 'price': s.price,
                 'description': s.description, 'category': s.category or 'General'}
                for s in services]
        return jsonify({'success': True, 'menu': menu})
    except Exception as e:
        return jsonify({'success': False, 'menu': []})

@dashboard.route('/api/save-order/<int:user_id>', methods=['POST'])
@require_api_token
def save_order(user_id):
    try:
        data  = request.get_json()
        order = Order(
            user_id=user_id,
            customer_phone=data.get('customer_phone'),
            customer_name=data.get('customer_name'),
            items=json.dumps(data.get('items', [])),
            total_amount=data.get('total_amount', 0),
            status='pending',
            notes=data.get('notes', '')
        )
        db.session.add(order)
        db.session.commit()
        return jsonify({'success': True, 'order_id': order.id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@dashboard.route('/api/check-limit/<int:user_id>', methods=['GET', 'POST'])
@require_api_token
def check_limit(user_id):
    try:
        today = str(date.today())
        raw_plan = UserPlan.query.filter_by(user_id=user_id, is_active=True).first()

        if raw_plan and raw_plan.plan_name != 'free' and raw_plan.expires_at and raw_plan.expires_at < datetime.utcnow():
            return jsonify({'allowed': False, 'count': 0, 'limit': 0, 'plan': 'expired',
                            'message': 'Your plan has expired. Please renew.'})

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

@dashboard.route('/api/log-message/<int:user_id>', methods=['POST'])
@require_api_token
def log_message(user_id):
    try:
        data = request.get_json()
        db.session.add(ConversationHistory(
            user_id=user_id,
            customer_phone=data.get('customer_phone'),
            customer_name=data.get('customer_name'),
            message_text=data.get('message_text'),
            sender=data.get('sender'),
            timestamp=datetime.utcnow()
        ))
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@dashboard.route('/api/save-booking/<int:user_id>', methods=['POST'])
@require_api_token
def save_booking(user_id):
    try:
        data    = request.get_json()
        booking = Booking(
            user_id=user_id,
            customer_name=data.get('customer_name'),
            customer_phone=data.get('customer_phone'),
            service=data.get('service'),
            date_time=data.get('date_time'),
            status='confirmed'
        )
        db.session.add(booking)
        db.session.commit()
        return jsonify({'success': True, 'booking_id': booking.id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@dashboard.route('/api/check-booking-slot/<int:user_id>')
@require_api_token
def check_booking_slot(user_id):
    service   = request.args.get('service', '').strip()
    date_time = request.args.get('date_time', '').strip()
    if not service or not date_time:
        return jsonify({'available': True})
    try:
        existing = Booking.query.filter_by(
            user_id=user_id, service=service, date_time=date_time, status='confirmed'
        ).first()
        if existing:
            return jsonify({'available': False,
                            'available_slots': ['10:00 am','11:00 am','12:00 pm','1:00 pm',
                                                '2:00 pm','3:00 pm','4:00 pm','5:00 pm','6:00 pm']})
        return jsonify({'available': True})
    except Exception as e:
        return jsonify({'available': True})

@dashboard.route('/api/active-bots')
@require_api_token
def active_bots():
    try:
        active    = Bot.query.filter_by(is_active=True).all()
        bots_list = []
        for bot in active:
            user     = User.query.get(bot.user_id)
            business = BusinessInfo.query.filter_by(user_id=bot.user_id).first()
            services = Service.query.filter_by(user_id=bot.user_id, is_available=True).all()
            services_text = '\n'.join([f"- {s.service_name} - Rs.{s.price}\n  {s.description or ''}"
                                       for s in services]) or "No services listed yet"
            bots_list.append({
                'user_id':         bot.user_id,
                'bot_name':        bot.bot_name         or '',
                'business_name':   user.business_name   if user else '',
                'whatsapp_number': user.whatsapp_number if user else '',
                'contact_phone':   business.contact_phone if business else '',
                'contact_email':   business.contact_email if business else '',
                'welcome_message': bot.welcome_message  or '',
                'features':        bot.features         or '',
                'personality':     bot.personality      or '',
                'address':         business.address     if business else '',
                'timings':         business.timings     if business else '',
                'website':         business.website    if business else '',
                'extra_info':      business.extra_info  if business else '',
                'menu_image':      business.menu_image  if business else '',
                'thanks_image':    business.thanks_image if business else '',
                'welcome_image':   business.welcome_image if business else '',
                'upi_id':          business.upi_id       if business else '',
                'services':        services_text,
            })
        return jsonify({'bots': bots_list})
    except Exception as e:
        return jsonify({'bots': []})

@dashboard.route('/api/start-bot/<int:user_id>', methods=['POST'])
@login_required
def start_bot(user_id):
    try:
        bot      = Bot.query.filter_by(user_id=user_id).first()
        services = Service.query.filter_by(user_id=user_id, is_available=True).all()
        business = BusinessInfo.query.filter_by(user_id=user_id).first()

        services_text = '\n'.join([
            f"- {s.service_name} - Rs.{s.price}\n  {s.description or ''}"
            for s in services
        ]) or "No services listed yet"

        botConfig = {
            'user_id':         user_id,
            'bot_name':        bot.bot_name        if bot else '',
            'welcome_message': bot.welcome_message if bot else '',
            'features':        bot.features        if bot else '',
            'personality':     bot.personality     if bot else '',
            'business_name':   current_user.business_name   or '',
            'whatsapp_number': current_user.whatsapp_number or '',
            'services':        services_text,
            'address':         business.address    if business else 'Not provided',
            'timings':         business.timings    if business else 'Not provided',
            'contact_phone':   business.contact_phone if business else '',
            'contact_email':   business.contact_email if business else '',
            'website':         business.website    if business else '',
            'extra_info':      business.extra_info if business else '',
            'menu_image':      business.menu_image if business else '',
            'thanks_image':    business.thanks_image if business else '',
            'welcome_image':   business.welcome_image if business else '',
            'upi_id':          business.upi_id     if business else ''
        }

        res = requests.post(f'{BAILEYS_URL}/start/{user_id}', json=botConfig, timeout=5)
        return jsonify(res.json())
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@dashboard.route('/api/qr/<int:user_id>')
@login_required
def get_qr(user_id):
    try:
        res  = requests.get(f'{BAILEYS_URL}/qr/{user_id}', timeout=5)
        data = res.json()
        if data.get('status') == 'connected':
            bot = Bot.query.filter_by(user_id=user_id).first()
            if bot:
                bot.is_active = True
                db.session.commit()
        return jsonify(data)
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)})

@dashboard.route('/api/disconnect/<int:user_id>')
@login_required
def disconnect_bot(user_id):
    try:
        bot = Bot.query.filter_by(user_id=user_id).first()
        if bot:
            bot.is_active = False
            db.session.commit()
        try:
            requests.get(f'{BAILEYS_URL}/disconnect/{user_id}', timeout=5)
        except:
            pass
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@dashboard.route('/api/msg-count')
@login_required
def get_msg_count():
    plan        = get_or_create_plan(current_user.id)
    today_count = get_today_count(current_user.id)
    pct         = round((today_count / plan.daily_limit) * 100) if plan.daily_limit > 0 else 0
    return jsonify({
        'today_count': today_count, 'daily_limit': plan.daily_limit,
        'plan_name': plan.plan_name, 'pct': pct,
        'remaining': max(0, plan.daily_limit - today_count)
    })

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

@dashboard.route('/api/create-order', methods=['POST'])
@login_required
def create_order():
    try:
        plan_key    = request.get_json().get('plan')
        if plan_key not in PLANS or plan_key == 'free':
            return jsonify({'success': False, 'error': 'Invalid plan'})
        plan_config = PLANS[plan_key]
        amount      = plan_config['price']
        order_id    = f"ORD_{current_user.id}_{int(datetime.utcnow().timestamp())}"
        upi_link    = f"upi://pay?pa=mukilarasu55@oksbi&pn=Botify&am={amount}&tn=Botify+Payment&tr={order_id}"

        db.session.add(Payment(
            user_id=current_user.id, plan_name=plan_key,
            amount=amount, rz_order_id=order_id, status='pending'
        ))
        db.session.commit()
        return jsonify({'success': True, 'upi_link': upi_link,
                        'order_id': order_id, 'amount': amount, 'plan': plan_key})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@dashboard.route('/api/verify-payment', methods=['POST'])
@login_required
def verify_payment():
    try:
        data     = request.get_json()
        order_id = data.get('order_id')
        payment  = Payment.query.filter_by(rz_order_id=order_id, user_id=current_user.id).first()
        if not payment:
            return jsonify({'success': False, 'error': 'Payment not found'})
        payment.status = 'submitted'
        db.session.commit()
        return jsonify({'success': True, 'message': 'Payment submitted! Admin will confirm within 1 hour.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@dashboard.route('/api/create-razorpay-order', methods=['POST'])
@login_required
def create_razorpay_order():
    try:
        plan_key = request.get_json().get('plan')
        if plan_key not in PLANS or plan_key == 'free':
            return jsonify({'success': False, 'error': 'Invalid plan'})

        plan_config = PLANS[plan_key]
        order_id = f"ORD_{current_user.id}_{int(datetime.utcnow().timestamp())}"
        PAYMENT_UPI_ID = os.environ.get('PAYMENT_UPI_ID', 'mukilarasu@upi')

        upi_link = f"upi://pay?pa={PAYMENT_UPI_ID}&pn=Botify&am={plan_config['price']}&cu=INR&tn=Botify_{plan_config['name']}_Plan_{order_id}"

        db.session.add(Payment(
            user_id=current_user.id,
            plan_name=plan_key,
            amount=plan_config['price'],
            rz_order_id=order_id,
            status='pending'
        ))
        db.session.commit()

        return jsonify({
            'success': True,
            'upi_qr': True,
            'upi_link': upi_link,
            'upi_id': PAYMENT_UPI_ID,
            'plan': plan_key,
            'plan_name': plan_config['name'],
            'amount': plan_config['price'],
            'order_id': order_id
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@dashboard.route('/api/submit-upi-payment', methods=['POST'])
@login_required
def submit_upi_payment():
    try:
        data = request.get_json()
        order_id = data.get('order_id')
        utr = data.get('utr', '').strip()

        if not order_id:
            return jsonify({'success': False, 'error': 'Order ID required'})

        payment = Payment.query.filter_by(rz_order_id=order_id, user_id=current_user.id).first()
        if not payment:
            return jsonify({'success': False, 'error': 'Payment not found'})

        if utr:
            payment.rz_payment_id = utr
        payment.status = 'submitted'
        payment.rz_payment_status = 'UPI_SUBMITTED'
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'Payment reference submitted! Admin will verify and activate your {payment.plan_name.title()} plan shortly.'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@dashboard.route('/api/orders-live')
@login_required
def orders_live():
    orders = Order.query.filter_by(user_id=current_user.id)\
                 .order_by(Order.created_at.desc()).limit(20).all()
    result = []
    for o in orders:
        try:
            items_list = json.loads(o.items or '[]')
        except:
            items_list = []
        result.append({
            'id': o.id, 'customer_name': o.customer_name,
            'customer_phone': o.customer_phone, 'status': o.status,
            'total_amount': o.total_amount, 'items': items_list,
            'created_at': o.created_at.strftime('%d %b %H:%M')
        })
    return jsonify({'orders': result})

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
# PDF BILL GENERATION
# ─────────────────────────────────────────────
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, HRFlowable
from reportlab.lib.units import inch
import io

@dashboard.route('/api/generate-bill/<int:order_id>')
def generate_bill(order_id):
    try:
        order = Order.query.get(order_id)
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'})

        user = User.query.get(order.user_id)
        business = BusinessInfo.query.filter_by(user_id=order.user_id).first()

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.4*inch, bottomMargin=0.4*inch, leftMargin=0.5*inch, rightMargin=0.5*inch)
        elements = []
        styles = getSampleStyleSheet()

        invoice_title = ParagraphStyle('InvoiceTitle', parent=styles['Heading1'], fontSize=24, textColor=colors.HexColor('#1a1a2e'),
                                      spaceAfter=5, alignment=1, fontName='Helvetica-Bold')
        business_name_style = ParagraphStyle('BusinessName', parent=styles['Heading1'], fontSize=18, textColor=colors.HexColor('#0f3460'),
                                            spaceAfter=12, alignment=0, fontName='Helvetica-Bold')
        label_style = ParagraphStyle('Label', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#666666'), spaceAfter=3)

        elements.append(Paragraph('INVOICE', invoice_title))
        elements.append(Spacer(1, 8))

        business_name = user.business_name if user else 'Business'
        elements.append(Paragraph(f'<b>{business_name}</b>', business_name_style))

        if business and business.address:
            elements.append(Paragraph(f'📍 {business.address}', label_style))
        if business and business.contact_phone:
            elements.append(Paragraph(f'📞 {business.contact_phone}', label_style))
        if business and business.contact_email:
            elements.append(Paragraph(f'✉️ {business.contact_email}', label_style))

        elements.append(Spacer(1, 12))
        elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#cccccc")))
        elements.append(Spacer(1, 10))

        order_details = [
            ['<b>INVOICE DETAILS</b>', '<b>CUSTOMER DETAILS</b>'],
            [
                f'Invoice #: {order.id}\nDate: {order.created_at.strftime("%d-%m-%Y")}\nTime: {order.created_at.strftime("%H:%M")}',
                f'Name: {order.customer_name or "N/A"}\nPhone: {order.customer_phone or "N/A"}\nStatus: {order.status.upper()}'
            ]
        ]

        details_table = Table(order_details, colWidths=[3.5*inch, 3.5*inch])
        details_table.setStyle(TableStyle([
            ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 10),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#0f3460')),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f0f0f0')),
            ('FONT', (0, 1), (-1, 1), 'Helvetica', 9),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dddddd')),
            ('TOPPADDING', (0, 1), (-1, 1), 8),
            ('BOTTOMPADDING', (0, 1), (-1, 1), 8),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(details_table)
        elements.append(Spacer(1, 15))

        items = json.loads(order.items) if order.items else []
        if items:
            subtotal = 0
            table_data = [['<b>Item Description</b>', '<b>Qty</b>', '<b>Unit Price</b>', '<b>Amount</b>']]

            for item in items:
                name = item.get('name', 'Item')
                qty = item.get('qty', 1)
                price = float(item.get('price', 0))
                total = qty * price
                subtotal += total
                table_data.append([name, str(qty), f'₹{price:.2f}', f'₹{total:.2f}'])

            items_table = Table(table_data, colWidths=[3.2*inch, 0.8*inch, 1.2*inch, 1.2*inch])
            items_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f3460')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 0), (-1, 0), 8),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#fafafa')),
                ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
                ('ALIGN', (0, 1), (0, -1), 'LEFT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('TOPPADDING', (0, 1), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dddddd')),
            ]))
            elements.append(items_table)
            elements.append(Spacer(1, 15))

            gst_rate = business.gst_rate if business else 18.0
            gst_amount = (subtotal * gst_rate) / 100
            total_amount = subtotal + gst_amount

            summary_data = [
                ['', 'Subtotal:', f'₹{subtotal:.2f}'],
                ['', f'GST ({gst_rate}%):', f'₹{gst_amount:.2f}'],
                ['', '<b>TOTAL AMOUNT:</b>', f'<b>₹{total_amount:.2f}</b>'],
            ]

            summary_table = Table(summary_data, colWidths=[3.2*inch, 1.2*inch, 1.2*inch])
            summary_table.setStyle(TableStyle([
                ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
                ('FONTNAME', (1, 0), (1, 1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('FONTNAME', (1, 2), (-1, 2), 'Helvetica-Bold'),
                ('FONTSIZE', (1, 2), (-1, 2), 11),
                ('BACKGROUND', (0, 2), (-1, 2), colors.HexColor('#0f3460')),
                ('TEXTCOLOR', (0, 2), (-1, 2), colors.whitesmoke),
                ('TOPPADDING', (0, 2), (-1, 2), 8),
                ('BOTTOMPADDING', (0, 2), (-1, 2), 8),
            ]))
            elements.append(summary_table)

        elements.append(Spacer(1, 20))
        elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#cccccc")))
        elements.append(Spacer(1, 10))

        footer_style = ParagraphStyle('Footer', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#888888'),
                                     spaceAfter=3, alignment=1)
        elements.append(Paragraph('Thank you for your business! 🙏', footer_style))
        if business and business.extra_info:
            elements.append(Paragraph(f'{business.extra_info}', footer_style))
        elements.append(Paragraph('For support, please contact us via WhatsApp', footer_style))
        elements.append(Paragraph(f'Generated on {datetime.now().strftime("%d-%m-%Y %H:%M:%S")}',
                                 ParagraphStyle('Timestamp', parent=styles['Normal'], fontSize=8, textColor=colors.HexColor('#aaaaaa'), alignment=1)))

        doc.build(elements)
        buffer.seek(0)

        return buffer.getvalue(), 200, {
            'Content-Type': 'application/pdf',
            'Content-Disposition': f'attachment; filename=Invoice_{order_id}.pdf'
        }
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@dashboard.route('/api/get-order-bill/<int:order_id>')
def get_order_bill(order_id):
    try:
        order = Order.query.get(order_id)
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'})

        items = json.loads(order.items) if order.items else []
        user = User.query.get(order.user_id)
        business = BusinessInfo.query.filter_by(user_id=order.user_id).first()

        subtotal = sum(float(item.get('price', 0)) * item.get('qty', 1) for item in items)
        gst_rate = business.gst_rate if business else 18.0
        gst_amount = (subtotal * gst_rate) / 100
        total_with_gst = subtotal + gst_amount

        return jsonify({
            'success': True,
            'order_id': order.id,
            'customer_name': order.customer_name,
            'customer_phone': order.customer_phone,
            'items': items,
            'subtotal': round(subtotal, 2),
            'gst_rate': gst_rate,
            'gst_amount': round(gst_amount, 2),
            'total': round(total_with_gst, 2),
            'date': order.created_at.strftime('%d-%m-%Y %H:%M'),
            'status': order.status,
            'business_name': user.business_name if user else 'Business',
            'business_address': business.address if business else '',
            'business_phone': business.contact_phone if business else '',
            'business_email': business.contact_email if business else '',
            'gst_number': business.gst_number if business else ''
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@dashboard.route('/api/customer-orders/<int:user_id>/<phone>')
def get_customer_orders(user_id, phone):
    try:
        phone_clean = phone.replace('@s.whatsapp.net', '').replace('%40s.whatsapp.net', '')
        orders = Order.query.filter_by(user_id=user_id, customer_phone=phone_clean)\
            .order_by(Order.created_at.desc()).limit(10).all()

        order_list = []
        for o in orders:
            items = json.loads(o.items) if o.items else []
            order_list.append({
                'order_id': o.id,
                'date': o.created_at.strftime('%d-%m-%Y'),
                'items': [i['name'] for i in items] if items else [],
                'total': o.total_amount,
                'status': o.status
            })
        return jsonify({'success': True, 'orders': order_list})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─────────────────────────────────────────────
# LIVE INBOX
# ─────────────────────────────────────────────
@dashboard.route('/live-chat')
@login_required
def live_chat():
    return render_template('live_chat.html', user=current_user)

@dashboard.route('/api/live-inbox/chats')
@login_required
def get_live_chats():
    try:
        subquery = db.session.query(
            ConversationHistory.customer_phone,
            db.func.max(ConversationHistory.timestamp).label('max_time')
        ).filter_by(user_id=current_user.id).group_by(ConversationHistory.customer_phone).subquery()

        last_msgs = db.session.query(ConversationHistory).join(
            subquery,
            db.and_(
                ConversationHistory.customer_phone == subquery.c.customer_phone,
                ConversationHistory.timestamp == subquery.c.max_time
            )
        ).filter(ConversationHistory.user_id == current_user.id).order_by(ConversationHistory.timestamp.desc()).all()

        chats = []
        for m in last_msgs:
            chats.append({
                'phone': m.customer_phone,
                'name': m.customer_name,
                'last_msg': m.message_text[:40] + ('...' if len(m.message_text) > 40 else ''),
                'last_time': m.timestamp.isoformat()
            })
        return jsonify({'success': True, 'chats': chats})
    except Exception as e:
        return jsonify({'success': False, 'chats': []})

@dashboard.route('/api/live-inbox/messages/<phone>')
@login_required
def get_live_messages(phone):
    try:
        msgs = ConversationHistory.query.filter_by(
            user_id=current_user.id, customer_phone=phone
        ).order_by(ConversationHistory.timestamp.asc()).all()

        return jsonify({
            'success': True,
            'messages': [{'text': m.message_text, 'sender': m.sender, 'time': m.timestamp.isoformat()} for m in msgs]
        })
    except Exception as e:
        return jsonify({'success': False, 'messages': []})

@dashboard.route('/api/live-inbox/send', methods=['POST'])
@login_required
def send_live_message():
    try:
        data = request.get_json()
        phone = data.get('phone')
        message = data.get('message')

        if not message or not phone:
            return jsonify({'success': False, 'error': 'Missing data'})

        res = requests.post(f'{BAILEYS_URL}/send-manual/{current_user.id}', json={
            'phone': phone, 'message': message
        }, timeout=5)

        if res.json().get('success'):
            log = ConversationHistory(
                user_id=current_user.id,
                customer_phone=phone,
                customer_name='',
                message_text=message,
                sender='business'
            )
            db.session.add(log)
            db.session.commit()
            return jsonify({'success': True})

        return jsonify({'success': False, 'error': 'Failed to send'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@dashboard.route('/api/live-inbox/toggle-bot', methods=['POST'])
@login_required
def toggle_bot():
    try:
        data = request.get_json()
        phone = data.get('phone')
        paused = data.get('paused', True)

        res = requests.post(f'{BAILEYS_URL}/toggle-bot/{current_user.id}', json={
            'phone': phone, 'paused': paused
        }, timeout=5)

        return jsonify({'success': True, 'paused': paused})
    except Exception as e:
        return jsonify({'success': False})

@dashboard.route('/api/live-inbox/bot-status/<phone>')
@login_required
def check_bot_status(phone):
    try:
        res = requests.get(f'{BAILEYS_URL}/bot-status/{current_user.id}/{phone}', timeout=5)
        return jsonify(res.json())
    except:
        return jsonify({'paused': False})
