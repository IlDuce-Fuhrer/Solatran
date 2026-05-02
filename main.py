import os
import re
import time
import secrets
import base64
import hashlib
import requests
import json
from urllib.parse import urlencode
from flask import Flask, redirect, request, session
from dotenv import load_dotenv
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.hash import Hash
from solana.rpc.api import Client
from solana.transaction import Transaction
from solders.system_program import TransferParams, transfer

load_dotenv()
app = Flask(__name__)
app.secret_key = secrets.token_hex(16)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True

CLIENT_ID = os.getenv("TWITTER_CLIENT_ID")
CLIENT_SECRET = os.getenv("TWITTER_CLIENT_SECRET")
REDIRECT_URL = os.getenv("TWITTER_REDIRECT_URL", "http://127.0.0.1:5000/callback")
BOT_HANDLE = "SolatranBot"  # no @ for API queries

MAX_SOL = 1.0  # safety cap per transaction
MAX_LAMPORTS = int(MAX_SOL * 1_000_000_000)

# --- FIX 1: Relative keypair path ---
KEYPAIR_PATH = os.getenv("KEYPAIR_PATH", "keypair.json")
SOLANA_CLIENT = Client("https://api.devnet.solana.com")

try:
    with open(KEYPAIR_PATH, "r") as f:
        secret_key = json.load(f)
    PAYER_KEYPAIR = Keypair.from_bytes(bytes(secret_key))
    print(f"Loaded keypair: {PAYER_KEYPAIR.pubkey()}")
except FileNotFoundError:
    print(f"Error: keypair not found at {KEYPAIR_PATH}")
    PAYER_KEYPAIR = None

# Track processed tweet IDs to avoid double-sending
processed_tweets = set()


# --- FIX 2: Fetch username from author_id ---
def get_username(author_id, access_token):
    url = f"https://api.twitter.com/2/users/{author_id}"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json().get("data", {}).get("username", author_id)
    except Exception:
        return author_id


# --- FIX 3: Add blockhash to transaction ---
def send_sol(recipient_pubkey: Pubkey, lamports: int):
    blockhash_resp = SOLANA_CLIENT.get_latest_blockhash()
    recent_blockhash = blockhash_resp.value.blockhash

    instruction = transfer(TransferParams(
        from_pubkey=PAYER_KEYPAIR.pubkey(),
        to_pubkey=recipient_pubkey,
        lamports=lamports
    ))
    transaction = Transaction()
    transaction.recent_blockhash = recent_blockhash
    transaction.add(instruction)

    response = SOLANA_CLIENT.send_transaction(transaction, PAYER_KEYPAIR)
    return str(response.value)


# --- FIX 4: Proper command parsing with regex ---
SEND_PATTERN = re.compile(
    r'!send\s+([\d.]+)\s+([1-9A-HJ-NP-Za-km-z]{32,44})',
    re.IGNORECASE
)


def process_tweets(tweets, access_token):
    if not PAYER_KEYPAIR:
        return ["Error: keypair not loaded"]

    results = []
    for tweet in tweets:
        tweet_id = tweet.get("id")
        tweet_text = tweet.get("text", "")
        author_id = tweet.get("author_id")

        if tweet_id in processed_tweets:
            continue
        processed_tweets.add(tweet_id)

        match = SEND_PATTERN.search(tweet_text)
        if not match:
            results.append(f"Ignored: {tweet_text[:60]}")
            continue

        amount_sol = float(match.group(1))
        recipient_str = match.group(2)
        lamports = int(amount_sol * 1_000_000_000)

        # FIX 5: Enforce max limit
        if lamports > MAX_LAMPORTS:
            reply_text = f"@{get_username(author_id, access_token)} ❌ Max is {MAX_SOL} SOL per transaction."
            post_reply(reply_text, tweet_id, access_token)
            results.append(f"Rejected oversized tx: {amount_sol} SOL")
            continue

        try:
            recipient_pubkey = Pubkey.from_string(recipient_str)
            signature = send_sol(recipient_pubkey, lamports)
            username = get_username(author_id, access_token)
            reply_text = f"@{username} ✅ Sent {amount_sol} SOL! Tx: {signature}"
            post_reply(reply_text, tweet_id, access_token)
            results.append(f"Sent {amount_sol} SOL → {signature}")

        except ValueError as e:
            reply_text = f"@{get_username(author_id, access_token)} ❌ Invalid address."
            post_reply(reply_text, tweet_id, access_token)
            results.append(f"Error (address): {e}")
        except Exception as e:
            results.append(f"Error (tx): {e}")

    return results


def post_reply(text, in_reply_to_id, access_token):
    url = "https://api.twitter.com/2/tweets"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "reply": {"in_reply_to_tweet_id": in_reply_to_id}
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"Failed to reply: {e}")


def get_tweets(access_token, since_id=None):
    url = "https://api.twitter.com/2/tweets/search/recent"
    params = {
        "query": f"to:@{BOT_HANDLE} !send",
        "max_results": 10,
        "tweet.fields": "created_at,author_id",
    }
    if since_id:
        params["since_id"] = since_id
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        print(f"Error fetching tweets: {e}")
        return []


# --- OAuth routes (keep as-is for token acquisition) ---
@app.route('/')
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
        'redirect_url': REDIRECT_URL,
        'scope': 'tweet.read tweet.write users.read offline.access',
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
    }
    url = f"https://twitter.com/i/oauth2/authorize?{urlencode(params)}"
    return redirect(url)


@app.route('/callback')
def callback():
    code = request.args.get('code')
    state = request.args.get('state')
    if not code:
        return "Error: no code", 400
    if not state or state != session.get('state'):
        return "Error: invalid state", 400

    code_verifier = session.get('code_verifier')
    token_url = 'https://api.twitter.com/2/oauth2/token'
    data = {
        'code': code,
        'grant_type': 'authorization_code',
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URL,
        'code_verifier': code_verifier,
    }
    auth_b64 = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': f"Basic {auth_b64}",
    }
    r = requests.post(token_url, data=data, headers=headers, timeout=10)
    r.raise_for_status()
    access_token = r.json().get('access_token')
    session['access_token'] = access_token

    tweets = get_tweets(access_token)
    results = process_tweets(tweets, access_token)
    return f"<pre>{'<br>'.join(results)}</pre>"


# --- FIX 6: Polling loop route for continuous operation ---
@app.route('/run-bot')
def run_bot():
    access_token = session.get('access_token')
    if not access_token:
        return redirect('/')
    since_id = None
    cycles = 0
    while cycles < 5:  # limit for web context; use a background thread/worker in prod
        tweets = get_tweets(access_token, since_id)
        if tweets:
            since_id = tweets[0]['id']
            process_tweets(tweets, access_token)
        time.sleep(60)
        cycles += 1
    return "Bot cycle complete"


if __name__ == '__main__':
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)