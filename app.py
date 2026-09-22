# =========================================================
# IMPORTS
# =========================================================

from flask import Flask, make_response, render_template, request, redirect, session, flash,jsonify
from flask_mail import Mail, Message
from utlis.pdf_generator import generate_pdf
import sqlite3
import bcrypt
import random
import config

import traceback
import razorpay

razorpay_client = razorpay.Client(
    auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET)
)




# =========================================================
# FLASK APPLICATION SETUP
# =========================================================

app = Flask(__name__)

# Secret key is used for Flask sessions
app.secret_key = config.SECRET_KEY

@app.template_filter('format_count')
def format_count(value):
    try:
        val = int(value)
        if val >= 1000:
            return f"{val / 1000:.1f}k"
        return str(val)
    except (ValueError, TypeError):
        return str(value or "1.2k")


# =========================================================
# EMAIL CONFIGURATION
# =========================================================

app.config['MAIL_SERVER'] = config.MAIL_SERVER
app.config['MAIL_PORT'] = config.MAIL_PORT
app.config['MAIL_USE_TLS'] = config.MAIL_USE_TLS
app.config['MAIL_USERNAME'] = config.MAIL_USERNAME
app.config['MAIL_PASSWORD'] = config.MAIL_PASSWORD

# Initialize Flask-Mail
mail = Mail(app)
app.config['ADMIN_UPLOAD_FOLDER'] = config.ADMIN_UPLOAD_FOLDER


import socket

def send_mail_with_timeout(message, timeout=3.0):
    """
    Sends an email with a strict socket timeout so that cloud firewalls
    (like Render blocking outbound SMTP ports) fail fast without
    hanging for 30s and causing Gunicorn worker timeout 500 crashes.
    """
    orig_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        mail.send(message)
        return True, None
    except Exception as e:
        print(f"Mail delivery failed: {e}")
        return False, str(e)
    finally:
        socket.setdefaulttimeout(orig_timeout)


def send_otp_email(recipient_email, otp, recipient_type="Customer"):
    """
    Sends a styled OTP email for password reset.
    """
    subject = f"SmartCart {recipient_type} - Password Reset OTP"
    message = Message(
        subject=subject,
        sender=config.MAIL_USERNAME,
        recipients=[recipient_email]
    )
    message.body = (
        f"Hello,\n\n"
        f"Your OTP for resetting your SmartCart {recipient_type.lower()} password is: {otp}\n\n"
        f"This OTP is valid for 10 minutes. If you did not request a password reset, please ignore this email.\n\n"
        f"Best regards,\nSmartCart Team"
    )
    message.html = f"""
    <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 520px; margin: auto; padding: 28px; border: 1px solid #e2e8f0; border-radius: 12px; background: #ffffff;">
        <div style="text-align: center; margin-bottom: 22px;">
            <h2 style="color: #1e293b; margin: 0; font-size: 24px; font-weight: 700;">Smart<span style="color: #2874f0;">Cart</span></h2>
            <p style="color: #64748b; font-size: 14px; margin: 6px 0 0;">Password Reset Request</p>
        </div>
        <p style="color: #334155; font-size: 15px; line-height: 1.5;">Hello,</p>
        <p style="color: #334155; font-size: 15px; line-height: 1.5;">We received a request to reset the password for your SmartCart <strong>{recipient_type.lower()}</strong> account. Use the one-time verification code below to set a new password:</p>
        <div style="text-align: center; margin: 26px 0;">
            <span style="display: inline-block; font-size: 32px; font-weight: 700; letter-spacing: 6px; color: #2874f0; background: #eff6ff; padding: 12px 28px; border-radius: 8px; border: 1px dashed #93c5fd;">{otp}</span>
        </div>
        <p style="color: #64748b; font-size: 13px; line-height: 1.5;">If you did not request this reset, you can safely ignore this email. Your password will remain unchanged.</p>
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 22px 0;" />
        <p style="color: #94a3b8; font-size: 12px; text-align: center; margin: 0;">&copy; SmartCart. All rights reserved.</p>
    </div>
    """
    sent, _ = send_mail_with_timeout(message, timeout=3.0)
    return sent



# =========================================================
# DATABASE CONNECTION (SQLite Engine)
# =========================================================

class RowDict(dict):
    """Hybrid dictionary that supports both column-name access and index-based access."""
    def __init__(self, row):
        super().__init__(dict(row))
        self._row = row
    def __getitem__(self, key):
        if isinstance(key, int):
            return self._row[key]
        return super().__getitem__(key)


class SQLiteCursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, query, params=None):
        # Translate MySQL parameter placeholders (%s) to SQLite (?)
        query = query.replace('%s', '?')
        if params is not None:
            return self.cursor.execute(query, params)
        return self.cursor.execute(query)

    def executemany(self, query, seq_of_params):
        query = query.replace('%s', '?')
        return self.cursor.executemany(query, seq_of_params)

    def fetchone(self):
        row = self.cursor.fetchone()
        return RowDict(row) if row is not None else None

    def fetchall(self):
        rows = self.cursor.fetchall()
        return [RowDict(row) for row in rows]

    def __iter__(self):
        for row in self.cursor:
            yield RowDict(row)

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def close(self):
        try:
            self.cursor.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class SQLiteConnectionWrapper:
    def __init__(self, conn):
        self.conn = conn

    def cursor(self, dictionary=True):
        return SQLiteCursorWrapper(self.conn.cursor())

    def commit(self):
        return self.conn.commit()

    def rollback(self):
        return self.conn.rollback()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()


def get_db_connection():
    """
    Creates and returns a connection to the SQLite database (smartcart.db).
    """
    db_path = getattr(config, 'DB_PATH', 'smartcart.db')
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return SQLiteConnectionWrapper(conn)


def init_db():
    """Ensure database tables exist on server startup."""
    try:
        db_path = getattr(config, 'DB_PATH', 'smartcart.db')
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON;")
        schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
        if os.path.exists(schema_path):
            with open(schema_path, 'r', encoding='utf-8') as f:
                conn.executescript(f.read())
        conn.close()
    except Exception as e:
        print(f"Database initialization warning: {e}")

init_db()


# =========================================================
# ROOT ROUTE -> DEFAULT USER LOGIN
# =========================================================

@app.route('/')
def root():
    if 'user_id' in session:
        return redirect('/user/user-dashboard')
    if 'admin_id' in session:
        return redirect('/admin-dashboard')
    return redirect('/user-login')


