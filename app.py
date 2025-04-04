from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash, send_file
import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import os
import numpy as np
import pandas as pd
import io
import logging
import atexit
import pickle
from functools import wraps
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from io import BytesIO
import base64

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24))
CORS(app)  # Enable CORS for all routes

# Email configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'mikaelsonklaus726@gmail.com'
app.config['MAIL_PASSWORD'] = 'rmgl cibt ginv ipuc'
app.config['MAIL_DEFAULT_SENDER'] = 'mikaelsonklaus726@gmail.com'

mail = Mail(app)
serializer = URLSafeTimedSerializer(app.secret_key)

# Configure your database connection here
DATABASE = {
    'dbname': 'users',
    'user': 'postgres',
    'password': 'Amaterasu@007',
    'host': 'localhost'
}


# Load the best model, label encoders, and scaler
with open('rf_model.pkl', 'rb') as file:
    best_model = pickle.load(file)

with open('label_encoders.pkl', 'rb') as file:
    label_encoders = pickle.load(file)

with open('scaler.pkl', 'rb') as file:
    scaler = pickle.load(file)


def get_db_connection():
    conn = psycopg2.connect(**DATABASE)
    return conn

# Generating token with expiration of 2 minutes
def generate_confirmation_token(email):
    return serializer.dumps(email, salt='email-confirmation-salt')

# Verifying the token within the expiration period
def confirm_token(token, expiration=120):
    try:
        email = serializer.loads(token, salt='email-confirmation-salt', max_age=expiration)
    except Exception as e:
        return False
    return email

def clean_up_temp_users():
    conn = get_db_connection()
    cur = conn.cursor()
    expiration_time = datetime.now() - timedelta(minutes=2)  # records older than 2 minutes
    cur.execute('DELETE FROM temp_users WHERE created_at < %s', (expiration_time,))
    conn.commit()
    cur.close()
    conn.close()

# Example average values dictionary
average_data = {
    'male': {
        '18-39': {'ap_hi': 119, 'ap_lo': 70, 'bmi': 26.8},
        '40-59': {'ap_hi': 124, 'ap_lo': 77, 'bmi': 27.6},
        '60+': {'ap_hi': 133, 'ap_lo': 69, 'bmi': 29.5}
    },
    'female': {
        '18-39': {'ap_hi': 110, 'ap_lo': 68, 'bmi': 27.5},
        '40-59': {'ap_hi': 122, 'ap_lo': 74, 'bmi': 28.7},
        '60+': {'ap_hi': 139, 'ap_lo': 68, 'bmi': 26.7}
    }
}

def get_age_group(age):
    if 18 <= age <= 39:
        return '18-39'
    elif 40 <= age <= 59:
        return '40-59'
    else:
        return '60+'

def generate_main_chart(risk_factors):
    exclude_columns = ['age', 'height', 'weight', 'gender', 'cholesterol', 'diabetes', 'smoking', 'alcohol_consumption', 'physically_active', 'obesity', 'hypertension', 'stress', 'anxiety', 'depression', 'diet']
    factors = [key for key in risk_factors.keys() if key not in exclude_columns]
    values = [float(risk_factors[key]) for key in factors]

    age = int(risk_factors['age'])
    gender = risk_factors['gender'].lower()
    age_group = get_age_group(age)

    average_values = [average_data[gender][age_group][factor] for factor in factors]

    fig, ax = plt.subplots(figsize=(10, 8))
    index = np.arange(len(factors))
    bar_width = 0.35

    bar1 = plt.bar(index, values, bar_width, label='Your Values')
    bar2 = plt.bar(index + bar_width, average_values, bar_width, label='Average Values')

    plt.xlabel('Risk Factors')
    plt.ylabel('Values')
    plt.title('Your Input Vs Average Values (as per Age and Gender)')
    plt.xticks(index + bar_width / 2, factors, rotation=90)
    plt.legend()

    plt.tight_layout()
    
    # Save plot to a bytes buffer
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    
    return buf

