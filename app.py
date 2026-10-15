
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'school-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///school.db'
db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Database Schema
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'student', 'teacher', 'admin'
    grade_or_subject = db.Column(db.String(100), nullable=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- ROUTES ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
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

# Database initialization helper with default seed data
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='teacher1').first():
        hashed_pw = generate_password_hash('password123', method='pbkdf2:sha256')
        teacher = User(username='teacher1', password=hashed_pw, role='teacher', grade_or_subject='Mathematics')
        student = User(username='student1', password=hashed_pw, role='student', grade_or_subject='10th Grade')
        db.session.add(teacher)
        db.session.add(student)
        db.session.commit()

if __name__ == '__main__':
    app.run(port=5000)
