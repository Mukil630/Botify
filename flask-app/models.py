from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

# ─────────────────────────────────────────────
# PLANS CONFIG
# ─────────────────────────────────────────────
PLANS = {
    'free':     {'name': 'Free',     'price': 0,   'daily_limit': 5,   'days': 0},
    'starter':  {'name': 'Starter',  'price': 49,  'daily_limit': 20,  'days': 30},
    'pro':      {'name': 'Pro',      'price': 199, 'daily_limit': 100, 'days': 30},
    'business': {'name': 'Business', 'price': 499, 'daily_limit': 500, 'days': 30},
}

# ─────────────────────────────────────────────
# BOT LIMITS PER PLAN
# ─────────────────────────────────────────────
BOT_LIMITS = {
    'free':     1,
    'starter':  3,
    'pro':      10,
    'business': 999,
}

# ─────────────────────────────────────────────
# USER
# ─────────────────────────────────────────────
class User(UserMixin, db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(100), nullable=False)
    email           = db.Column(db.String(100), unique=True, nullable=False)
    password        = db.Column(db.String(200), nullable=False)
    business_name   = db.Column(db.String(100))
    whatsapp_number = db.Column(db.String(20))
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    bots = db.relationship('Bot', backref='owner', lazy=True)

# ─────────────────────────────────────────────
# BOT
# ─────────────────────────────────────────────
class Bot(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    bot_name        = db.Column(db.String(100))
    welcome_message = db.Column(db.Text)
    features        = db.Column(db.String(200))
    is_active       = db.Column(db.Boolean, default=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

# ─────────────────────────────────────────────
# BUSINESS INFO
# ─────────────────────────────────────────────
class BusinessInfo(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    address    = db.Column(db.Text)
    timings    = db.Column(db.String(200))
    website    = db.Column(db.String(200))
    extra_info = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ─────────────────────────────────────────────
# SERVICE
# ─────────────────────────────────────────────
class Service(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    service_name = db.Column(db.String(100), nullable=False)
    price        = db.Column(db.String(50))
    description  = db.Column(db.Text)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

# ─────────────────────────────────────────────
# BOOKING
# ─────────────────────────────────────────────
class Booking(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    customer_name  = db.Column(db.String(100))
    customer_phone = db.Column(db.String(20))
    service        = db.Column(db.String(100))
    date_time      = db.Column(db.String(100))
    status         = db.Column(db.String(20), default='pending')
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

# ─────────────────────────────────────────────
# CONVERSATION HISTORY
# ─────────────────────────────────────────────
class ConversationHistory(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    booking_id     = db.Column(db.Integer, db.ForeignKey('booking.id'), nullable=True)
    customer_phone = db.Column(db.String(20), nullable=False)
    customer_name  = db.Column(db.String(100))
    message_text   = db.Column(db.Text)
    sender         = db.Column(db.String(10))
    message_type   = db.Column(db.String(20), default='text')
    timestamp      = db.Column(db.DateTime, default=datetime.utcnow)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

# ─────────────────────────────────────────────
# MESSAGE COUNT
# ─────────────────────────────────────────────
class MessageCount(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date       = db.Column(db.String(20), nullable=False)
    count      = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ─────────────────────────────────────────────
# USER PLAN
# ─────────────────────────────────────────────
class UserPlan(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_name   = db.Column(db.String(20), default='free')
    daily_limit = db.Column(db.Integer, default=5)
    price       = db.Column(db.Integer, default=0)
    expires_at  = db.Column(db.DateTime, nullable=True)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def days_left(self):
        if self.plan_name == 'free' or not self.expires_at:
            return None
        delta = self.expires_at - datetime.utcnow()
        return max(0, delta.days)

    @property
    def is_expired(self):
        if self.plan_name == 'free' or not self.expires_at:
            return False
        return self.expires_at < datetime.utcnow()

# ─────────────────────────────────────────────
# PAYMENT
# ─────────────────────────────────────────────
class Payment(db.Model):
    id                = db.Column(db.Integer, primary_key=True)
    user_id           = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_name         = db.Column(db.String(20), nullable=False)
    amount            = db.Column(db.Float, nullable=False)
    cf_order_id       = db.Column(db.String(100), unique=True)
    cf_payment_id     = db.Column(db.String(100))
    cf_payment_status = db.Column(db.String(30), default='PENDING')
    status            = db.Column(db.String(20), default='pending')
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at      = db.Column(db.DateTime, nullable=True)