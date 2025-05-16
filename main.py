import os
import secrets
import base64
import hashlib
import requests
import json
from urllib.parse import urlencode
from flask import Flask, redirect, request, session
from dotenv import load_dotenv
from solana.keypair import Keypair
from solana.publickey import PublicKey
from solana.rpc.api import Client
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer

load_dotenv()
app = Flask(__name__)
app.secret_key = secrets.token_hex(16)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False  # Set to True for HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_PATH'] = '/'

# Twitter API credentials
CLIENT_ID = os.getenv("TWITTER_CLIENT_ID", "default_client_id")
CLIENT_SECRET = os.getenv("TWITTER_CLIENT_SECRET", "default_client_secret")
REDIRECT_URI = os.getenv("TWITTER_REDIRECT_URI", "http://127.0.0.1:5000/callback")
BOT_HANDLE = "@SolatranBot"

# Solana setup
SOLANA_CLIENT = Client("https://api.devnet.solana.com")
try:
    with open("C:/Users/USER/Documents/Solatran/keypair.json", "r") as f:
        secret_key = json.load(f)
    PAYER_KEYPAIR = Keypair.from_secret_key(bytes(secret_key))
except FileNotFoundError:
    print("Error: keypair.json not found")
    PAYER_KEYPAIR = None


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
        'redirect_uri': REDIRECT_URI,
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
        return "Error: Authorization code not provided", 400
    if not state or state != session.get('state'):
        return "Error: Invalid or missing state parameter", 400
    if session.get('used_code') == code:
        return "Error: Authorization code already used", 400

    code_verifier = session.get('code_verifier')
    if not code_verifier:
        return "Error: Missing code_verifier in session", 400

    token_url = 'https://api.twitter.com/2/oauth2/token'
    data = {
        'code': code,
        'grant_type': 'authorization_code',
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URI,
        'code_verifier': code_verifier,
    }

    auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    auth_b64 = base64.b64encode(auth_str).decode()
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': f"Basic {auth_b64}",
    }

    try:
        response = requests.post(token_url, data=data, headers=headers, timeout=10)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        session['access_token'] = access_token
        session['used_code'] = code
    except requests.RequestException as err:
        print(f"Token request failed: {str(err)}")
        return f"Error: Failed to connect to Twitter API - {str(err)}", 500

    # Fetch and process tweets
    tweets = get_tweets(access_token)
    results = process_tweets(tweets, access_token)

    session.clear()
    return f"""
    <h3>Access Token:</h3><code>{access_token}</code>
    <h3>Tweet Processing Results:</h3><pre>{results}</pre>
    """


def get_tweets(access_token):
    url = "https://api.twitter.com/2/tweets/search/recent"
    params = {
        'query': f"to:{BOT_HANDLE}",
        'max_results': 10,
        'tweet.fields': 'created_at,author_id',
    }
    headers = {'Authorization': f"Bearer {access_token}"}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get('data', [])
    except requests.RequestException as err:
        print(f"Error fetching tweets: {str(err)}")
        return [{"text": f"Error fetching tweets: {str(err)}"}]


def process_tweets(tweets, access_token):
    if not PAYER_KEYPAIR:
        return "Error: Solana keypair not loaded"
    results = []
    for tweet in tweets:
        tweet_text = tweet.get('text', '')
        if tweet_text.startswith('!send'):
            try:
                parts = tweet_text.split()
                if len(parts) != 3:
                    result = "Invalid format. Use: !send <amount> <address>"
                else:
                    _, amount, recipient = parts
                    amount = int(amount)
                    recipient_pubkey = PublicKey(recipient)
                    instruction = transfer(TransferParams(
                        from_pubkey=PAYER_KEYPAIR.public_key,
                        to_pubkey=recipient_pubkey,
                        lamports=amount
                    ))
                    transaction = Transaction().add(instruction)
                    response = SOLANA_CLIENT.send_transaction(transaction, PAYER_KEYPAIR)
                    signature = response.value  # Access signature from SendTransactionResp
                    result = f"Transaction sent: {signature}"
                    # Post reply
                    reply_url = "https://api.twitter.com/2/tweets"
                    reply_data = {
                        "text": f"@{tweet['author_id']} {result}",
                        "reply": {"in_reply_to_tweet_id": tweet['id']}
                    }
                    headers = {'Authorization': f"Bearer {access_token}"}
                    reply_response = requests.post(reply_url, json=reply_data, headers=headers)
                    if reply_response.status_code != 201:
                        result += f" (Failed to reply: {reply_response.text})"
                results.append(f"Tweet: {tweet_text} - {result}")
            except ValueError as err:
                results.append(f"Tweet: {tweet_text} - Error: Invalid amount or address - {str(err)}")
            except Exception as err:
                results.append(f"Tweet: {tweet_text} - Error: {str(err)}")
        else:
            results.append(f"Tweet: {tweet_text} - Ignored: Not a !send command")
    return "\n".join(results)


if __name__ == '__main__':
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)