# =========================================================
# ADMIN SIGNUP
# =========================================================

@app.route('/admin-signup', methods=['GET', 'POST'])
def admin_signup():

    if request.method == 'GET':
        return render_template('admin/admin_signup.html')

    name = request.form['name']
    email = request.form['email']

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT admin_id FROM admin WHERE email = %s",
        (email,)
    )

    existing_admin = cursor.fetchone()

    cursor.close()
    conn.close()

    if existing_admin:
        flash(
            "This email is already registered. Please login instead.",
            "danger"
        )

        return redirect('/admin-signup')

    session['signup_name'] = name
    session['signup_email'] = email

    otp = random.randint(100000, 999999)
    session['otp'] = otp

    message = Message(
        subject="SmartCart Admin OTP",
        sender=config.MAIL_USERNAME,
        recipients=[email]
    )

    message.body = (
        f"Your OTP for SmartCart Admin Registration is: {otp}"
    )

    sent, err = send_mail_with_timeout(message, timeout=3.0)
    if sent:
        flash("OTP sent to your email!", "success")
    else:
        print(f"Error sending admin OTP: {err}")
        flash(f"Notice: Email could not be sent (cloud host SMTP restriction). For testing, your OTP is: {otp}", "warning")

    return redirect('/verify-otp')


# =========================================================
# OTP VERIFICATION PAGE
# =========================================================

@app.route('/verify-otp', methods=['GET'])
def verify_otp_get():

    return render_template('admin/verify_otp.html')


# =========================================================
# VERIFY OTP AND CREATE ADMIN
# =========================================================

