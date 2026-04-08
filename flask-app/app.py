from flask import Flask
from flask_login import LoginManager
from models import db
import os

login_manager = LoginManager()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY']                  = 'whatsapp-saas-secret-2024'
    app.config['SQLALCHEMY_DATABASE_URI']     = 'sqlite:///saas.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    @app.context_processor
    def inject_config():
        return dict(config=app.config)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'

    with app.app_context():
        from models import User, Bot, BusinessInfo, Service, Booking, \
                           ConversationHistory, MessageCount, UserPlan, Payment
        db.create_all()

        from werkzeug.security import generate_password_hash
        admin_user = User.query.filter_by(email='mukilarasu@admin.com').first()
        if not admin_user:
            admin_user = User(
                email           = 'mukilarasu@admin.com',
                name            = 'Admin',
                password        = generate_password_hash('admin@Muki123'),
                business_name   = 'Botify Admin',
                whatsapp_number = ''
            )
            db.session.add(admin_user)
            db.session.commit()
            print("✅ Admin user created: mukilarasu@admin.com")

    @login_manager.user_loader
    def load_user(user_id):
        from models import User
        return User.query.get(int(user_id))

    from handlers.auth      import auth
    from handlers.dashboard import dashboard
    from handlers.admin     import admin as admin_bp

    app.register_blueprint(auth)
    app.register_blueprint(dashboard)
    app.register_blueprint(admin_bp)

    return app

if __name__ == '__main__':
    app = create_app()
    print("✅ Flask running!")
    print("📊 Admin panel: http://localhost:5000/admin/login")
    app.run(debug=True, port=5000)