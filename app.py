from flask import Flask
from flask_login import LoginManager
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'qi-dev-key-change-in-prod')

login_manager = LoginManager(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = '请登录后继续。'
login_manager.login_message_category = 'error'

@login_manager.user_loader
def load_user(user_id):
    from models import Business
    from db import get_db
    db = get_db()
    row = db.execute('SELECT * FROM businesses WHERE id=%s', (user_id,)).fetchone()
    db.close()
    return Business(row) if row else None

from blueprints.auth import auth_bp
from blueprints.dashboard import dashboard_bp
from blueprints.booking import booking_bp

app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(booking_bp)

from db import init_db
init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5002)
