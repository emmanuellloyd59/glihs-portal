import os
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# --- SECURE CONFIGURATION ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'glihs-super-secret-key-2026')

login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- SAFE USER CLASS (INHERITING USERMIXIN) ---
class User(UserMixin):
    def __init__(self, id, username, password, role):
        self.id = str(id)  # Flask-Login expects string IDs
        self.username = username
        self.password = password
        self.role = role

    def get_id(self):
        return self.id

# Static user dictionary matching string keys to prevent value parsing crashes
USERS = {
    "1": User(1, "teacher1", generate_password_hash("password123"), "teacher"),
    "2": User(2, "student1", generate_password_hash("password123"), "student")
}

@login_manager.user_loader
def load_user(user_id):
    # Safely fetches user by string ID without triggering internal attribute checks
    return USERS.get(str(user_id))

# --- WEB ROUTES ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username_input = request.form.get('username')
        password_input = request.form.get('password')
        
        user = next((u for u in USERS.values() if u.username == username_input), None)
        
        if user and check_password_hash(user.password, password_input):
            login_user(user)
            return redirect(url_for('dashboard'))
            
        flash('Invalid credentials')
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', name=current_user.username, role=current_user.role)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(port=5000)

