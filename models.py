from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

PLANS = {
    'free':       {'name': 'Free',       'price': 0,    'daily_limit': 8,      'days': 0},
    'starter':    {'name': 'Starter',    'price': 49,   'daily_limit': 35,     'days': 30},
    'pro':        {'name': 'Pro',        'price': 199,  'daily_limit': 120,    'days': 30},
    'growth':     {'name': 'Growth',     'price': 299,  'daily_limit': 500,    'days': 30},
    'business':   {'name': 'Business',   'price': 499,  'daily_limit': 800,    'days': 30},
    'enterprise': {'name': 'Enterprise', 'price': 999,  'daily_limit': 99999,  'days': 30},
}

# Safety: Each message has a 1-second delay to prevent rate limiting
MSG_DELAY_SECONDS = 1

BOT_LIMITS = {
    'free':       1,
    'starter':    3,
    'pro':        5,
    'growth':     10,
    'business':   20,
    'enterprise': 999,
}

SUPPORT_CONFIG = {
    'email':    'mukilarasu2005@gmail.com',
    'phone':    '+91 9080030538',
    'whatsapp': '919080030538',
    'name':     'Botify Support',
}

class User(UserMixin, db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(100), nullable=False)
    email           = db.Column(db.String(100), unique=True, nullable=False)
    password        = db.Column(db.String(200), nullable=False)
    business_name   = db.Column(db.String(100))
    whatsapp_number = db.Column(db.String(20))
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    bots = db.relationship('Bot', backref='owner', lazy=True)
    payments = db.relationship('Payment', backref='user', lazy=True)

class Bot(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    bot_name        = db.Column(db.String(100))
    welcome_message = db.Column(db.Text)
    features        = db.Column(db.String(200))
    is_active       = db.Column(db.Boolean, default=False)
    personality     = db.Column(db.Text)       # Custom AI personality
    language        = db.Column(db.String(10), default='auto')
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

class BusinessInfo(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    address       = db.Column(db.Text)
    timings       = db.Column(db.String(200))
    website       = db.Column(db.String(200))
    extra_info    = db.Column(db.Text)
    contact_phone = db.Column(db.String(20))
    contact_email = db.Column(db.String(100))
    menu_image    = db.Column(db.String(300))
    thanks_image  = db.Column(db.String(300))
    welcome_image = db.Column(db.String(300))
    upi_id        = db.Column(db.String(100))
    gst_rate      = db.Column(db.Float, default=18.0)
    gst_number    = db.Column(db.String(50))
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

class Service(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    service_name = db.Column(db.String(100), nullable=False)
    price        = db.Column(db.String(50))
    duration     = db.Column(db.String(50))
    description  = db.Column(db.Text)
    category     = db.Column(db.String(50), default='General')
    image_url    = db.Column(db.String(300))
    is_available = db.Column(db.Boolean, default=True)
    sort_order   = db.Column(db.Integer, default=0)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

class Booking(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    customer_name  = db.Column(db.String(100))
    customer_phone = db.Column(db.String(20))
    service        = db.Column(db.String(100))
    date_time      = db.Column(db.String(100))
    status         = db.Column(db.String(20), default='pending')
    payment_status = db.Column(db.String(20), default='unpaid')
    reminder_sent  = db.Column(db.Boolean, default=False)
    notes          = db.Column(db.Text)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

class Order(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    customer_phone = db.Column(db.String(20))
    customer_name  = db.Column(db.String(100))
    items          = db.Column(db.Text)   # JSON: [{"name":"Biryani","qty":2,"price":150}]
    total_amount   = db.Column(db.Float, default=0)
    status         = db.Column(db.String(20), default='pending')   # pending/confirmed/preparing/ready/delivered/cancelled
    payment_status = db.Column(db.String(20), default='unpaid')    # unpaid/paid
    notes          = db.Column(db.Text)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

class BroadcastLog(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message         = db.Column(db.Text, nullable=False)
    recipient_count = db.Column(db.Integer, default=0)
    status          = db.Column(db.String(20), default='sent')
    sent_at         = db.Column(db.DateTime, default=datetime.utcnow)

class Feedback(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    booking_id     = db.Column(db.Integer, db.ForeignKey('booking.id'), nullable=True)
    customer_phone = db.Column(db.String(20))
    customer_name  = db.Column(db.String(100))
    rating         = db.Column(db.Integer)   # 1–5
    comment        = db.Column(db.Text)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

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

class MessageCount(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date       = db.Column(db.String(20), nullable=False)
    count      = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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

class Payment(db.Model):
    id                = db.Column(db.Integer, primary_key=True)
    user_id           = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_name         = db.Column(db.String(20), nullable=False)
    amount            = db.Column(db.Float, nullable=False)
    rz_order_id       = db.Column(db.String(100), unique=True)
    rz_payment_id     = db.Column(db.String(100))
    rz_payment_status = db.Column(db.String(30), default='PENDING')
    status            = db.Column(db.String(20), default='pending')
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at      = db.Column(db.DateTime, nullable=True)
