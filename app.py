from flask import Flask
from flask_login import LoginManager
from models import db
from dotenv import load_dotenv
import os

load_dotenv()

login_manager = LoginManager()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-this')
    database_url = os.environ.get('DATABASE_URL', 'sqlite:///saas.db')
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'

    with app.app_context():
        db.create_all()

        import sqlite3
        basedir = os.path.abspath(os.path.dirname(__file__))
        db_path = os.path.join(basedir, 'instance', 'saas.db')
        if not os.path.exists(db_path):
            db_path = os.path.join(basedir, 'saas.db')
        print(f"[Migration] DB path: {db_path}, exists: {os.path.exists(db_path)}")

        # Rename old columns if needed
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("ALTER TABLE payment RENAME COLUMN cf_order_id TO rz_order_id")
            conn.execute("ALTER TABLE payment RENAME COLUMN cf_payment_id TO rz_payment_id")
            conn.execute("ALTER TABLE payment RENAME COLUMN cf_payment_status TO rz_payment_status")
            conn.commit()
            conn.close()
        except:
            pass

        # Always run migrations — no flag check
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            migrations = [
                ('business_info', 'menu_image',    'VARCHAR(300)', None),
                ('business_info', 'contact_phone', 'VARCHAR(20)',  None),
                ('business_info', 'contact_email', 'VARCHAR(100)', None),
                ('business_info', 'thanks_image',  'VARCHAR(300)', None),
                ('business_info', 'welcome_image', 'VARCHAR(300)', None),
                ('business_info', 'upi_id',        'VARCHAR(100)', None),
                ('business_info', 'gst_rate',      'FLOAT',        '18.0'),
                ('business_info', 'gst_number',    'VARCHAR(50)',  None),
                ('service',       'sort_order',    'INTEGER',      '0'),
                ('service',       'duration',      'VARCHAR(50)',  None),
                ('service',       'category',      'VARCHAR(50)',  "'General'"),
                ('service',       'image_url',     'VARCHAR(300)', "''"),
                ('service',       'is_available',  'BOOLEAN',      '1'),
                ('bot',           'personality',   'TEXT',         None),
                ('bot',           'language',      'VARCHAR(10)',  "'auto'"),
                ('booking',       'reminder_sent', 'BOOLEAN',      '0'),
                ('booking',       'payment_status','VARCHAR(20)',  "'unpaid'"),
                ('booking',       'notes',         'TEXT',         None),
            ]

            for table, column, col_type, default in migrations:
                try:
                    cursor.execute(f"SELECT {column} FROM {table} LIMIT 1")
                except sqlite3.OperationalError:
                    default_clause = f" DEFAULT {default}" if default else ""
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}{default_clause}")
                    print(f"[Migration] Added column {table}.{column}")

            conn.commit()
            conn.close()
            print("[Migration] Done!")
        except Exception as e:
            print(f"[Migration Warning] {e}")

        from werkzeug.security import generate_password_hash
        from models import User
        admin_email = os.environ.get('ADMIN_EMAIL')
        admin_pass  = os.environ.get('ADMIN_PASSWORD')
        if admin_email and admin_pass and not User.query.filter_by(email=admin_email).first():
            db.session.add(User(
                email=admin_email, name='Admin',
                password=generate_password_hash(admin_pass),
                business_name='Botify Admin', whatsapp_number=''
            ))
            db.session.commit()

    @login_manager.user_loader
    def load_user(user_id):
        from models import User
        return User.query.get(int(user_id))

    from handlers.auth      import auth
    from handlers.dashboard import dashboard
    from handlers.admin     import admin as admin_bp
    from handlers.payment   import payment_bp

    app.register_blueprint(auth)
    app.register_blueprint(dashboard)
    app.register_blueprint(admin_bp)
    app.register_blueprint(payment_bp, url_prefix='/payment')

    return app

app = create_app()

if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
    try:
        from handlers.reminders import start_scheduler
        start_scheduler(app)
    except Exception as e:
        print(f'[Warning] Scheduler not started: {e}')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)