@app.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    if request.method == 'GET':
        return render_template('admin/verify_otp.html')

    user_otp = request.form['otp']
    password = request.form['password']

    if str(session.get('otp')) != str(user_otp):
        flash("Invalid OTP. Try again!", "danger")
        return redirect('/verify-otp')

    # continue creating admin...


    # -----------------------------------------------------
    # Hash password using bcrypt
    # -----------------------------------------------------

    hashed_password = bcrypt.hashpw(
        password.encode('utf-8'),
        bcrypt.gensalt()
    )


    # -----------------------------------------------------
    # Insert admin details into database
    # -----------------------------------------------------

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO admin (name, email, password)
        VALUES (%s, %s, %s)
        """,
        (
            session['signup_name'],
            session['signup_email'],
            hashed_password
        )
    )

    conn.commit()

    cursor.close()
    conn.close()


    # -----------------------------------------------------
    # Clear temporary signup information from session
    # -----------------------------------------------------

    session.pop('otp', None)
    session.pop('signup_name', None)
    session.pop('signup_email', None)


    # -----------------------------------------------------
    # Registration successful
    # -----------------------------------------------------

    flash("Admin Registered Successfully!", "success")

    return redirect('/admin-login')


# =================================================================
# ROUTE 4: ADMIN LOGIN PAGE (GET + POST)
# =================================================================
@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():

    # Show login page
    if request.method == 'GET':
        return render_template("admin/admin_login.html")

    # POST → Validate login
    email = request.form['email']
    password = request.form['password']

    # Step 1: Check if admin email exists
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM admin WHERE email=%s", (email,))
    admin = cursor.fetchone()

    cursor.close()
    conn.close()

    if admin is None:
        flash("Email not found! Please register first.", "danger")
        return redirect('/admin-login')

    # Step 2: Compare entered password with hashed password
    stored_hashed_password = admin['password'].encode('utf-8')

    if not bcrypt.checkpw(password.encode('utf-8'), stored_hashed_password):
        flash("Incorrect password! Try again.", "danger")
        return redirect('/admin-login')

    # Step 5: If login success → Create admin session
    session['admin_id'] = admin['admin_id']
    session['admin_name'] = admin['name']
    session['admin_email'] = admin['email']

    flash("Login Successful!", "success")
    return redirect('/admin-dashboard')


# =================================================================
# ROUTE: ADMIN FORGOT PASSWORD
# =================================================================
@app.route('/admin/forgot-password', methods=['GET', 'POST'])
def admin_forgot_password():
    if request.method == 'GET':
        return render_template('admin/forgot_password.html')

    email = request.form.get('email', '').strip()
    if not email:
        flash("Please enter your registered admin email.", "danger")
        return redirect('/admin/forgot-password')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT admin_id, name, email FROM admin WHERE email=%s", (email,))
    admin = cursor.fetchone()
    cursor.close()
    conn.close()

    if not admin:
        flash("No admin account found with this email address.", "danger")
        return redirect('/admin/forgot-password')

    otp = random.randint(100000, 999999)
    session['admin_reset_otp'] = str(otp)
    session['admin_reset_email'] = email

    email_sent = send_otp_email(email, otp, "Admin")
    if not email_sent:
        flash(f"Notice: Email delivery blocked by host. Your reset OTP is: {otp}", "warning")
    else:
        flash(f"A password reset OTP has been sent to {email}.", "success")
    return redirect('/admin/reset-password')


# =================================================================
# ROUTE: ADMIN RESEND RESET OTP
# =================================================================
@app.route('/admin/forgot-password/resend', methods=['GET', 'POST'])
def admin_resend_otp():
    email = session.get('admin_reset_email')
    if not email:
        flash("Reset session expired. Please enter your email again.", "warning")
        return redirect('/admin/forgot-password')

    otp = random.randint(100000, 999999)
    session['admin_reset_otp'] = str(otp)

    email_sent = send_otp_email(email, otp, "Admin")
    if not email_sent:
        flash(f"Notice: Email delivery blocked by host. Your new OTP is: {otp}", "warning")
    else:
        flash(f"A new OTP code has been sent to {email}.", "success")
    return redirect('/admin/reset-password')


# =================================================================
# ROUTE: ADMIN RESET PASSWORD
# =================================================================
@app.route('/admin/reset-password', methods=['GET', 'POST'])
def admin_reset_password():
    email = session.get('admin_reset_email')
    if not email:
        flash("Please enter your email to request a reset code first.", "warning")
        return redirect('/admin/forgot-password')

    if request.method == 'GET':
        return render_template('admin/reset_password.html', email=email)

    otp = request.form.get('otp', '').strip()
    new_password = request.form.get('password', '')
    confirm_password = request.form.get('confirm_password', '')

    stored_otp = str(session.get('admin_reset_otp', ''))

    if not otp or otp != stored_otp:
        flash("Invalid or expired OTP code. Please enter the correct code.", "danger")
        return redirect('/admin/reset-password')

    if len(new_password) < 6:
        flash("Password must be at least 6 characters long.", "danger")
        return redirect('/admin/reset-password')

    if new_password != confirm_password:
        flash("Passwords do not match. Please re-enter.", "danger")
        return redirect('/admin/reset-password')

    hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE admin SET password=%s WHERE email=%s", (hashed_password, email))
    conn.commit()
    cursor.close()
    conn.close()

    session.pop('admin_reset_otp', None)
    session.pop('admin_reset_email', None)

    flash("Admin password has been reset successfully! Please sign in with your new password.", "success")
    return redirect('/admin-login')


# =================================================================
# ROUTE 5: ADMIN DASHBOARD (PROTECTED ROUTE)
# =================================================================
@app.route('/admin-dashboard')
def admin_dashboard():

    # Protect dashboard → Only logged-in admin can access
    if 'admin_id' not in session:
        flash("Please login to access dashboard!", "danger")
        return redirect('/admin-login')

    # Send admin name to dashboard UI
    return render_template("admin/dashboard.html", admin_name=session['admin_name'])



# =================================================================
# ROUTE 6: ADMIN LOGOUT
# =================================================================
@app.route('/admin-logout')
def admin_logout():

    # Clear admin session
    session.pop('admin_id', None)
    session.pop('admin_name', None)
    session.pop('admin_email', None)

    flash("Logged out successfully.", "success")
    return redirect('/admin-login')

import os
from werkzeug.utils import secure_filename

# ------------------- IMAGE UPLOAD PATH -------------------
UPLOAD_FOLDER = 'static/uploads/product_images'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


# =================================================================
# ROUTE 7: SHOW ADD PRODUCT PAGE (Protected Route)
# =================================================================
@app.route('/admin/add-item', methods=['GET'])
def add_item_page():

    # Only logged-in admin can access
    if 'admin_id' not in session:
        flash("Please login first!", "danger")
        return redirect('/admin-login')

    return render_template("admin/add_item.html")



# =================================================================
# ROUTE 8: ADD PRODUCT INTO DATABASE
# =================================================================
@app.route('/admin/add-item', methods=['POST'])
def add_item():

    # Check admin session
    if 'admin_id' not in session:
        flash("Please login first!", "danger")
        return redirect('/admin-login')

    # 1️⃣ Get form data
    name = request.form['name']
    description = request.form['description']
    category = request.form['category']
    price = request.form['price']
    image_file = request.files['image']

    try:
        price_val = float(price)
        if price_val < 1.0:
            flash("Price must be at least ₹1.00!", "danger")
            return redirect('/admin/add-item')
    except (ValueError, TypeError):
        flash("Invalid price entered!", "danger")
        return redirect('/admin/add-item')

    # 2️⃣ Validate image upload
    if image_file.filename == "":
        flash("Please upload a product image!", "danger")
        return redirect('/admin/add-item')

    # 3️⃣ Secure the file name
    filename = secure_filename(image_file.filename)

    # 4️⃣ Create full path
    image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    # 5️⃣ Save image into folder
    image_file.save(image_path)

    # 6️⃣ Insert product into database
    auto_rating = round(random.uniform(4.0, 4.9), 1)
    auto_reviews = random.randint(350, 4200)

    conn = get_db_connection()
    cursor = conn.cursor()

    admin_id = session['admin_id']

    cursor.execute(
        "INSERT INTO products (admin_id, name, description, category, price, image, rating, reviews_count) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (admin_id, name, description, category, price, filename, auto_rating, auto_reviews)
    )

    conn.commit()
    cursor.close()
    conn.close()

    flash("Product added successfully!", "success")
    return redirect('/admin/add-item')


# =================================================================
# ROUTE 9: DISPLAY ALL PRODUCTS (Admin - Only Own Products)
# =================================================================
@app.route('/admin/item-list')
def item_list():

    if 'admin_id' not in session:
        flash("Please login!", "danger")
        return redirect('/admin-login')

    admin_id = session['admin_id']
    search = request.args.get('search', '')
    category_filter = request.args.get('category', '')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # 1️⃣ Fetch category list for dropdown (only for this admin's products)
    cursor.execute("SELECT DISTINCT category FROM products WHERE admin_id = %s", (admin_id,))
    categories = cursor.fetchall()

    # 2️⃣ Build dynamic query based on filters (scoped to this admin)
    query = "SELECT * FROM products WHERE admin_id = %s"
    params = [admin_id]

    if search:
        query += " AND name LIKE %s"
        params.append("%" + search + "%")

    if category_filter:
        query += " AND category = %s"
        params.append(category_filter)

    cursor.execute(query, params)
    products = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "admin/item_list.html",
        products=products,
        categories=categories
    )




#=================================================================
# ROUTE 10: VIEW SINGLE PRODUCT DETAILS (Only Own Product)
# =================================================================
@app.route('/admin/view-item/<int:item_id>')
def view_item(item_id):

    # Check admin session
    if 'admin_id' not in session:
        flash("Please login first!", "danger")
        return redirect('/admin-login')

    admin_id = session['admin_id']
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM products WHERE product_id = %s AND admin_id = %s", (item_id, admin_id))
    product = cursor.fetchone()

    cursor.close()
    conn.close()

    if not product:
        flash("Product not found or access denied!", "danger")
        return redirect('/admin/item-list')

    return render_template("admin/view_item.html", product=product)
@app.route('/admin/update-item/<int:item_id>', methods=['GET'])
def update_item_page(item_id):

    # Check login
    if 'admin_id' not in session:
        flash("Please login!", "danger")
        return redirect('/admin-login')

    admin_id = session['admin_id']

    # Fetch product data (only if owned by this admin)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM products WHERE product_id = %s AND admin_id = %s", (item_id, admin_id))
    product = cursor.fetchone()

    cursor.close()
    conn.close()

    if not product:
        flash("Product not found or access denied!", "danger")
        return redirect('/admin/item-list')

    return render_template("admin/update_item.html", product=product)
# =================================================================
# ROUTE-12: UPDATE PRODUCT + OPTIONAL IMAGE REPLACE
# =================================================================
@app.route('/admin/update-item/<int:item_id>', methods=['POST'])
def update_item(item_id):

    if 'admin_id' not in session:
        flash("Please login!", "danger")
        return redirect('/admin-login')

    admin_id = session['admin_id']

    # 1️⃣ Get updated form data
    name = request.form['name']
    description = request.form['description']
    category = request.form['category']
    price = request.form['price']

    try:
        price_val = float(price)
        if price_val < 1.0:
            flash("Price must be at least ₹1.00!", "danger")
            return redirect(f'/admin/update-item/{item_id}')
    except (ValueError, TypeError):
        flash("Invalid price entered!", "danger")
        return redirect(f'/admin/update-item/{item_id}')

    new_image = request.files['image']

    # 2️⃣ Fetch old product data (verify ownership)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM products WHERE product_id = %s AND admin_id = %s", (item_id, admin_id))
    product = cursor.fetchone()

    if not product:
        flash("Product not found or access denied!", "danger")
        return redirect('/admin/item-list')

    old_image_name = product['image']

    # 3️⃣ If admin uploaded a new image → replace it
    if new_image and new_image.filename != "":
        
        # Secure filename
        from werkzeug.utils import secure_filename
        new_filename = secure_filename(new_image.filename)

        # Save new image
        new_image_path = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
        new_image.save(new_image_path)

        # Delete old image file
        old_image_path = os.path.join(app.config['UPLOAD_FOLDER'], old_image_name)
        if os.path.exists(old_image_path):
            os.remove(old_image_path)

        final_image_name = new_filename

    else:
        # No new image uploaded → keep old one
        final_image_name = old_image_name

    # 4️⃣ Update product in the database (scoped to admin_id)
    cursor.execute("""
        UPDATE products
        SET name=%s, description=%s, category=%s, price=%s, image=%s
        WHERE product_id=%s AND admin_id=%s
    """, (name, description, category, price, final_image_name, item_id, admin_id))

    conn.commit()
    cursor.close()
    conn.close()

    flash("Product updated successfully!", "success")
    return redirect('/admin/item-list')

@app.route('/admin/delete-item/<int:item_id>')
def delete_item(item_id):

    if 'admin_id' not in session:
        flash("Please login first!", "danger")
        return redirect('/admin-login')

    admin_id = session['admin_id']

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # 1️⃣ Fetch product to get image name (verify ownership)
    cursor.execute("SELECT image FROM products WHERE product_id=%s AND admin_id=%s", (item_id, admin_id))
    product = cursor.fetchone()

    if not product:
        flash("Product not found or access denied!", "danger")
        return redirect('/admin/item-list')

    image_name = product['image']

    # Delete image from folder
    image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_name)
    if os.path.exists(image_path):
        os.remove(image_path)

    # 2️⃣ Delete product from DB (scoped to admin_id)
    cursor.execute("DELETE FROM products WHERE product_id=%s AND admin_id=%s", (item_id, admin_id))
    conn.commit()

    cursor.close()
    conn.close()

    flash("Product deleted successfully!", "success")
    return redirect('/admin/item-list')
# =================================================================
# ROUTE 1: SHOW ADMIN PROFILE DATA
# =================================================================
@app.route('/admin/profile', methods=['GET'])
def admin_profile():

    if 'admin_id' not in session:
        flash("Please login!", "danger")
        return redirect('/admin-login')

    admin_id = session['admin_id']

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM admin WHERE admin_id = %s", (admin_id,))
    admin = cursor.fetchone()

    cursor.close()
    conn.close()

    return render_template("admin/admin_profile.html", admin=admin)
# =================================================================
# ROUTE 2: UPDATE ADMIN PROFILE (NAME, EMAIL, PASSWORD, IMAGE)
# =================================================================
@app.route('/admin/profile', methods=['POST'])
def admin_profile_update():

    if 'admin_id' not in session:
        flash("Please login!", "danger")
        return redirect('/admin-login')

    admin_id = session['admin_id']

    # 1️⃣ Get form data
    name = request.form['name']
    email = request.form['email']
    new_password = request.form['password']
    new_image = request.files['profile_image']

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # 2️⃣ Fetch old admin data
    cursor.execute("SELECT * FROM admin WHERE admin_id = %s", (admin_id,))
    admin = cursor.fetchone()

    old_image_name = admin['profile_image']

    # 3️⃣ Update password only if entered
    if new_password:
        hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
    else:
        hashed_password = admin['password']  # keep old password

    # 4️⃣ Process new profile image if uploaded
    if new_image and new_image.filename != "":
        
        from werkzeug.utils import secure_filename
        new_filename = secure_filename(new_image.filename)

        # Save new image
        image_path = os.path.join(app.config['ADMIN_UPLOAD_FOLDER'], new_filename)
        new_image.save(image_path)

        # Delete old image
        if old_image_name:
            old_image_path = os.path.join(app.config['ADMIN_UPLOAD_FOLDER'], old_image_name)
            if os.path.exists(old_image_path):
                os.remove(old_image_path)

        final_image_name = new_filename
    else:
        final_image_name = old_image_name

    # 5️⃣ Update database
    cursor.execute("""
        UPDATE admin
        SET name=%s, email=%s, password=%s, profile_image=%s
        WHERE admin_id=%s
    """, (name, email, hashed_password, final_image_name, admin_id))

    conn.commit()
    cursor.close()
    conn.close()

    # Update session name for UI consistency
    session['admin_name'] = name  
    session['admin_email'] = email

    flash("Profile updated successfully!", "success")
    return redirect('/admin/profile')





# =========================================================
# USER SIGNUP
# =========================================================

@app.route('/user-signup', methods=['GET', 'POST'])
@app.route('/user/user-signup', methods=['GET', 'POST'])
def user_signup():

    # -----------------------------------------------------
    # Display signup form
    # -----------------------------------------------------

    if request.method == 'GET':
        return render_template('user/user_signup.html')


    # -----------------------------------------------------
    # Get signup details from form
    # -----------------------------------------------------

    name = request.form['name']
    email = request.form['email']


    # -----------------------------------------------------
    # Check whether email already exists
    # -----------------------------------------------------

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT user_id FROM users WHERE email = %s",
        (email,)
    )

    existing_user = cursor.fetchone()

    cursor.close()
    conn.close()


    # -----------------------------------------------------
    # Stop registration if email already exists
    # -----------------------------------------------------

    if existing_user:
        flash(
            "This email is already registered. Please login instead.",
            "danger"
        )

        return redirect('/user-signup')


    # -----------------------------------------------------
    # Store signup details temporarily in session
    # -----------------------------------------------------

    session['user_signup_name'] = name
    session['user_signup_email'] = email


    # -----------------------------------------------------
    # Generate and store OTP
    # -----------------------------------------------------

    otp = random.randint(100000, 999999)

    session['user_otp'] = otp


    # -----------------------------------------------------
    # Send OTP to user's email
    # -----------------------------------------------------

    message = Message(
        subject="SmartCart User OTP",
        sender=config.MAIL_USERNAME,
        recipients=[email]
    )

    message.body = (
        f"Your OTP for SmartCart User Registration is: {otp}"
    )

    sent, err = send_mail_with_timeout(message, timeout=3.0)
    if sent:
        flash("OTP sent to your email!", "success")
    else:
        print(f"Error sending user OTP: {err}")
        flash(f"Notice: Email could not be sent (cloud host SMTP restriction). For testing, your OTP is: {otp}", "warning")

    return redirect('/user-verify-otp')


# =========================================================
# USER OTP VERIFICATION PAGE
# =========================================================

@app.route('/user-verify-otp', methods=['GET'])
def user_verify_otp_get():

    return render_template('user/user_verify_otp.html')


# =========================================================
# VERIFY USER OTP AND CREATE USER
# =========================================================

@app.route('/user-verify-otp', methods=['POST'])
def user_verify_otp_post():

    # -----------------------------------------------------
    # Get OTP and password from form
    # -----------------------------------------------------

    user_otp = request.form['otp']
    password = request.form['password']


    # -----------------------------------------------------
    # Verify OTP
    # -----------------------------------------------------

    if str(session.get('user_otp')) != str(user_otp):

        flash("Invalid OTP. Try again!", "danger")

        return redirect('/user-verify-otp')


    # -----------------------------------------------------
    # Hash password using bcrypt
    # -----------------------------------------------------

    hashed_password = bcrypt.hashpw(
        password.encode('utf-8'),
        bcrypt.gensalt()
    )


    # -----------------------------------------------------
    # Insert user details into database
    # -----------------------------------------------------

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO users (name, email, password)
        VALUES (%s, %s, %s)
        """,
        (
            session['user_signup_name'],
            session['user_signup_email'],
            hashed_password
        )
    )

    conn.commit()

    cursor.close()
    conn.close()


    # -----------------------------------------------------
    # Clear temporary signup information from session
    # -----------------------------------------------------

    session.pop('user_otp', None)
    session.pop('user_signup_name', None)
    session.pop('user_signup_email', None)


    # -----------------------------------------------------
    # Registration successful
    # -----------------------------------------------------

    flash("User Registered Successfully!", "success")

    return redirect('/user-signup')


