import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY", "abc123")   # used for sessions

# SQLite Database Configuration
DB_PATH = os.environ.get("DB_PATH", "smartcart.db")

# MySQL Database Configuration (kept for reference)
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "root")  
DB_NAME = os.environ.get("DB_NAME", "smartcart_db")

# Email SMTP Settings
MAIL_SERVER = os.environ.get("MAIL_SERVER", 'smtp.gmail.com')
MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "True").lower() in ("true", "1", "yes")
MAIL_USERNAME = os.environ.get("MAIL_USERNAME", 'puneethsai572@gmail.com')
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", 'wdpp oagu uluu gguq')

ADMIN_UPLOAD_FOLDER = os.environ.get("ADMIN_UPLOAD_FOLDER", 'static/uploads/admin_profiles')

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "rzp_test_TcBVqgh8wunkV7") 
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "Dn04TkvACMHiMo7q7FG2KCoa") 
