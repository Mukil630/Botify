"""
Reminder automation — runs every 15 minutes via APScheduler.
Sends WhatsApp reminders for bookings due in the next 60 minutes.

To use: call start_scheduler(app) from app.py after create_app()
"""
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import requests
import os

BAILEYS_URL = os.environ.get('BAILEYS_URL', 'https://luminous-kindness-production.up.railway.app')

def send_booking_reminders(app):
    with app.app_context():
        try:
            from models import db, Booking
            from dateutil import parser as dateparser

            now       = datetime.utcnow()
            one_hour  = now + timedelta(hours=1)

            pending = Booking.query.filter_by(reminder_sent=False, status='confirmed').all()

            for booking in pending:
                try:
                    appt_dt = dateparser.parse(booking.date_time, fuzzy=True)
                    if not appt_dt:
                        continue
                    # Send reminder if booking is within next 60 minutes
                    if now <= appt_dt <= one_hour:
                        res = requests.post(
                            f'{BAILEYS_URL}/send-reminder/{booking.user_id}',
                            json={
                                'customer_phone': booking.customer_phone,
                                'customer_name':  booking.customer_name,
                                'service':        booking.service,
                                'date_time':      booking.date_time,
                            },
                            timeout=8
                        )
                        if res.json().get('success'):
                            booking.reminder_sent = True
                            db.session.commit()
                            print(f'[Reminder] Sent to {booking.customer_name} for {booking.service}')
                except Exception as e:
                    print(f'Reminder error for booking {booking.id}: {e}')

        except Exception as e:
            print(f'Scheduler run error: {e}')


def start_scheduler(app):
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=lambda: send_booking_reminders(app),
        trigger='interval',
        minutes=15,
        id='reminder_job'
    )
    scheduler.start()
    print('[Scheduler] Reminder scheduler started (every 15 min)')
    return scheduler