# =========================================================
# ROUTE 2: USER LOGIN
# =========================================================

@app.route('/user/user-login', methods=['GET', 'POST'])
@app.route('/user-login', methods=['GET', 'POST'])
def user_login():

    if request.method == 'GET':
        if 'user_id' in session:
            return redirect('/user/user-dashboard')
        return render_template("user/user_login.html")


    # -----------------------------------------------------
    # Get login details
    # -----------------------------------------------------

    email = request.form['email']
    password = request.form['password']


    # -----------------------------------------------------
    # Get user from database
    # -----------------------------------------------------

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT * FROM users WHERE email=%s",
        (email,)
    )

    user = cursor.fetchone()

    cursor.close()
    conn.close()


    # -----------------------------------------------------
    # Check user exists
    # -----------------------------------------------------

    if not user:

        flash(
            "Email not found! Please register.",
            "danger"
        )

        return redirect('/user/user-login')


    # -----------------------------------------------------
    # Verify password
    # -----------------------------------------------------

    stored_password = user['password']

    if isinstance(stored_password, str):
        stored_password = stored_password.encode('utf-8')

    if not bcrypt.checkpw(
        password.encode('utf-8'),
        stored_password
    ):

        flash(
            "Incorrect password!",
            "danger"
        )

        return redirect('/user/user-login')


    # -----------------------------------------------------
    # Create user session
    # -----------------------------------------------------

    session['user_id'] = user['user_id']
    session['user_name'] = user['name']
    session['user_email'] = user['email']


    # -----------------------------------------------------
    # Login successful
    # -----------------------------------------------------

    flash(
        "Login successful!",
        "success"
    )

    # Redirect to destination if next param exists
    next_url = request.form.get('next') or request.args.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)

    return redirect('/user/user-dashboard')


