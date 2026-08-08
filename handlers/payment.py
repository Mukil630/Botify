from flask import Blueprint, request, jsonify
import os, hmac, hashlib
from models import db, Payment
from handlers.dashboard import activate_plan
from datetime import datetime

payment_bp = Blueprint('payment', __name__)

WEBHOOK_SECRET = os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')

@payment_bp.route('/webhook', methods=['POST'])
def razorpay_webhook():
    try:
        webhook_secret = os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')
        signature = request.headers.get('X-Razorpay-Signature', '')

        if webhook_secret and signature:
            payload = request.get_data()
            expected_signature = hmac.new(
                webhook_secret.encode('utf-8'),
                payload,
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected_signature, signature):
                return jsonify({'error': 'Invalid signature'}), 400

        data = request.get_json()
        event_type = data.get('event')

        if event_type == 'payment.captured':
            payment_data = data.get('payload', {}).get('payment', {}).get('entity', {})
            order_id = payment_data.get('order_id')
            payment_id = payment_data.get('id')

            payment = Payment.query.filter_by(rz_order_id=order_id).first()
            if payment and payment.status != 'completed':
                payment.status = 'completed'
                payment.rz_payment_id = payment_id
                payment.rz_payment_status = 'SUCCESS'
                payment.completed_at = datetime.utcnow()
                db.session.commit()
                activate_plan(payment.user_id, payment.plan_name)

        return jsonify({'success': True}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500