@app.route('/')
def index():
    logged_in = 'username' in session
    return render_template('index.html', logged_in=logged_in, username=session.get('username'),role=session.get('role'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            username = request.form['username']
            email = request.form['email']
            password = request.form['password']
            confirm_password = request.form['confirm_password']

            if password != confirm_password:
                flash('Passwords do not match!')
                return redirect(url_for('login'))

            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute('SELECT * FROM users_final WHERE email = %s', (email,))
            existing_user = cur.fetchone()
            if existing_user:
                flash('Email is already registered!')
                return redirect(url_for('login'))

            verification_token = generate_confirmation_token(email)
            hashed_password = generate_password_hash(password, method='sha256')
            cur.execute('INSERT INTO temp_users (username, email, password, verification_token) VALUES (%s, %s, %s, %s)', 
                        (username, email, hashed_password, verification_token))
            conn.commit()
            cur.close()
            conn.close()

            verification_link = url_for('confirm_email', token=verification_token, _external=True)
            send_verification_email(email, verification_link)

            flash('A verification email has been sent to your email address.')
            return redirect(url_for('login'))
        except Exception as e:
            logger.error(f"Error during registration: {e}")
            flash('An error occurred during registration. Please try again.')
            return redirect(url_for('login'))

    return render_template('login.html')


@app.route('/confirm/<token>')
def confirm_email(token):
    try:
        email = confirm_token(token)
        if not email:
            flash('The verification link is invalid or has expired.')
            return redirect(url_for('login'))

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM temp_users WHERE email = %s AND verification_token = %s', (email, token))
        temp_user = cur.fetchone()

        if not temp_user:
            flash('The verification process failed. Please register again.')
            cur.close()
            conn.close()
            return redirect(url_for('login'))

        cur.execute('INSERT INTO users_final (username, email, password, is_verified) VALUES (%s, %s, %s, %s)', 
                    (temp_user[1], temp_user[2], temp_user[3], True))
        cur.execute('DELETE FROM temp_users WHERE email = %s', (email,))
        conn.commit()
        cur.close()
        conn.close()

        flash('Your email has been verified. You can now log in.')
        return redirect(url_for('login'))
    except Exception as e:
        logger.error(f"Error during email confirmation: {e}")
        flash('An error occurred during email confirmation. Please try again.')
        return redirect(url_for('login'))


def send_verification_email(to_email, link):
    msg = Message('Email Verification', recipients=[to_email])
    msg.html = f'''
    <!DOCTYPE html>
    <html>
    <head>

      <meta charset="utf-8">
      <meta http-equiv="x-ua-compatible" content="ie=edge">
      <title>Email Confirmation</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style type="text/css">
      @media screen {{
        @font-face {{
          font-family: 'Source Sans Pro';
          font-style: normal;
          font-weight: 400;
          src: local('Source Sans Pro Regular'), local('SourceSansPro-Regular'), url(https://fonts.gstatic.com/s/sourcesanspro/v10/ODelI1aHBYDBqgeIAH2zlBM0YzuT7MdOe03otPbuUS0.woff) format('woff');
        }}
        @font-face {{
          font-family: 'Source Sans Pro';
          font-style: normal;
          font-weight: 700;
          src: local('Source Sans Pro Bold'), local('SourceSansPro-Bold'), url(https://fonts.gstatic.com/s/sourcesanspro/v10/toadOcfmlt9b38dHJxOBGFkQc6VGVFSmCnC_l7QZG60.woff) format('woff');
        }}
      }}
      body,
      table,
      td,
      a {{
        -ms-text-size-adjust: 100%;
        -webkit-text-size-adjust: 100%;
      }}
      table,
      td {{
        mso-table-rspace: 0pt;
        mso-table-lspace: 0pt;
      }}
      img {{
        -ms-interpolation-mode: bicubic;
      }}
      a[x-apple-data-detectors] {{
        font-family: inherit !important;
        font-size: inherit !important;
        font-weight: inherit !important;
        line-height: inherit !important;
        color: inherit !important;
        text-decoration: none !important;
      }}
      div[style*="margin: 16px 0;"] {{
        margin: 0 !important;
      }}
      body {{
        width: 100% !important;
        height: 100% !important;
        padding: 0 !important;
        margin: 0 !important;
      }}
      table {{
        border-collapse: collapse !important;
      }}
      a {{
        color: #1a82e2;
      }}
      img {{
        height: auto;
        line-height: 100%;
        text-decoration: none;
        border: 0;
        outline: none;
      }}
      </style>

    </head>
    <body style="background-color: #e9ecef;">

      <div class="preheader" style="display: none; max-width: 0; max-height: 0; overflow: hidden; font-size: 1px; line-height: 1px; color: #fff; opacity: 0;">
        A preheader is the short summary text that follows the subject line when an email is viewed in the inbox.
      </div>

      <table border="0" cellpadding="0" cellspacing="0" width="100%">

        <tr>
          <td align="center" bgcolor="#e9ecef">
            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px;">
              <tr>
                <td align="left" bgcolor="#ffffff" style="padding: 36px 24px 0; font-family: 'Source Sans Pro', Helvetica, Arial, sans-serif; border-top: 3px solid #d4dadf;">
                  <h1 style="margin: 0; font-size: 32px; font-weight: 700; letter-spacing: -1px; line-height: 48px;">Confirm Your Email Address</h1>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <tr>
          <td align="center" bgcolor="#e9ecef">
            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px;">

              <tr>
                <td align="left" bgcolor="#ffffff" style="padding: 24px; font-family: 'Source Sans Pro', Helvetica, Arial, sans-serif; font-size: 16px; line-height: 24px;">
                  <p style="margin: 0;">Tap the button below to confirm your email address. If you didn't create an account with HeartGaurd, you can safely delete this email.</p>
                </td>
              </tr>

              <tr>
                <td align="left" bgcolor="#ffffff">
                  <table border="0" cellpadding="0" cellspacing="0" width="100%">
                    <tr>
                      <td align="center" bgcolor="#ffffff" style="padding: 12px;">
                        <table border="0" cellpadding="0" cellspacing="0">
                          <tr>
                            <td align="center" bgcolor="#1a82e2" style="border-radius: 6px;">
                              <a href="{link}" target="_blank" style="display: inline-block; padding: 16px 36px; font-family: 'Source Sans Pro', Helvetica, Arial, sans-serif; font-size: 16px; color: #ffffff; text-decoration: none; border-radius: 6px;">Verify Email</a>
                            </td>
                          </tr>
                        </table>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>

              <tr>
                <td align="left" bgcolor="#ffffff" style="padding: 24px; font-family: 'Source Sans Pro', Helvetica, Arial, sans-serif; font-size: 16px; line-height: 24px;">
                  <p style="margin: 0;">If that doesn't work, copy and paste the following link in your browser:</p>
                  <p style="margin: 0;"><a href="{link}" target="_blank">{link}</a></p>
                </td>
              </tr>

              <tr>
                <td align="left" bgcolor="#ffffff" style="padding: 24px; font-family: 'Source Sans Pro', Helvetica, Arial, sans-serif; font-size: 16px; line-height: 24px; border-bottom: 3px solid #d4dadf">
                  <p style="margin: 0;">Cheers,<br> HeartGaurd</p>
                </td>
              </tr>

            </table>
          </td>
        </tr>

      </table>

    </body>
    </html>
    '''
    mail.send(msg)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM users_final WHERE email = %s', (email,))
        user = cur.fetchone()
        cur.execute("SELECT * FROM admins WHERE email = %s", (email,))
        admin = cur.fetchone()
        cur.close()
        conn.close()
        
        if user and check_password_hash(user[5], password):
            session['user_id'] = user[0]
            session['username'] = user[3]
            session['role'] = 'user'
            print(f"User {user[3]} logged in successfully.")
            if not user[12]:
                return redirect(url_for('complete_register'))
            else:
                return redirect(url_for('index'))
        elif admin and check_password_hash(admin[3], password):
            session['admin_id'] = admin[0]
            session['username'] = admin[1]
            session['role'] = 'admin'
            print(f"Admin {admin[1]} logged in successfully.")
            return redirect(url_for('admin_profile'))
        else:
            flash('Login failed! Check your email and password.')
            print("Login failed. Invalid credentials.")
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    flash('You have been logged out.')
    return redirect(url_for('index'))

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session and 'admin_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    logged_in = 'username' in session
    if request.method == 'POST':
        if session['role']=='user':
          user_id = session['user_id']
        elif session['role']=='admin':
          admin_id=session['admin_id']
        current_password = request.form['curr_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']
        
        # Connect to the database
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        if session["role"]=="user":
          # Fetch the user from the database
          cur.execute('SELECT * FROM users_final WHERE id = %s', (user_id,))
          user = cur.fetchone()
          
          # Verify the current password
          if not user or not check_password_hash(user['password'], current_password):
              flash('Current password is incorrect.')
              return render_template('change_password.html')
          
          # Check if the new password and confirm password match
          if new_password != confirm_password:
              flash('New passwords do not match!')
              return render_template('change_password.html')
          
          # Hash the new password and update in the database
          hashed_password = generate_password_hash(new_password, method='sha256')
          cur.execute('UPDATE users_final SET password = %s WHERE id = %s', (hashed_password, admin_id))
          conn.commit()
        elif session["role"]=="admin":
            # Fetch the user from the database
          cur.execute('SELECT * FROM admins WHERE id = %s', (admin_id,))
          admins = cur.fetchone()
          
          # Verify the current password
          if not admins or not check_password_hash(admins['password'], current_password):
              flash('Current password is incorrect.')
              return render_template('change_password.html')
          
          # Check if the new password and confirm password match
          if new_password != confirm_password:
              flash('New passwords do not match!')
              return render_template('change_password.html')
          
          # Hash the new password and update in the database
          hashed_password = generate_password_hash(new_password, method='sha256')
          cur.execute('UPDATE admins SET password = %s WHERE id = %s', (hashed_password, admin_id))
          conn.commit()
            
            
        
        # Close the cursor and connection
        cur.close()
        conn.close()
        
        flash('Your password has been updated.')
        return redirect(url_for('change_password'))
    
    return render_template('change_password.html', logged_in=logged_in)

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        if 'email' in request.form:
            email = request.form['email']
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute('SELECT * FROM users_final WHERE email = %s', (email,))
            user = cur.fetchone()
            if user:
                reset_token = generate_confirmation_token(email)
                reset_link = url_for('forgot_password', token=reset_token, _external=True)
                send_reset_password_email(email, reset_link)
                flash('A password reset link has been sent to your email address.')
            else:
                flash('No account found with that email address.')
            cur.close()
            conn.close()
        elif 'token' in request.form:
            token = request.form['token']
            new_password = request.form['new_password']
            confirm_password = request.form['confirm_password']
            if new_password != confirm_password:
                flash('Passwords do not match!')
                return render_template('forgot_password.html', token=token)
            email = confirm_token(token)
            if not email:
                flash('The reset link is invalid or has expired.')
                return render_template('forgot_password.html')
            hashed_password = generate_password_hash(new_password, method='sha256')
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute('UPDATE users_final SET password = %s WHERE email = %s', (hashed_password, email))
            conn.commit()
            cur.close()
            conn.close()
            flash('Your password has been updated. You can now log in.')
            return redirect(url_for('login'))

    token = request.args.get('token')
    return render_template('forgot_password.html', token=token)

def send_reset_password_email(to_email, link):
    msg = Message('Reset Your Password', recipients=[to_email])
    msg.html = f'''
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <meta http-equiv="x-ua-compatible" content="ie=edge">
      <title>Password Reset</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style type="text/css">
        body, table, td, a {{
          -ms-text-size-adjust: 100%;
          -webkit-text-size-adjust: 100%;
        }}
        table, td {{
          mso-table-rspace: 0pt;
          mso-table-lspace: 0pt;
        }}
        img {{
          -ms-interpolation-mode: bicubic;
        }}
        a[x-apple-data-detectors] {{
          font-family: inherit !important;
          font-size: inherit !important;
          font-weight: inherit !important;
          line-height: inherit !important;
          color: inherit !important;
          text-decoration: none !important;
        }}
        div[style*="margin: 16px 0;"] {{
          margin: 0 !important;
        }}
        body {{
          width: 100% !important;
          height: 100% !important;
          padding: 0 !important;
          margin: 0 !important;
        }}
        table {{
          border-collapse: collapse !important;
        }}
        a {{
          color: #ffffff;
        }}
        img {{
          height: auto;
          line-height: 100%;
          text-decoration: none;
          border: 0;
          outline: none;
        }}
        .button {{
          font-family: 'Source Sans Pro', Helvetica, Arial, sans-serif;
          font-size: 16px;
          color: #ffffff;
          text-decoration: none;
          background-color: #ff6f61;
          padding: 12px 24px;
          border-radius: 6px;
          display: inline-block;
        }}
      </style>
    </head>
    <body style="background-color: #e9ecef;">
      <table border="0" cellpadding="0" cellspacing="0" width="100%">
        <tr>
          <td align="center" bgcolor="#e9ecef">
            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px;">
              <tr>
                <td align="center" valign="top" style="padding: 36px 24px;">
                  <h1 style="margin: 0; font-size: 32px; font-weight: 700; letter-spacing: -1px; line-height: 48px;">Reset your password</h1>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td align="center" bgcolor="#e9ecef">
            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px;">
              <tr>
                <td align="left" bgcolor="#ffffff" style="padding: 36px 24px; font-family: 'Source Sans Pro', Helvetica, Arial, sans-serif; font-size: 16px; line-height: 24px;">
                  <p style="margin: 0;">Hi,</p>
                  <p style="margin: 0;">We've received a request to reset your password. If you didn't make the request, just ignore this message. Otherwise, you can reset your password using the button below:</p>
                </td>
              </tr>
              <tr>
                <td align="center" bgcolor="#ffffff" style="padding: 24px;">
                  <a href="{link}" target="_blank" class="button">Reset your password</a>
                </td>
              </tr>
              <tr>
                <td align="left" bgcolor="#ffffff" style="padding: 24px; font-family: 'Source Sans Pro', Helvetica, Arial, sans-serif; font-size: 16px; line-height: 24px;">
                  <p style="margin: 0;">If that doesn't work, copy and paste the following link in your browser:</p>
                  <p style="margin: 0;"><a href="{link}" target="_blank">{link}</a></p>
                </td>
              </tr>
              <tr>
                <td align="left" bgcolor="#ffffff" style="padding: 24px; font-family: 'Source Sans Pro', Helvetica, Arial, sans-serif; font-size: 16px; line-height: 24px; border-bottom: 3px solid #d4dadf;">
                  <p style="margin: 0;">Thanks,<br> The Team</p>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    '''
    mail.send(msg)


@app.route('/complete_register', methods=['GET','POST'])
def complete_register():
    logged_in = 'username' in session
    if 'username' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM users_final WHERE username = %s', (session['username'],))
    user_data = cur.fetchone()
    cur.close()
    conn.close()

    if request.method == 'POST':
        firstname = request.form['firstname']
        lastname = request.form['lastname']
        gender = request.form['gender']
        age = request.form['age']
        phone = request.form['phone']
        postcode = request.form['postcode']
        address1 = request.form['address1']
        address2 = request.form['address2']
        city = request.form['city']
        country = request.form['country']
        smoking_status = request.form['smoking']
        diabetes = request.form['diabetes']
        height = request.form['height']
        weight = request.form['weight']
        physical_activity = request.form['physical_activity']
        diet = request.form['diet']
        alcohol = request.form['alcohol']
        stress = request.form['stress']
        anxiety = request.form['anxiety']
        depression = request.form['depression']

        address = f"{address1} {address2}".strip()
        weight = float(weight)
        height = float(height)
        bmi = (weight / (height / 100) ** 2)
        bmi = round(bmi, 2)

        # Determine obesity category
        if bmi < 18.5:
            obesity = 'Underweight'
        elif 18.5 <= bmi < 24.9:
            obesity = 'Normal'
        elif 25 <= bmi < 29.9:
            obesity = 'Overweight'
        elif bmi >= 30:
            obesity = 'Obese'
        else:
            obesity = 'Unknown'

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
                    UPDATE users_final
                    SET first_name = %s,
                        last_name = %s,
                        phone_no = %s,
                        address = %s,
                        postcode = %s,
                        city = %s,
                        country = %s
                    WHERE username = %s
                ''', (firstname, lastname, phone, address, postcode, city, country,session['username']))
        cur.execute('INSERT INTO risk_records (user_id, age, gender, smoking_status, diabetes, height, weight, obesity, physical_activity, diet, alcohol, stress, anxiety, depression, bmi) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)', 
                    (session['user_id'], age, gender, smoking_status, diabetes, height, weight, obesity, physical_activity, diet, alcohol, stress, anxiety, depression, bmi))
        cur.execute('UPDATE users_final SET registration_completed = TRUE WHERE username = %s', (session['username'],))
        conn.commit()
        cur.close()
        conn.close()

        return redirect(url_for('index'))

    return render_template('complete_register.html', logged_in=logged_in, username=session.get('username'),user_data=user_data)

@app.route('/user_profile')
def user_profile():
    logged_in = 'username' in session
    if 'username' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM users_final WHERE username = %s', (session['username'],))
    user_data = cur.fetchone()
    cur.execute('SELECT * FROM risk_records WHERE user_id = %s ORDER BY record_id DESC LIMIT 1', (session['user_id'],))
    user_risk_data = cur.fetchone()
    cur.close()
    conn.close()
    return render_template('user_profile.html', logged_in=logged_in,user_data=user_data, user_risk_data=user_risk_data)

@app.route('/admin_profile')
def admin_profile():
    logged_in = 'username' in session
    if 'username' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM admins WHERE username = %s ', (session['username'],))
    admin_data = cur.fetchone()
    cur.close()
    conn.close()

    return render_template('admin_profile.html', logged_in=logged_in, admin_data=admin_data)

@app.route('/data_input_form', methods=['GET', 'POST'])
def data_input_form():
    logged_in = 'username' in session
    if not logged_in:
        return redirect(url_for('login'))

    user_id = session['user_id']

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM risk_records WHERE user_id = %s ORDER BY record_id DESC LIMIT 1', (user_id,))
    user_risk_data = cur.fetchone()

    if request.method == 'POST':
        form_data = {
            'age': request.form['age'],
            'gender': request.form['gender'],
            'smoking': request.form['smoking'],
            'ap_hi': request.form['ap_hi'],
            'ap_lo': request.form['ap_lo'],
            'cholesterol': request.form['cholesterol'],
            'diabetes': request.form['diabetes'],
            'height': request.form['height'],
            'weight': request.form['weight'],
            'physical_activity': request.form['physical_activity'],
            'diet': request.form['diet'],
            'alcohol': request.form['alcohol'],
            'stress': request.form['stress'],
            'anxiety': request.form['anxiety'],
            'depression': request.form['depression'],
        }
        weight = float(form_data['weight'])
        height = float(form_data['height'])
        bmi = (weight /(height / 100) ** 2)
        form_data['bmi']  = round(bmi, 1)

        # Determine obesity category
        if form_data['bmi']  < 18.5:
            form_data['obesity'] = 'Underweight'
        elif 18.5 <= form_data['bmi']  < 24.9:
            form_data['obesity'] = 'Normal'
        elif 25 <= form_data['bmi']  < 29.9:
            form_data['obesity'] = 'Overweight'
        elif form_data['bmi']  >= 30:
            form_data['obesity'] = 'Obese'
        else:
            form_data['obesity'] = 'Unknown'
        session['temp_form_data'] = form_data
        return redirect(url_for('data_check_form'))

    cur.close()
    conn.close()

    return render_template('data_input_form.html', logged_in=logged_in, user_risk_data=user_risk_data)

@app.route('/data_check_form', methods=['GET'])
def data_check_form():
    logged_in = 'username' in session
    if not logged_in:
        return redirect(url_for('login'))

    temp_form_data = session.get('temp_form_data')
    print(temp_form_data)
    if not temp_form_data:
        return redirect(url_for('data_input_form'))

    return render_template('data_check_form.html', logged_in=logged_in, user_risk_data=temp_form_data)

@app.route('/user_dashboard')
def user_dashboard():
    logged_in = 'username' in session
    if 'username' in session and session.get('role') == 'user':
        conn = get_db_connection()
        cur = conn.cursor()

        columns = ['record_id','age','gender','ap_hi','ap_lo','hypertension','height','weight','bmi','obesity',
                   'cholesterol_levels','diabetes','smoking_status','alcohol','diet','physical_activity',
                   'stress','anxiety','depression','cvd_risk']
        
        # Map of original column names to their new display names
        column_display_names = {
            'record_id': 'Record ID',
            'age': 'Age',
            'gender': 'Gender',
            'ap_hi': 'Systolic BP',
            'ap_lo': 'Diastolic BP',
            'hypertension': 'Hypertension',
            'height': 'Height',
            'weight': 'Weight',
            'bmi': 'BMI',
            'obesity': 'Obesity',
            'cholesterol_levels': 'Cholesterol',
            'diabetes': 'Diabetes',
            'smoking_status': 'Smoking Status',
            'alcohol': 'Alcohol Consumption',
            'diet': 'Diet',
            'physical_activity': 'Physical Activity',
            'stress': 'Stress',
            'anxiety': 'Anxiety',
            'depression': 'Depression',
            'cvd_risk': 'CVD Risk'
          }

        # Fetch all records for the user risk records in the specified order
        query = f"SELECT {', '.join(columns)} FROM risk_records WHERE user_id = %s ORDER BY record_id DESC"
        cur.execute(query, (session['user_id'],))
        user_risk_data = cur.fetchall()

        # Fetch all records for the user data in the specified order
        query = f"SELECT * FROM users_final WHERE id = %s"
        cur.execute(query, (session['user_id'],))
        user_data_row = cur.fetchone()

        # Convert the user data row to a dictionary
        user_data_columns = [desc[0] for desc in cur.description]
        user_data = dict(zip(user_data_columns, user_data_row))

        # Fetch all admin emails
        query = "SELECT email FROM admins"
        cur.execute(query)
        # Extract emails from the query result
        admin_emails = [row[0] for row in cur.fetchall()]  
        # Join emails with a comma for the mailto link
        admin_emails_str = ",".join(admin_emails)  

        cur.close()
        conn.close()

        return render_template('user_dashboard.html', logged_in=logged_in, columns=columns, column_display_names=column_display_names, user_risk_data=user_risk_data, user_data=user_data, admin_emails_str=admin_emails_str, username=session['username'])
    return redirect(url_for('login'))

@app.route('/admin_dashboard')
def admin_dashboard():
    logged_in = 'username' in session
    if 'username' in session and session.get('role') == 'admin':
        conn = get_db_connection()
        cur = conn.cursor()

        columns = ['record_id', 'id', 'first_name', 'last_name', 'email', 'phone_no', 'country', 'cvd_risk']
        
        # Map of original column names to their new display names
        column_display_names = {
            'record_id': 'Record ID',
            'id': 'User ID',
            'first_name': 'First Name',
            'last_name': 'Last Name',
            'email': 'Email',
            'phone_no': 'Mobile No',
            'country': 'Country',
            'cvd_risk': 'CVD Risk'
        }

        # Fetch all records for the users along with their latest CVD risk
        user_query = """
            SELECT u.id, u.first_name, u.last_name, u.email, u.phone_no, u.country,
                   r.record_id as record_id, r.cvd_risk as cvd_risk, r.age, r.gender, r.smoking_status, r.alcohol,
                   r.cholesterol_levels, r.diabetes, r.hypertension, r.stress, r.anxiety, r.depression,
                   r.obesity, r.physical_activity, r.diet
            FROM users_final u
            LEFT JOIN (
                SELECT DISTINCT ON (user_id) user_id, record_id, cvd_risk, age, gender, smoking_status, alcohol,
                       cholesterol_levels, diabetes, hypertension, stress, anxiety, depression,
                       obesity, physical_activity, diet
                FROM risk_records
                ORDER BY user_id, record_id DESC
            ) r ON u.id = r.user_id
        """
        cur.execute(user_query)
        user_data = cur.fetchall()
        user_columns = [desc[0] for desc in cur.description]

        # Count users with the latest CVD risk as 'Heart Disease'
        count_heart_disease_query = """
            SELECT COUNT(*)
            FROM (
                SELECT DISTINCT ON (user_id) user_id, cvd_risk
                FROM risk_records
                ORDER BY user_id, record_id DESC
            ) r
            WHERE r.cvd_risk = 'Heart Disease'
        """
        cur.execute(count_heart_disease_query)
        total_heart_disease_users = cur.fetchone()[0]

        # Count users with the latest CVD risk as 'No Heart Disease'
        count_no_heart_disease_query = """
            SELECT COUNT(*)
            FROM (
                SELECT DISTINCT ON (user_id) user_id, cvd_risk
                FROM risk_records
                ORDER BY user_id, record_id DESC
            ) r
            WHERE r.cvd_risk = 'No Heart Disease'
        """
        cur.execute(count_no_heart_disease_query)
        total_no_heart_disease_users = cur.fetchone()[0]
        
        # Fetch all risk records
        risk_query = "SELECT * FROM risk_records"
        cur.execute(risk_query)
        risk_data = cur.fetchall()
        risk_columns = [desc[0] for desc in cur.description]

        # Convert user_data and risk_data to dictionaries
        user_data = [dict(zip(user_columns, row)) for row in user_data]
        risk_data = [dict(zip(risk_columns, row)) for row in risk_data]

        # Define the risk columns and their display names
        risk_columns = ['age', 'gender', 'ap_hi', 'ap_lo', 'hypertension', 'bmi', 'diet', 'obesity',
                        'cholesterol_levels', 'smoking_status', 'alcohol', 'diabetes', 'physical_activity',
                        'stress', 'anxiety', 'depression']
        risk_column_display_names = {
            'age': 'Age',
            'gender': 'Gender',
            'ap_hi': 'Systolic BP',
            'ap_lo': 'Diastolic BP',
            'hypertension': 'Hypertension',
            'bmi': 'BMI',
            'obesity': 'Obesity',
            'cholesterol_levels': 'Cholesterol',
            'diabetes': 'Diabetes',
            'smoking_status': 'Smoking Status',
            'alcohol': 'Alcohol Consumption',
            'diet': 'Diet',
            'physical_activity': 'Physical Activity',
            'stress': 'Stress',
            'anxiety': 'Anxiety',
            'depression': 'Depression'
        }

      # Generate the visualizations for distinct users
        data = {
            'id': [user['id'] for user in user_data],
            'age': [user['age'] for user in user_data],
            'gender': [user['gender'] for user in user_data],
            'smoking_status': [user['smoking_status'] for user in user_data],
            'alcohol': [user['alcohol'] for user in user_data],
            'cholesterol_levels': [user['cholesterol_levels'] for user in user_data],
            'diabetes': [user['diabetes'] for user in user_data],
            'hypertension': [user['hypertension'] for user in user_data],
            'stress': [user['stress'] for user in user_data],
            'anxiety': [user['anxiety'] for user in user_data],
            'depression': [user['depression'] for user in user_data],
            'obesity': [user['obesity'] for user in user_data],
            'physical_activity': [user['physical_activity'] for user in user_data],
            'diet': [user['diet'] for user in user_data],
            'cvd_risk': [user['cvd_risk'] for user in user_data],
            'country': [user['country'] for user in user_data]
        }

        df = pd.DataFrame(data)
        
        # Age Distribution
        plt.figure(figsize=(10, 6))
        plt.hist(df['age'], bins=10, edgecolor='k')
        plt.title('Age Distribution')
        plt.xlabel('Age')
        plt.ylabel('Number of Users')
        age_img = BytesIO()
        plt.savefig(age_img, format='png')
        age_img.seek(0)
        age_img_b64 = base64.b64encode(age_img.getvalue()).decode('utf8')
        plt.close()

        # Gender Distribution
        gender_counts = df['gender'].value_counts()
        plt.figure(figsize=(10, 6))
        plt.pie(gender_counts, labels=gender_counts.index, autopct='%1.1f%%', startangle=140)
        plt.title('Gender Distribution')
        gender_img = BytesIO()
        plt.savefig(gender_img, format='png')
        gender_img.seek(0)
        gender_img_b64 = base64.b64encode(gender_img.getvalue()).decode('utf8')
        plt.close()

        # Smoking Status Distribution
        smoking_counts = df['smoking_status'].value_counts()
        plt.figure(figsize=(10, 6))
        plt.pie(smoking_counts, labels=smoking_counts.index, autopct='%1.1f%%', startangle=140)
        plt.title('Smoking Status Distribution')
        smoking_img = BytesIO()
        plt.savefig(smoking_img, format='png')
        smoking_img.seek(0)
        smoking_img_b64 = base64.b64encode(smoking_img.getvalue()).decode('utf8')
        plt.close()

        # Alcohol Consumption Distribution
        alcohol_counts = df['alcohol'].value_counts()
        plt.figure(figsize=(10, 6))
        plt.pie(alcohol_counts, labels=alcohol_counts.index, autopct='%1.1f%%', startangle=140)
        plt.title('Alcohol Consumption Distribution')
        alcohol_img = BytesIO()
        plt.savefig(alcohol_img, format='png')
        alcohol_img.seek(0)
        alcohol_img_b64 = base64.b64encode(alcohol_img.getvalue()).decode('utf8')
        plt.close()

        # Cholesterol Levels Distribution
        cholesterol_counts = df['cholesterol_levels'].value_counts()
        plt.figure(figsize=(10, 6))
        plt.bar(cholesterol_counts.index, cholesterol_counts.values)
        plt.title('Cholesterol Levels Distribution')
        plt.xlabel('Cholesterol Levels')
        plt.ylabel('Number of Users')
        cholesterol_img = BytesIO()
        plt.savefig(cholesterol_img, format='png')
        cholesterol_img.seek(0)
        cholesterol_img_b64 = base64.b64encode(cholesterol_img.getvalue()).decode('utf8')
        plt.close()

        # Diabetes Prevalence
        diabetes_counts = df['diabetes'].value_counts()
        plt.figure(figsize=(10, 6))
        plt.bar(diabetes_counts.index, diabetes_counts.values)
        plt.title('Diabetes Prevalence')
        plt.xlabel('Diabetes')
        plt.ylabel('Number of Users')
        diabetes_img = BytesIO()
        plt.savefig(diabetes_img, format='png')
        diabetes_img.seek(0)
        diabetes_img_b64 = base64.b64encode(diabetes_img.getvalue()).decode('utf8')
        plt.close()

        # Stress Levels
        stress_counts = df['stress'].value_counts()
        plt.figure(figsize=(10, 6))
        plt.pie(stress_counts, labels=stress_counts.index, autopct='%1.1f%%', startangle=140, wedgeprops=dict(width=0.3))
        plt.title('Stress Levels')
        stress_img = BytesIO()
        plt.savefig(stress_img, format='png')
        stress_img.seek(0)
        stress_img_b64 = base64.b64encode(stress_img.getvalue()).decode('utf8')
        plt.close()

        # Anxiety Levels
        anxiety_counts = df['anxiety'].value_counts()
        plt.figure(figsize=(10, 6))
        plt.pie(anxiety_counts, labels=anxiety_counts.index, autopct='%1.1f%%', startangle=140, wedgeprops=dict(width=0.3))
        plt.title('Anxiety Levels')
        anxiety_img = BytesIO()
        plt.savefig(anxiety_img, format='png')
        anxiety_img.seek(0)
        anxiety_img_b64 = base64.b64encode(anxiety_img.getvalue()).decode('utf8')
        plt.close()

        # Depression Levels
        depression_counts = df['depression'].value_counts()
        plt.figure(figsize=(10, 6))
        plt.pie(depression_counts, labels=depression_counts.index, autopct='%1.1f%%', startangle=140, wedgeprops=dict(width=0.3))
        plt.title('Depression Levels')
        depression_img = BytesIO()
        plt.savefig(depression_img, format='png')
        depression_img.seek(0)
        depression_img_b64 = base64.b64encode(depression_img.getvalue()).decode('utf8')
        plt.close()

        # Obesity Levels
        obesity_counts = df['obesity'].value_counts()
        plt.figure(figsize=(10, 6))
        plt.bar(obesity_counts.index, obesity_counts.values)
        plt.title('Obesity Levels')
        plt.xlabel('Obesity Levels')
        plt.ylabel('Number of Users')
        obesity_img = BytesIO()
        plt.savefig(obesity_img, format='png')
        obesity_img.seek(0)
        obesity_img_b64 = base64.b64encode(obesity_img.getvalue()).decode('utf8')
        plt.close()

        # Physical Activity Levels
        physical_activity_counts = df['physical_activity'].value_counts()
        plt.figure(figsize=(10, 6))
        plt.pie(physical_activity_counts, labels=physical_activity_counts.index, autopct='%1.1f%%', startangle=140)
        plt.title('Physical Activity Levels')
        physical_activity_img = BytesIO()
        plt.savefig(physical_activity_img, format='png')
        physical_activity_img.seek(0)
        physical_activity_img_b64 = base64.b64encode(physical_activity_img.getvalue()).decode('utf8')
        plt.close()

        # Diet Patterns
        diet_counts = df['diet'].value_counts()
        plt.figure(figsize=(10, 6))
        plt.pie(diet_counts, labels=diet_counts.index, autopct='%1.1f%%', startangle=140)
        plt.title('Diet Patterns')
        diet_img = BytesIO()
        plt.savefig(diet_img, format='png')
        diet_img.seek(0)
        diet_img_b64 = base64.b64encode(diet_img.getvalue()).decode('utf8')
        plt.close()

        # Unique Users with CVD Risk by Country
        country_cvd_risk_counts = df[df['cvd_risk'] == 'Heart Disease'].groupby('country')['id'].nunique()
        plt.figure(figsize=(10, 6))
        country_cvd_risk_counts.plot(kind='bar')
        plt.title('Unique Users with CVD Risk by Country')
        plt.xlabel('Country')
        plt.ylabel('Number of Unique Users with CVD Risk')
        country_cvd_risk_img = BytesIO()
        plt.savefig(country_cvd_risk_img, format='png')
        country_cvd_risk_img.seek(0)
        country_cvd_risk_img_b64 = base64.b64encode(country_cvd_risk_img.getvalue()).decode('utf8')
        plt.close()

        # Hollow Disc Chart for CVD counts by Gender
        gender_cvd_counts = df[df['cvd_risk'] == 'Heart Disease']['gender'].value_counts()
        plt.figure(figsize=(8, 8))
        plt.pie(gender_cvd_counts, labels=gender_cvd_counts.index, autopct='%1.1f%%', startangle=140, wedgeprops=dict(width=0.3))
        plt.title('CVD Counts by Gender')
        gender_cvd_img = BytesIO()
        plt.savefig(gender_cvd_img, format='png')
        gender_cvd_img.seek(0)
        gender_cvd_img_b64 = base64.b64encode(gender_cvd_img.getvalue()).decode('utf8')
        plt.close()


        cur.close()
        conn.close()

        return render_template('admin_dashboard.html', logged_in=logged_in, columns=columns, column_display_names=column_display_names, 
                               user_data=user_data, risk_data=risk_data, risk_columns=risk_columns, risk_column_display_names=risk_column_display_names, 
                               total_heart_disease_users=total_heart_disease_users, total_no_heart_disease_users=total_no_heart_disease_users, 
                               age_img_b64=age_img_b64, gender_img_b64=gender_img_b64, smoking_img_b64=smoking_img_b64, alcohol_img_b64=alcohol_img_b64, 
                               cholesterol_img_b64=cholesterol_img_b64, diabetes_img_b64=diabetes_img_b64, stress_img_b64=stress_img_b64, 
                               anxiety_img_b64=anxiety_img_b64, depression_img_b64=depression_img_b64, obesity_img_b64=obesity_img_b64, 
                               physical_activity_img_b64=physical_activity_img_b64, diet_img_b64=diet_img_b64,
                               country_cvd_risk_img_b64=country_cvd_risk_img_b64,gender_cvd_img_b64=gender_cvd_img_b64,username=session['username'])
    return redirect(url_for('login'))

# Function to preprocess the input data
def preprocess_input(input_data, label_encoders, scaler):
    # Create a DataFrame from the input data
    input_df = pd.DataFrame([input_data])
    
    # Encode categorical variables using the same LabelEncoder
    for column, le in label_encoders.items():
        if column in input_df:
            input_df[column] = le.transform(input_df[column].astype(str))
    
    # Scale the numerical features using the same StandardScaler
    input_scaled = scaler.transform(input_df)
    
    return input_scaled
@app.route('/predict', methods=['POST'])
def predict():
    logged_in = 'username' in session
    # Define mappings for categorical variables
    cholesterol_mapping = {'Normal': 1, 'Above Normal': 2, 'Well Above Normal': 3}
    diabetes_mapping = {'Normal': 1, 'Above Normal': 2, 'Well Above Normal': 3}
    smoking_mapping = {'No': 0, 'Yes': 1}
    alcohol_mapping = {'No': 0, 'Yes': 1}
    physical_activity_mapping = {'Not Active': 0, 'Active': 1}
    hypertension_mapping = {'No': 0, 'Yes': 1}

    # Collect and convert input data from the form
    input_data = {
        'age': int(request.form['age']),
        'gender': request.form['gender'],
        'height': float(request.form['height']),
        'weight': float(request.form['weight']),
        'ap_hi': int(request.form['ap_hi']),
        'ap_lo': int(request.form['ap_lo']),
        'cholesterol': cholesterol_mapping[request.form['cholesterol']],
        'diabetes': diabetes_mapping[request.form['diabetes']],
        'smoking': smoking_mapping[request.form['smoking']],
        'alcohol_consumption': alcohol_mapping[request.form['alcohol']],
        'physically_active': physical_activity_mapping[request.form['physical_activity']],
        'bmi': float(request.form['bmi']),
        'obesity': request.form['obesity'],
        'hypertension': hypertension_mapping[request.form['hypertension']],
        'stress': request.form['stress'],
        'anxiety': request.form['anxiety'],
        'depression': request.form['depression'],
        'diet': request.form['diet']
    }

    try:
        input_scaled = preprocess_input(input_data, label_encoders, scaler)
        prediction = best_model.predict(input_scaled)
        result = "Heart Disease" if prediction[0] == 1 else "No Heart Disease"

        user_id = session['user_id']
        conn = get_db_connection()
        cur = conn.cursor()

        # Check if there is an existing record for the user
        cur.execute('SELECT * FROM risk_records WHERE user_id = %s ORDER BY record_id DESC LIMIT 1', (user_id,))
        user_risk_data = cur.fetchone()

        if user_risk_data:
            record_id = user_risk_data[0]
            if (user_risk_data[14] is None or user_risk_data[14] == '') and \
               (user_risk_data[15] is None or user_risk_data[15] == ''):
                # Update the existing record
                cur.execute('''
                    UPDATE risk_records 
                    SET age = %s, gender = %s, smoking_status = %s, ap_hi = %s, ap_lo = %s, hypertension = %s, cholesterol_levels = %s, 
                        diabetes = %s, height = %s, weight = %s, bmi = %s, obesity = %s, physical_activity = %s, diet = %s, 
                        alcohol = %s, stress = %s, anxiety = %s, depression = %s, cvd_risk = %s
                    WHERE record_id = %s
                ''', (input_data['age'], input_data['gender'], input_data['smoking'], input_data['ap_hi'], input_data['ap_lo'], input_data['hypertension'], input_data['cholesterol'], input_data['diabetes'], input_data['height'], input_data['weight'], input_data['bmi'], input_data['obesity'], input_data['physically_active'], input_data['diet'], input_data['alcohol_consumption'], input_data['stress'], input_data['anxiety'], input_data['depression'], result, record_id))
            else:
                # Insert a new record
                cur.execute('''
                    INSERT INTO risk_records (user_id, age, gender, smoking_status, ap_hi, ap_lo, hypertension, cholesterol_levels, diabetes, height, weight, bmi, obesity, physical_activity, diet, alcohol, stress, anxiety, depression, cvd_risk) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (user_id, input_data['age'], input_data['gender'], input_data['smoking'], input_data['ap_hi'], input_data['ap_lo'], input_data['hypertension'], input_data['cholesterol'], input_data['diabetes'], input_data['height'], input_data['weight'], input_data['bmi'], input_data['obesity'], input_data['physically_active'], input_data['diet'], input_data['alcohol_consumption'], input_data['stress'], input_data['anxiety'], input_data['depression'], result))
        else:
            # Insert a new record since no existing record was found
            cur.execute('''
                INSERT INTO risk_records (user_id, age, gender, smoking_status, ap_hi, ap_lo, hypertension, cholesterol_levels, diabetes, height, weight, bmi, obesity, physical_activity, diet, alcohol, stress, anxiety, depression, cvd_risk) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (user_id, input_data['age'], input_data['gender'], input_data['smoking'], input_data['ap_hi'], input_data['ap_lo'], input_data['hypertension'], input_data['cholesterol'], input_data['diabetes'], input_data['height'], input_data['weight'], input_data['bmi'], input_data['obesity'], input_data['physically_active'], input_data['diet'], input_data['alcohol_consumption'], input_data['stress'], input_data['anxiety'], input_data['depression'], result))

        conn.commit()
        cur.close()
        conn.close()

       # Store the input data and result in session
        session['input_data'] = input_data
        session['prediction_result'] = result

        return redirect(url_for('risk_details'))
    except Exception as e:
        return render_template('risk-details.html', prediction=f"Error: {str(e)}", logged_in = logged_in)

@app.route('/chart/<chart_type>')
def chart(chart_type):
    input_data = session.get('input_data')
    if not input_data:
        return "No data available", 400

    if chart_type == 'main':
        buf = generate_main_chart(input_data)
    else:
        return "Invalid chart type", 400
    
    return send_file(buf, mimetype='image/png', as_attachment=False, download_name=f'{chart_type}_comparison.png')

def get_average_values(age, gender):
    averages = {
        'ap_hi': 120,
        'ap_lo': 80,
        'cholesterol': 'Normal',
        'diabetes': 'Normal',
        'bmi': 24.9,
        'physicaly_active': 'Active',
        'diet': 'Healthy',
        'alcohol_consumption': 'No',
        'obesity':'Normal',
        'stress': 'No',
        'anxiety': 'No',
        'depression': 'No',
        'smoking':'No'
    }

    if gender == 'Male':
        if 18 <= age <= 24:
            averages['bmi'] = 19.0
        elif 25 <= age <= 34:
            averages['bmi'] = 20.0
        elif 35 <= age <= 44:
            averages['bmi'] = 21.0
        elif 45 <= age <= 54:
            averages['bmi'] = 22.0
        elif 55 <= age <= 64:
            averages['bmi'] = 23.0
        else:
            averages['bmi'] = 24.0
    else:
        if 18 <= age <= 24:
            averages['bmi'] = 18.0
        elif 25 <= age <= 34:
            averages['bmi'] = 19.0
        elif 35 <= age <= 44:
            averages['bmi'] = 20.0
        elif 45 <= age <= 54:
            averages['bmi'] = 21.0
        elif 55 <= age <= 64:
            averages['bmi'] = 22.0
        else:
            averages['bmi'] = 23.0

    # Adjust blood pressure based on age and gender
    if gender == 'Male':
        if 18 <= age <= 39:
            averages['ap_hi'] = 119
            averages['ap_lo'] = 70
        elif 40 <= age <= 59:
            averages['ap_hi'] = 124
            averages['ap_lo'] = 77
        else:
            averages['ap_hi'] = 133
            averages['ap_lo'] = 69
    else:
        if 18 <= age <= 39:
            averages['ap_hi'] = 110
            averages['ap_lo'] = 68
        elif 40 <= age <= 59:
            averages['ap_hi'] = 122
            averages['ap_lo'] = 74
        else:
            averages['ap_hi'] = 139
            averages['ap_lo'] = 68

    return averages

def get_recommendation(factor, user_value, avg_value):
    if factor == 'ap_hi' and user_value > avg_value:
        return 'Reduce sodium intake, exercise regularly, and consult your healthcare provider.'
    if factor == 'ap_lo' and user_value > avg_value:
        return 'Monitor your diastolic pressure, reduce stress, and maintain a healthy diet.'
    if factor == 'cholesterol' and user_value != avg_value:
        return 'Adopt a low-cholesterol diet, increase physical activity, and consider medication if advised.'
    if factor == 'diabetes' and user_value != avg_value:
        return 'Manage your blood sugar through diet, exercise, and medication.'
    if factor == 'bmi' and user_value > avg_value:
        return 'Aim for a healthy weight through diet and exercise.'
    if factor == 'physicaly_active' and user_value != avg_value:
        return 'Increase your physical activity to at least 150 minutes of moderate exercise per week.'
    if factor == 'diet' and user_value != avg_value:
        return 'Follow a balanced diet rich in fruits, vegetables, and whole grains.'
    if factor == 'alcohol_consumption' and user_value != avg_value:
        return 'Limit your alcohol intake to moderate levels.'
    if factor == 'smoking' and user_value != avg_value:
        return 'Quitting is the best thing you can do for your heart health. Seek support from smoking cessation programs or your healthcare provider.'
    if factor in ['stress', 'anxiety', 'depression'] and user_value == 'Yes':
        return 'Consider stress management techniques, and seek professional help if necessary.'
    return 'Maintain current healthy habits.'

@app.route('/risk_details')
def risk_details():
    prediction_result = session.get('prediction_result', 'No data available')
    input_data = session.get('input_data', {})
    logged_in = 'username' in session

    age = input_data['age']
    gender=input_data['gender']

    # Define explanations and recommendations
    risk_factor_explanations = {
        'age': 'Age is a significant risk factor for heart disease. As you get older, your risk of heart disease increases.',
        'gender': 'Gender plays a role in heart disease risk. Men are generally at higher risk at a younger age, while women’s risk increases after menopause.',
        'smoking': 'Smoking damages the lining of your arteries, leading to a buildup of fatty material (atheroma) which narrows the artery. This can cause angina, heart attack or stroke.',
        'ap_hi': 'High systolic blood pressure (the first number in a blood pressure reading) indicates how much pressure your blood is exerting against your artery walls when the heart beats.',
        'ap_lo': 'High diastolic blood pressure (the second number in a blood pressure reading) indicates how much pressure your blood is exerting against your artery walls while the heart is resting between beats.',
        'cholesterol': 'High cholesterol can lead to a buildup of plaque in your arteries, which can increase the risk of heart disease and stroke.',
        'diabetes': 'Diabetes increases the risk of heart disease. High blood sugar from diabetes can damage your blood vessels and the nerves that control your heart.',
        'height': 'Height alone is not a direct risk factor for heart disease, but it can be related to other factors such as weight and BMI.',
        'weight': 'Being overweight increases the risk of heart disease. It is associated with high blood pressure, high cholesterol, and diabetes.',
        'bmi': 'Body Mass Index (BMI) is a measure of body fat based on height and weight. A high BMI can indicate high body fat, which can increase the risk of heart disease.',
        'obesity': 'Obesity is a major risk factor for heart disease. It can lead to high blood pressure, high cholesterol, and diabetes.',
        'physically_active': 'Physical activity helps improve heart health. Being physically active can lower your risk of heart disease.',
        'diet': 'A healthy diet can help protect your heart. Eating a diet that is high in fruits, vegetables, and whole grains can reduce the risk of heart disease.',
        'alcohol_consumption': 'Excessive alcohol consumption can increase blood pressure and the risk of heart disease. Moderate consumption is key.',
        'stress': 'Chronic stress can increase the risk of heart disease. Stress can raise blood pressure and lead to unhealthy coping mechanisms like smoking or overeating.',
        'anxiety': 'Anxiety can lead to increased heart rate and blood pressure, which over time can damage the heart.',
        'depression': 'Depression is linked to an increased risk of heart disease. It can lead to poor lifestyle choices and reduced adherence to heart-healthy behaviors.',
        'hypertension': 'Hypertension, or high blood pressure, increases the risk of heart disease and stroke. It can damage blood vessels and the heart over time, leading to serious health problems.'
    }

    # Define mappings for displaying user-friendly values
    display_mappings = {
        'smoking': {0: 'No', 1: 'Yes'},
        'physically_active': {0: 'Not Active', 1: 'Active'},
        'alcohol_consumption': {0: 'No', 1: 'Yes'},
        'stress': {0: 'No', 1: 'Yes'},
        'anxiety': {0: 'No', 1: 'Yes'},
        'depression': {0: 'No', 1: 'Yes'},
        'hypertension': {0: 'No', 1: 'Yes'},
        'cholesterol': {1: 'Normal', 2: 'Above Normal', 3: 'Well Above Normal'},
        'diabetes': {1: 'Normal', 2: 'Above Normal', 3: 'Well Above Normal'},
        'obesity': {1: 'Underweight', 2: 'Normal', 3: 'Overweight', 4: 'Obese'}
    }

    # Apply display mappings
    for key, mapping in display_mappings.items():
        if key in input_data:
            input_data[key] = mapping.get(input_data[key], input_data[key])

    factor_labels = {
    'alcohol_consumption': 'Alcohol Consumption',
    'anxiety': 'Anxiety',
    'ap_hi': 'Systolic BP (ap_hi)',
    'ap_lo': 'Diastolic BP (ap_lo)',
    'bmi': 'Body Mass Index (BMI)',
    'cholesterol': 'Cholesterol',
    'depression': 'Depression',
    'diabetes': 'Diabetes',
    'diet': 'Diet',
    'obesity': 'Obesity',
    'smoking': 'Smoking',
    'stress': 'Stress'
}

    # Determine average values based on age and gender
    average_values = get_average_values(age, gender)

    personalized_recommendations = []
    for factor, value in input_data.items():
        if factor in average_values:
            recommendation = get_recommendation(factor, value, average_values[factor])
            personalized_recommendations.append({
                'factor': factor_labels.get(factor, factor),
                'your_value': value,
                'average_value': average_values[factor],
                'recommendation': recommendation
            })

    return render_template('risk-details.html', prediction=prediction_result, risk_factors=input_data.items(), explanations=risk_factor_explanations, recommendations=personalized_recommendations,  logged_in=logged_in)

scheduler = BackgroundScheduler()
scheduler.add_job(func=clean_up_temp_users, trigger="interval", minutes=10)  # Run every 10 minutes
scheduler.start()

# Shut down the scheduler when exiting the app
atexit.register(lambda: scheduler.shutdown())

if __name__ == '__main__':
    app.run(debug=True)