# =========================================================
# ROUTE: USER FORGOT PASSWORD
# =========================================================
@app.route('/user/forgot-password', methods=['GET', 'POST'])
def user_forgot_password():
    if request.method == 'GET':
        return render_template('user/forgot_password.html')

    email = request.form.get('email', '').strip()
    if not email:
        flash("Please enter your registered email address.", "danger")
        return redirect('/user/forgot-password')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT user_id, name, email FROM users WHERE email=%s", (email,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user:
        flash("No account found with this email address.", "danger")
        return redirect('/user/forgot-password')

    otp = random.randint(100000, 999999)
    session['user_reset_otp'] = str(otp)
    session['user_reset_email'] = email

    email_sent = send_otp_email(email, otp, "Customer")
    if not email_sent:
        flash(f"Notice: Email delivery blocked by host. Your reset OTP is: {otp}", "warning")
    else:
        flash(f"A password reset OTP has been sent to {email}.", "success")
    return redirect('/user/reset-password')


# =========================================================
# ROUTE: USER RESEND RESET OTP
# =========================================================
@app.route('/user/forgot-password/resend', methods=['GET', 'POST'])
def user_resend_otp():
    email = session.get('user_reset_email')
    if not email:
        flash("Reset session expired. Please enter your email again.", "warning")
        return redirect('/user/forgot-password')

    otp = random.randint(100000, 999999)
    session['user_reset_otp'] = str(otp)

    email_sent = send_otp_email(email, otp, "Customer")
    if not email_sent:
        flash(f"Notice: Email delivery blocked by host. Your new OTP is: {otp}", "warning")
    else:
        flash(f"A new OTP code has been sent to {email}.", "success")
    return redirect('/user/reset-password')


