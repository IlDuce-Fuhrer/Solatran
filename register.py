"""
register.py — Solatran registration web app
Flow:
  1. User visits / → landing page
  2. Clicks "Connect Twitter" → Twitter OAuth
  3. Twitter redirects back to /callback
  4. We fetch their Twitter profile
  5. We create their account + generate wallets on all chains
  6. We show them their deposit addresses
"""

import os
import secrets
import base64
import hashlib
import requests
from urllib.parse import urlencode
from flask import Flask, redirect, request, session, jsonify, render_template_string
from dotenv import load_dotenv
from models import Session, User, Wallet, init_db
from wallets import generate_all_wallets
from flask_session import Session as FlaskSession

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_NAME'] = 'solatran_session'
app.config['SESSION_TYPE'] = 'filesystem'
FlaskSession(app)

CLIENT_ID    = os.getenv("TWITTER_CLIENT_ID")
CLIENT_SECRET = os.getenv("TWITTER_CLIENT_SECRET")
REDIRECT_URL  = os.getenv("TWITTER_REDIRECT_URL", "http://127.0.0.1:5000/callback")


# ─── HTML templates ───────────────────────────────────────────────────────────

HOME_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Solatran — Register</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #0a0a0a; color: #f0f0f0; min-height: 100vh;
           display: flex; align-items: center; justify-content: center; }
    .card { background: #111; border: 1px solid #222; border-radius: 16px;
            padding: 48px; max-width: 420px; width: 100%; text-align: center; }
    .logo { font-size: 2rem; font-weight: 800; color: #9945FF; margin-bottom: 8px; }
    .tagline { color: #888; margin-bottom: 40px; font-size: 0.95rem; }
    .btn { display: inline-block; background: #1d9bf0; color: white;
           padding: 14px 32px; border-radius: 999px; text-decoration: none;
           font-weight: 600; font-size: 1rem; transition: opacity 0.2s; }
    .btn:hover { opacity: 0.85; }
    .features { margin-top: 40px; text-align: left; }
    .feature { display: flex; gap: 12px; margin-bottom: 16px; color: #aaa; font-size: 0.9rem; }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">Solatran</div>
    <div class="tagline">Send crypto on Twitter. Instantly. Free.</div>
    <a href="/login" class="btn">Connect Twitter to Register</a>
    <div class="features">
      <div class="feature"><span>&#9889;</span><span>Instant transfers between users — no blockchain fees</span></div>
      <div class="feature"><span>&#127760;</span><span>SOL, ETH, USDT, USDC and more</span></div>
      <div class="feature"><span>&#128274;</span><span>Your keys encrypted with AES-256</span></div>
      <div class="feature"><span>&#128038;</span><span>Just tweet a command — that's it</span></div>
    </div>
  </div>
</body>
</html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Solatran — Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #0a0a0a; color: #f0f0f0; min-height: 100vh; padding: 32px 16px; }
    .container { max-width: 600px; margin: 0 auto; }
    .header { display: flex; align-items: center; gap: 16px; margin-bottom: 32px; }
    .logo { font-size: 1.5rem; font-weight: 800; color: #9945FF; }
    .handle { color: #888; }
    h2 { font-size: 1rem; color: #888; margin-bottom: 16px; text-transform: uppercase;
         letter-spacing: 0.05em; }
    .card { background: #111; border: 1px solid #222; border-radius: 12px;
            padding: 24px; margin-bottom: 24px; }
    .wallet { margin-bottom: 20px; }
    .wallet:last-child { margin-bottom: 0; }
    .chain { font-weight: 600; color: #9945FF; margin-bottom: 6px; font-size: 0.85rem;
             text-transform: uppercase; letter-spacing: 0.05em; }
    .address { font-family: monospace; font-size: 0.8rem; color: #aaa;
               background: #0a0a0a; padding: 10px 14px; border-radius: 8px;
               word-break: break-all; cursor: pointer; border: 1px solid #222;
               transition: border-color 0.2s; }
    .address:hover { border-color: #9945FF; }
    .copy-hint { font-size: 0.75rem; color: #555; margin-top: 4px; }
    .command { background: #0a0a0a; border: 1px solid #222; border-radius: 8px;
               padding: 12px 16px; font-family: monospace; font-size: 0.85rem;
               color: #9945FF; margin-bottom: 10px; }
    .badge-new { display: inline-block; background: #1e3a5f; color: #60a5fa;
                 padding: 4px 12px; border-radius: 999px; font-size: 0.8rem;
                 margin-bottom: 24px; }
    .badge-ok { display: inline-block; background: #14532d; color: #4ade80;
                padding: 4px 12px; border-radius: 999px; font-size: 0.8rem;
                margin-bottom: 24px; }
  </style>
  <script>
    function copyAddress(el) {
      navigator.clipboard.writeText(el.innerText);
      el.style.borderColor = '#4ade80';
      setTimeout(() => el.style.borderColor = '#222', 1500);
    }
  </script>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="logo">Solatran</div>
      <div class="handle">@{{ handle }}</div>
    </div>

    {% if is_new %}
    <div class="badge-new">Welcome! Your account has been created.</div>
    {% else %}
    <div class="badge-ok">Already registered</div>
    {% endif %}

    <h2>Your deposit addresses</h2>
    <div class="card">
      {% for wallet in wallets %}
      <div class="wallet">
        <div class="chain">{{ wallet.chain }}</div>
        <div class="address" onclick="copyAddress(this)" title="Click to copy">{{ wallet.address }}</div>
        <div class="copy-hint">Click to copy</div>
      </div>
      {% endfor %}
    </div>

    <h2>How to use Solatran</h2>
    <div class="card">
      <div class="command">@Solatran send 10 USDT to @friend</div>
      <div class="command">@Solatran balance</div>
      <div class="command">@Solatran deposit ETH</div>
      <div class="command">@Solatran withdraw 0.1 ETH 0xYourAddress</div>
    </div>
  </div>
</body>
</html>
"""

ERROR_HTML = """
<!DOCTYPE html>
<html>
<head><title>Solatran — Error</title>
<style>
  body { font-family: sans-serif; background: #0a0a0a; color: #f0f0f0;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .card { background: #111; border: 1px solid #222; border-radius: 12px;
          padding: 40px; max-width: 400px; text-align: center; }
  h2 { color: #f87171; margin-bottom: 16px; }
  p { color: #888; margin-bottom: 24px; }
  a { color: #9945FF; }
</style>
</head>
<body>
  <div class="card">
    <h2>Something went wrong</h2>
    <p>{{ message }}</p>
    <a href="/">Try again</a>
  </div>
</body>
</html>
"""


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def home():
    return render_template_string(HOME_HTML)


@app.route('/login')
def login():
    session.clear()
    code_verifier = secrets.token_urlsafe(96)[:128]
    state = secrets.token_hex(8)
    session['code_verifier'] = code_verifier
    session['state'] = state
    session.modified = True

    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")

    params = {
        'response_type': 'code',
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URL,
        'scope': 'tweet.read tweet.write users.read offline.access',
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
    }
    url = f"https://twitter.com/i/oauth2/authorize?{urlencode(params)}"
    return redirect(url)


@app.route('/callback')
def callback():
    code  = request.args.get('code')
    state = request.args.get('state')

    if not code:
        return render_template_string(ERROR_HTML,
            message="No authorization code received from Twitter."), 400
    if not state or state != session.get('state'):
        return render_template_string(ERROR_HTML,
            message="Invalid state parameter. Please try again."), 400

    code_verifier = session.get('code_verifier')
    if not code_verifier:
        return render_template_string(ERROR_HTML,
            message="Session expired. Please try again."), 400

    # Exchange code for access token
    auth_b64 = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    try:
        r = requests.post(
            'https://api.twitter.com/2/oauth2/token',
            data={
                'code': code,
                'grant_type': 'authorization_code',
                'client_id': CLIENT_ID,
                'redirect_uri': REDIRECT_URL,
                'code_verifier': code_verifier,
            },
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Authorization': f"Basic {auth_b64}",
            },
            timeout=10
        )
        r.raise_for_status()
        access_token = r.json().get('access_token')
    except Exception as e:
        return render_template_string(ERROR_HTML,
            message=f"Failed to get access token: {e}"), 500

    # Fetch Twitter profile
    try:
        profile_r = requests.get(
            "https://api.twitter.com/2/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10
        )
        profile_r.raise_for_status()
        profile        = profile_r.json().get('data', {})
        twitter_id     = profile.get('id')
        twitter_handle = profile.get('username')
    except Exception as e:
        return render_template_string(ERROR_HTML,
            message=f"Failed to fetch Twitter profile: {e}"), 500

    if not twitter_id or not twitter_handle:
        return render_template_string(ERROR_HTML,
            message="Could not read your Twitter profile."), 500

    # Register or fetch user
    is_new = False
    with Session() as db:
        user = db.query(User).filter_by(twitter_id=twitter_id).first()

        if not user:
            is_new = True
            user = User(twitter_id=twitter_id, twitter_handle=twitter_handle)
            db.add(user)
            db.flush()   # get user.id without committing

            # Generate wallets for all chains
            print(f"Generating wallets for @{twitter_handle}...")
            generated = generate_all_wallets()
            for w in generated:
                db.add(Wallet(
                    user_id=user.id,
                    chain=w['chain'],
                    address=w['address'],
                    encrypted_key=w['encrypted_key'],
                ))
            db.commit()
            print(f"Registered @{twitter_handle}")
        else:
            # Update handle if they changed their Twitter username
            if user.twitter_handle != twitter_handle:
                user.twitter_handle = twitter_handle
                db.commit()

        wallets = db.query(Wallet).filter_by(user_id=user.id).all()
        wallet_list = [{"chain": w.chain, "address": w.address} for w in wallets]

    return render_template_string(
        DASHBOARD_HTML,
        handle=twitter_handle,
        wallets=wallet_list,
        is_new=is_new,
    )


# ─── Internal API (used by the Twitter bot) ───────────────────────────────────

@app.route('/api/user/<twitter_handle>')
def get_user(twitter_handle):
    """Check if a user is registered and return their wallets."""
    with Session() as db:
        user = db.query(User).filter_by(
            twitter_handle=twitter_handle.lstrip('@')
        ).first()
        if not user:
            return jsonify({"registered": False}), 404
        wallets = db.query(Wallet).filter_by(user_id=user.id).all()
        return jsonify({
            "registered": True,
            "twitter_handle": user.twitter_handle,
            "wallets": [{"chain": w.chain, "address": w.address} for w in wallets],
        })


@app.route('/api/health')
def health():
    return jsonify({"status": "ok"})


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    app.run(
        debug=os.getenv("FLASK_ENV") == "development",
        use_reloader=False,
        host='0.0.0.0',
        port=5000
    )
