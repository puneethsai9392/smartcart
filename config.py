import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY")   # used for sessions

# SQLite Database Configuration
DB_PATH = os.environ.get("DB_PATH", "smartcart.db")

# MySQL Database Configuration (kept for reference)
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")  
DB_NAME = os.environ.get("DB_NAME", "smartcart_db")

# Email SMTP Settings
MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "True").lower() in ("true", "1", "yes")
MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
if MAIL_PASSWORD:
    MAIL_PASSWORD = MAIL_PASSWORD.replace(" ", "").strip()

# Resend HTTPS Email API (Preferred for cloud hosts blocking outbound SMTP)
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM = os.getenv("RESEND_FROM", "SmartCart <onboarding@resend.dev>")

ADMIN_UPLOAD_FOLDER = os.environ.get("ADMIN_UPLOAD_FOLDER", 'static/uploads/admin_profiles')

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID") 
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET") 