# =========================================================
# ROUTE: USER RESET PASSWORD
# =========================================================
@app.route('/user/reset-password', methods=['GET', 'POST'])
def user_reset_password():
    email = session.get('user_reset_email')
    if not email:
        flash("Please enter your email to request a reset code first.", "warning")
        return redirect('/user/forgot-password')

    if request.method == 'GET':
        return render_template('user/reset_password.html', email=email)

    otp = request.form.get('otp', '').strip()
    new_password = request.form.get('password', '')
    confirm_password = request.form.get('confirm_password', '')

    stored_otp = str(session.get('user_reset_otp', ''))

    if not otp or otp != stored_otp:
        flash("Invalid or expired OTP code. Please enter the correct code.", "danger")
        return redirect('/user/reset-password')

    if len(new_password) < 6:
        flash("Password must be at least 6 characters long.", "danger")
        return redirect('/user/reset-password')

    if new_password != confirm_password:
        flash("Passwords do not match. Please re-enter.", "danger")
        return redirect('/user/reset-password')

    hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET password=%s WHERE email=%s", (hashed_password, email))
    conn.commit()
    cursor.close()
    conn.close()

    session.pop('user_reset_otp', None)
    session.pop('user_reset_email', None)

    flash("Password reset successfully! Please sign in with your new password.", "success")
    return redirect('/user/user-login')


# =========================================================
# ROUTE 3: USER DASHBOARD
# =========================================================

@app.route('/user/user-dashboard')
def user_dashboard():

    if 'user_id' not in session:

        flash(
            "Please login first!",
            "danger"
        )

        return redirect('/user/user-login')

    # Fetch latest products to display on home dashboard
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM products ORDER BY product_id DESC LIMIT 12")
    products = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template(
        "user/user_home.html",
        user_name=session['user_name'],
        products=products
    )


# =========================================================
# ROUTE 4: USER LOGOUT
# =========================================================

@app.route('/user/user-logout')
def user_logout():

    session.pop('user_id', None)
    session.pop('user_name', None)
    session.pop('user_email', None)


    flash(
        "Logged out successfully!",
        "success"
    )

    return redirect('/user/user-login')


@app.route('/user/products')
def user_products():

    # Optional: restrict only logged-in users
# Allow both guests and logged-in users to view products without redirect

    search = request.args.get('search', '')
    category_filter = request.args.get('category', '')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch categories for filter dropdown
    cursor.execute("SELECT DISTINCT category FROM products")
    categories = cursor.fetchall()

    # Build dynamic SQL
    query = "SELECT * FROM products WHERE 1=1"
    params = []

    if search:
        query += " AND name LIKE %s"
        params.append("%" + search + "%")

    if category_filter:
        query += " AND category = %s"
        params.append(category_filter)

    cursor.execute(query, params)
    products = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "user/user_products.html",
        products=products,
        categories=categories
    )

#⭐ ROUTE 2: Single Product Details Page
# =================================================================
# ROUTE: USER PRODUCT DETAILS PAGE
# =================================================================
@app.route('/user/product/<int:product_id>')
def user_product_details(product_id):

    if 'user_id' not in session:
        flash("Please login to view product details!", "danger")
        return redirect('/user/user-login')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM products WHERE product_id = %s", (product_id,))
    product = cursor.fetchone()

    cursor.close()
    conn.close()

    if not product:
        flash("Product not found!", "danger")
        return redirect('/user/products')

    return render_template("user/product_details.html", product=product)




# =========================================================
# ADD TO CART
# =========================================================

@app.route('/user/add-to-cart/<int:pid>', methods=['POST'])
def add_to_cart(pid):

    # Check whether cart exists
    if 'cart' not in session:
        session['cart'] = {}

    # Get database connection
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Get product
    cursor.execute(
        "SELECT * FROM products WHERE product_id = %s",
        (pid,)
    )

    product = cursor.fetchone()

    cursor.close()
    conn.close()

    # Product not found
    if not product:
        return jsonify({
            'success': False,
            'message': 'Product not found!'
        })

    # Get cart
    cart = session['cart']

    # Session dictionary keys are strings
    pid = str(pid)

    # Product already exists
    if pid in cart:

        cart[pid]['quantity'] += 1

    # Product is new
    else:

        cart[pid] = {
            'name': product['name'],
            'price': float(product['price']),
            'image': product['image'],
            'quantity': 1
        }

    # Save cart back into session
    session['cart'] = cart

    # Make Flask detect session change
    session.modified = True

    # Calculate cart count
    cart_count = sum(
        item['quantity']
        for item in cart.values()
    )

    return jsonify({
        'success': True,
        'message': 'Product added to cart!',
        'cart_count': cart_count
    })


# =========================================================
# USER CART PAGE
# =========================================================

@app.route('/user/cart')
def user_cart():

    # Check login
    if 'user_id' not in session:
        flash("Please login to view your cart!", "danger")
        return redirect('/user-login?next=/user/cart')

    # Get cart from session
    cart = session.get('cart', {})

    # Calculate grand total
    grand_total = 0

    for item in cart.values():

        item['item_total'] = (
            item['price'] * item['quantity']
        )

        grand_total += item['item_total']

    # Calculate total quantity
    cart_count = sum(
        item['quantity']
        for item in cart.values()
    )

    return render_template(
        'user/user_cart.html',
        cart=cart,
        grand_total=grand_total,
        cart_count=cart_count
    )


# =========================================================
# INCREASE CART QUANTITY
# =========================================================

@app.route('/user/cart/increase/<int:pid>', methods=['POST'])
def increase_cart(pid):

    cart = session.get('cart', {})

    pid = str(pid)

    if pid in cart:

        cart[pid]['quantity'] += 1

        session['cart'] = cart
        session.modified = True

    return redirect('/user/cart')


# =========================================================
# DECREASE CART QUANTITY
# =========================================================

@app.route('/user/cart/decrease/<int:pid>', methods=['POST'])
def decrease_cart(pid):

    cart = session.get('cart', {})

    pid = str(pid)

    if pid in cart:

        cart[pid]['quantity'] -= 1

        if cart[pid]['quantity'] <= 0:
            cart.pop(pid)

        session['cart'] = cart
        session.modified = True

    return redirect('/user/cart')


# =========================================================
# REMOVE FROM CART
# =========================================================

@app.route('/user/cart/remove/<int:pid>', methods=['POST'])
def remove_from_cart(pid):

    cart = session.get('cart', {})

    pid = str(pid)

    if pid in cart:

        cart.pop(pid)

        session['cart'] = cart
        session.modified = True

    return redirect('/user/cart')



@app.route('/user/selected-checkout', methods=['POST'])
def selected_checkout():

    # Check login
    if 'user_id' not in session:
        return redirect('/user/login')

    # Get selected product IDs
    selected_ids = request.form.getlist('product_ids')

    # Nothing selected
    if not selected_ids:
        flash('Please select at least one product.', 'error')
        return redirect('/user/cart')

    # Get current cart
    cart = session.get('cart', {})

    # Create checkout items
    checkout_items = {}

    for pid in selected_ids:

        pid = str(pid)

        if pid in cart:

            checkout_items[pid] = cart[pid]

    # Check whether valid products were selected
    if not checkout_items:
        flash('Selected products are not available in cart.', 'error')
        return redirect('/user/cart')

    # Clear any previous buy-now product
    session.pop('buy_now_product', None)

    # Store only selected products for checkout
    session['checkout_items'] = checkout_items

    # Tell payment/order system this is cart checkout
    session['checkout_type'] = 'cart'
    session.modified = True

    # Go to address page
    return redirect('/user/address?source=cart')


# =================================================================
# ROUTE: CREATE RAZORPAY ORDER
# =================================================================
# =========================================================
# PAYMENT
# =========================================================

@app.route('/user/pay')
def user_pay():

    if 'user_id' not in session:
        flash("Please login!", "danger")
        return redirect('/user-login')

    # =====================================================
    # CHECK ADDRESS
    # =====================================================

    address = session.get('address')

    if not address:
        flash("Please add your delivery address first.", "danger")
        return redirect('/user/address')


    # =====================================================
    # CHECK CHECKOUT SOURCE & ITEMS
    # =====================================================

    checkout_type = session.get('checkout_type', 'cart')

    if checkout_type == 'buy_now' and session.get('buy_now_product'):
        buy_now_product = session['buy_now_product']
        checkout_items = {
            str(buy_now_product['product_id']): {
                'name': buy_now_product['name'],
                'price': float(buy_now_product['price']),
                'quantity': buy_now_product['quantity']
            }
        }
    else:
        checkout_items = session.get('checkout_items') or session.get('cart', {})
        checkout_type = 'cart'

    if not checkout_items:
        flash("Your cart is empty!", "danger")
        return redirect('/user/cart')

    # Store what is being purchased
    session['checkout_items'] = checkout_items
    session['checkout_type'] = checkout_type
    session.modified = True

    # =====================================================
    # CALCULATE TOTAL
    # =====================================================

    total_amount = sum(
        float(item['price']) * int(item['quantity'])
        for item in checkout_items.values()
    )

    razorpay_amount = int(round(total_amount * 100))

    if razorpay_amount < 100:
        flash("Order amount must be at least ₹1.00 for payment processing.", "danger")
        return redirect('/user/cart' if checkout_type == 'cart' else '/user/products')

    # =====================================================
    # CREATE RAZORPAY ORDER
    # =====================================================

    try:
        razorpay_order = razorpay_client.order.create({
            "amount": razorpay_amount,
            "currency": "INR",
            "payment_capture": "1"
        })
    except Exception as e:
        flash(f"Payment gateway error: {str(e)}", "danger")
        return redirect('/user/cart' if checkout_type == 'cart' else '/user/products')

    session['razorpay_order_id'] = razorpay_order['id']

    # =====================================================
    # PAYMENT PAGE
    # =====================================================

    return render_template(
        "user/payment.html",
        amount=total_amount,
        key_id=config.RAZORPAY_KEY_ID,
        order_id=razorpay_order['id'],
        address=address
    )

# =================================================================
# TEMP SUCCESS PAGE (Verification in Day 13)
# =================================================================
@app.route('/payment-success')
def payment_success():

    payment_id = request.args.get('payment_id')
    order_id = request.args.get('order_id')

    if not payment_id:
        flash("Payment failed!", "danger")
        return redirect('/user/cart')

    return render_template(
        "user/payment_success.html",
        payment_id=payment_id,
        order_id=order_id
    )


@app.route('/verify-payment', methods=['POST'])
def verify_payment():
    if 'user_id' not in session:
        flash("Please login to complete the payment.", "danger")
        return redirect('/user-login')

    # Read values posted from frontend
    razorpay_payment_id = request.form.get('razorpay_payment_id')
    razorpay_order_id = request.form.get('razorpay_order_id')
    razorpay_signature = request.form.get('razorpay_signature')

    if not (razorpay_payment_id and razorpay_order_id and razorpay_signature):
        flash("Payment verification failed (missing data).", "danger")
        return redirect('/user/cart')

    # Build verification payload required by Razorpay client.utility
    payload = {
        'razorpay_order_id': razorpay_order_id,
        'razorpay_payment_id': razorpay_payment_id,
        'razorpay_signature': razorpay_signature
    }

    try:
        # This will raise an error if signature invalid
        razorpay_client.utility.verify_payment_signature(payload)

    except Exception as e:
        # Verification failed
        app.logger.error("Razorpay signature verification failed: %s", str(e))
        flash("Payment verification failed. Please contact support.", "danger")
        return redirect('/user/cart')

    # Signature verified — now store order and items into DB
    user_id = session['user_id']
    checkout_items = session.get('cart', {})

    if not checkout_items:
        flash("Cart is empty. Cannot create order.", "danger")
        return redirect('/user/products')

    # Calculate total amount (ensure same as earlier)
    total_amount = sum(
    float(item['price']) * int(item['quantity'])
    for item in checkout_items.values()
)

    # DB insert: orders and order_items
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get address from session
        addr_data = session.get('address', {})
        shipping_name = addr_data.get('name', '')
        shipping_phone = addr_data.get('phone', '')
        shipping_address = addr_data.get('address', '')
        shipping_city = addr_data.get('city', '')
        shipping_state = addr_data.get('state', '')
        shipping_pincode = addr_data.get('pincode', '')

        # Insert into orders table
        cursor.execute("""
            INSERT INTO orders (
                user_id, razorpay_order_id, razorpay_payment_id, amount, payment_status,
                shipping_name, shipping_phone, shipping_address, shipping_city, shipping_state, shipping_pincode
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id, razorpay_order_id, razorpay_payment_id, total_amount, 'paid',
            shipping_name, shipping_phone, shipping_address, shipping_city, shipping_state, shipping_pincode
        ))

        order_db_id = cursor.lastrowid  # newly created order's primary key

        # Insert all items
        for pid_str, item in checkout_items.items():
            product_id = int(pid_str)
            cursor.execute("""
                INSERT INTO order_items (order_id, product_id, product_name, quantity, price)
                VALUES (%s, %s, %s, %s, %s)
            """, (order_db_id, product_id, item['name'], item['quantity'], item['price']))

        # Commit transaction
        conn.commit()

        # Clear cart and temporary razorpay order id
        # Clear purchased data
        session.pop('checkout_items', None)
        session.pop('buy_now_product', None)
        session.pop('razorpay_order_id', None)
        # If purchase came from cart, clear cart
        if session.get('checkout_type') == 'cart':
            session.pop('cart', None)
            session.pop('checkout_type', None)

        flash("Payment successful and order placed!", "success")
        return redirect(f"/user/order-success/{order_db_id}")

    except Exception as e:
        # Rollback and log error
        conn.rollback()
        app.logger.error("Order storage failed: %s\n%s", str(e), traceback.format_exc())
        flash("There was an error saving your order. Contact support.", "danger")
        return redirect('/user/cart')

    finally:
        cursor.close()
        conn.close()

@app.route('/user/order-success/<int:order_db_id>')
def order_success(order_db_id):
    if 'user_id' not in session:
        flash("Please login!", "danger")
        return redirect('/user-login')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM orders WHERE order_id=%s AND user_id=%s", (order_db_id, session['user_id']))
    order = cursor.fetchone()

    cursor.execute("SELECT * FROM order_items WHERE order_id=%s", (order_db_id,))
    items = cursor.fetchall()

    cursor.close()
    conn.close()

    if not order:
        flash("Order not found.", "danger")
        return redirect('/user/products')

    return render_template("user/order_success.html", order=order, items=items)
@app.route('/user/my-orders')
def my_orders():
    if 'user_id' not in session:
        flash("Please login!", "danger")
        return redirect('/user-login')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM orders WHERE user_id=%s ORDER BY created_at DESC", (session['user_id'],))
    orders = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("user/my_orders.html", orders=orders)


# =========================================================
# BUY NOW
# =========================================================

@app.route('/user/buy-now/<int:pid>')
def buy_now(pid):

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT * FROM products WHERE product_id = %s",
        (pid,)
    )

    product = cursor.fetchone()

    cursor.close()
    conn.close()

    if not product:
        flash("Product not found!", "error")
        return redirect('/user/products')

    # Clear any cart checkout selection
    session.pop('checkout_items', None)

    # Store selected product in session
    session['buy_now_product'] = {
        'product_id': product['product_id'],
        'name': product['name'],
        'price': float(product['price']),
        'image': product['image'],
        'quantity': 1
    }
    session['checkout_type'] = 'buy_now'
    session.modified = True

    # Go to address page
    return redirect('/user/address?source=buy_now')


# =========================================================
# ADDRESS
# =========================================================

@app.route('/user/address')
def address():

    if 'user_id' not in session:
        flash("Please login!", "danger")
        return redirect('/user-login')

    # Know whether user came from cart or Buy Now
    source = request.args.get('source', 'cart')

    # -------------------------
    # BUY NOW
    # -------------------------
    if source == 'buy_now':

        product = session.get('buy_now_product')

        if not product:
            flash("No product selected!", "danger")
            return redirect('/user/products')

    # -------------------------
    # CART
    # -------------------------
    else:

        cart = session.get('cart', {})

        if not cart:
            flash("Your cart is empty!", "danger")
            return redirect('/user/products')

        product = None

    saved_address = session.get('address', {})

    return render_template(
        'user/address.html',
        product=product,
        address=saved_address,
        source=source
    )


# =========================================================
# SAVE / UPDATE ADDRESS
# =========================================================

@app.route('/user/save-address', methods=['POST'])
def save_address():

    if 'user_id' not in session:
        flash("Please login!", "danger")
        return redirect('/user-login')

    source = request.form.get('source', 'cart')

    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()
    address_line = request.form.get('address', '').strip()
    city = request.form.get('city', '').strip()
    state = request.form.get('state', '').strip()
    pincode = request.form.get('pincode', '').strip()

    if not all([
        name,
        phone,
        address_line,
        city,
        state,
        pincode
    ]):
        flash("Please fill all address fields.", "danger")

        return redirect(
            f'/user/address?source={source}'
        )

    # Store address in session
    session['address'] = {
        'name': name,
        'phone': phone,
        'address': address_line,
        'city': city,
        'state': state,
        'pincode': pincode
    }

    session.modified = True

    flash("Address saved successfully!", "success")

    # Go to payment
    return redirect('/user/pay')


# =========================================================
# UPDATE ADDRESS
# =========================================================

@app.route('/user/update-address')
def update_address():

    if 'user_id' not in session:
        flash("Please login!", "danger")
        return redirect('/user-login')

    source = request.args.get('source', 'cart')

    return redirect(
        f'/user/address?source={source}'
    )


@app.route("/user/download-invoice/<int:order_id>")
def download_invoice(order_id):

    if 'user_id' not in session:
        flash("Please login!", "danger")
        return redirect('/user-login')

    # Fetch order
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM orders WHERE order_id=%s AND user_id=%s",
                   (order_id, session['user_id']))
    order = cursor.fetchone()

    if not order:
        cursor.close()
        conn.close()
        flash("Order not found.", "danger")
        return redirect('/user/my-orders')

    cursor.execute("SELECT * FROM order_items WHERE order_id=%s", (order_id,))
    items = cursor.fetchall()

    # Fetch user details
    cursor.execute("SELECT user_id, name, email, phone FROM users WHERE user_id=%s", (session['user_id'],))
    user = cursor.fetchone()

    cursor.close()
    conn.close()

    # Consolidate address information (order shipping info -> fallback to session -> fallback to user)
    saved_address = session.get('address', {})
    address = {
        'name': order.get('shipping_name') or (user.get('name') if user else '') or saved_address.get('name') or 'Customer',
        'phone': order.get('shipping_phone') or (user.get('phone') if user else '') or saved_address.get('phone') or '',
        'address': order.get('shipping_address') or saved_address.get('address') or 'Street / Area',
        'city': order.get('shipping_city') or saved_address.get('city') or 'Hyderabad',
        'state': order.get('shipping_state') or saved_address.get('state') or 'Telangana',
        'pincode': order.get('shipping_pincode') or saved_address.get('pincode') or ''
    }

    # Render invoice HTML
    html = render_template("user/invoice.html", order=order, items=items, address=address, user=user)

    pdf = generate_pdf(html)
    if not pdf:
        flash("Error generating PDF", "danger")
        return redirect('/user/my-orders')

    # Prepare response
    response = make_response(pdf.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f"attachment; filename=invoice_{order_id}.pdf"

    return response

# =========================================================
# RUN SERVER
# =========================================================

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() in ("true", "1")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)