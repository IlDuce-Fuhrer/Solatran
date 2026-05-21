"""
main.py — Solatran Twitter Bot
Polls Twitter mentions every 60 seconds and processes commands:
  @Solatran send 10 USDT to @someone
  @Solatran balance
  @Solatran deposit ETH
  @Solatran withdraw 0.1 ETH 0xABC...
"""

import os
import re
import time
import logging
import requests
from dotenv import load_dotenv
from models import Session, User, Wallet, Transaction
from transfer import internal_transfer, withdraw, get_all_balances, to_smallest_unit

load_dotenv()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# Config
BOT_HANDLE    = os.getenv("TWITTER_BOT_HANDLE", "Solatran")
CLIENT_ID     = os.getenv("TWITTER_CLIENT_ID")
CLIENT_SECRET = os.getenv("TWITTER_CLIENT_SECRET")
POLL_INTERVAL = 60   # seconds between each mention check
REGISTER_URL  = "https://solatran.com"   # update when deployed

# Command patterns 
# @Solatran send 10 USDT to @friend
SEND_PATTERN = re.compile(
    r'send\s+([\d.]+)\s+([A-Z]+)\s+to\s+@(\w+)',
    re.IGNORECASE
)
# @Solatran send 10 USDT to @friend on ethereum  (optional chain)
SEND_WITH_CHAIN_PATTERN = re.compile(
    r'send\s+([\d.]+)\s+([A-Z]+)\s+to\s+@(\w+)\s+on\s+(\w+)',
    re.IGNORECASE
)
# @Solatran withdraw 0.1 ETH 0xABC...
WITHDRAW_PATTERN = re.compile(
    r'withdraw\s+([\d.]+)\s+([A-Z]+)\s+([A-Za-z0-9]{20,})',
    re.IGNORECASE
)
# @Solatran deposit ETH
DEPOSIT_PATTERN = re.compile(
    r'deposit(?:\s+([A-Z]+))?',
    re.IGNORECASE
)
# @Solatran balance
BALANCE_PATTERN = re.compile(r'balance', re.IGNORECASE)


# Twitter API helpers

def get_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def get_access_token() -> str | None:
    """
    Get a bot-level access token using OAuth 2.0 client credentials.
    This is used for reading mentions. For posting replies, we need
    the user access token obtained via the OAuth flow in register.py.
    """
    import base64
    url = "https://api.twitter.com/oauth2/token"
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    try:
        r = requests.post(url,
            data={"grant_type": "client_credentials"},
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            timeout=10
        )
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:
        log.error(f"Failed to get access token: {e}")
        return None


def get_mentions(access_token: str, since_id: str = None) -> list:
    """Fetch recent mentions of the bot account."""
    url = "https://api.twitter.com/2/tweets/search/recent"
    params = {
        "query": f"@{BOT_HANDLE} -is:retweet",
        "max_results": 10,
        "tweet.fields": "created_at,author_id,text",
        "expansions": "author_id",
        "user.fields": "username",
    }
    if since_id:
        params["since_id"] = since_id

    try:
        r = requests.get(url, params=params,
                         headers=get_headers(access_token), timeout=10)
        r.raise_for_status()
        data = r.json()

        tweets = data.get("data", [])

        # Build a map of author_id → username from the includes
        users = {
            u["id"]: u["username"]
            for u in data.get("includes", {}).get("users", [])
        }
        # Attach username to each tweet
        for tweet in tweets:
            tweet["author_username"] = users.get(tweet["author_id"], tweet["author_id"])

        return tweets
    except Exception as e:
        log.error(f"Failed to fetch mentions: {e}")
        return []


def post_reply(text: str, in_reply_to_id: str, access_token: str):
    """Post a reply tweet."""
    url = "https://api.twitter.com/2/tweets"
    try:
        r = requests.post(url,
            json={
                "text": text[:280],   # enforce Twitter character limit
                "reply": {"in_reply_to_tweet_id": in_reply_to_id}
            },
            headers={**get_headers(access_token), "Content-Type": "application/json"},
            timeout=10
        )
        r.raise_for_status()
        log.info(f"Replied to tweet {in_reply_to_id}: {text[:60]}...")
    except Exception as e:
        log.error(f"Failed to post reply to {in_reply_to_id}: {e}")


def send_dm(user_id: str, text: str, access_token: str):
    """Send a direct message to a user (for balance and deposit info)."""
    url = "https://api.twitter.com/2/dm_conversations/with/:participant_id/messages"
    url = f"https://api.twitter.com/2/dm_conversations/with/{user_id}/messages"
    try:
        r = requests.post(url,
            json={"text": text},
            headers={**get_headers(access_token), "Content-Type": "application/json"},
            timeout=10
        )
        r.raise_for_status()
        log.info(f"DM sent to user {user_id}")
    except Exception as e:
        log.error(f"Failed to send DM to {user_id}: {e}")


def is_registered(twitter_handle: str) -> bool:
    """Check if a user is registered in the database."""
    with Session() as db:
        user = db.query(User).filter_by(
            twitter_handle=twitter_handle.lstrip("@")
        ).first()
        return user is not None


# Command handlers

def handle_send(tweet: dict, access_token: str):
    """Handle: @Solatran send 10 USDT to @friend [on ethereum]"""
    text        = tweet["text"]
    tweet_id    = tweet["id"]
    sender      = tweet["author_username"]
    author_id   = tweet["author_id"]

    # Check for optional chain specifier first
    chain = None
    match = SEND_WITH_CHAIN_PATTERN.search(text)
    if match:
        amount_str, token, recipient, chain = match.groups()
        chain = chain.lower()
    else:
        match = SEND_PATTERN.search(text)
        if not match:
            return
        amount_str, token, recipient = match.groups()

    try:
        amount = float(amount_str)
    except ValueError:
        post_reply(f"@{sender} ❌ Invalid amount.", tweet_id, access_token)
        return

    if amount <= 0:
        post_reply(f"@{sender} ❌ Amount must be greater than zero.", tweet_id, access_token)
        return

    # Check sender is registered
    if not is_registered(sender):
        post_reply(
            f"@{sender} ❌ You're not registered. Sign up at {REGISTER_URL} to use Solatran.",
            tweet_id, access_token
        )
        return

    # Check recipient is registered
    if not is_registered(recipient):
        post_reply(
            f"@{sender} ❌ @{recipient} isn't registered on Solatran yet. "
            f"They can sign up at {REGISTER_URL}",
            tweet_id, access_token
        )
        return

    result = internal_transfer(
        sender_handle=sender,
        recipient_handle=recipient,
        token=token,
        amount_human=amount,
        tweet_id=tweet_id,
        chain=chain,
    )

    post_reply(result["message"], tweet_id, access_token)
    log.info(f"Transfer: @{sender} → @{recipient} {amount} {token} | {result['success']}")


def handle_balance(tweet: dict, access_token: str):
    """Handle: @Solatran balance"""
    tweet_id  = tweet["id"]
    sender    = tweet["author_username"]
    author_id = tweet["author_id"]

    if not is_registered(sender):
        post_reply(
            f"@{sender} ❌ You're not registered. Sign up at {REGISTER_URL}",
            tweet_id, access_token
        )
        return

    with Session() as db:
        user = db.query(User).filter_by(twitter_handle=sender).first()
        balances = get_all_balances(user.id)

    if not balances:
        msg = f"@{sender} Your Solatran balance is empty. Deposit funds at {REGISTER_URL}"
        post_reply(msg, tweet_id, access_token)
        return

    # Format balance list
    lines = ["Your Solatran balances:"]
    for b in balances:
        lines.append(f"  {b['amount']} {b['token']} ({b['chain']})")

    # Send as DM for privacy — balance is sensitive info
    send_dm(author_id, "\n".join(lines), access_token)
    post_reply(
        f"@{sender} 📬 I've sent your balance details via DM.",
        tweet_id, access_token
    )


def handle_deposit(tweet: dict, access_token: str):
    """Handle: @Solatran deposit ETH"""
    text      = tweet["text"]
    tweet_id  = tweet["id"]
    sender    = tweet["author_username"]
    author_id = tweet["author_id"]

    match = DEPOSIT_PATTERN.search(text)
    token = match.group(1).upper() if match and match.group(1) else None

    if not is_registered(sender):
        post_reply(
            f"@{sender} ❌ You're not registered. Sign up at {REGISTER_URL}",
            tweet_id, access_token
        )
        return

    with Session() as db:
        user = db.query(User).filter_by(twitter_handle=sender).first()

        if token:
            # Map token to its chain
            chain_map = {
                "SOL": "solana", "ETH": "ethereum", "USDT": "tron",
                "USDC": "ethereum", "BNB": "ethereum",
            }
            chain = chain_map.get(token)
            if not chain:
                post_reply(f"@{sender} ❌ {token} is not supported.", tweet_id, access_token)
                return
            wallet = db.query(Wallet).filter_by(user_id=user.id, chain=chain).first()
            if not wallet:
                post_reply(f"@{sender} ❌ No {chain} wallet found.", tweet_id, access_token)
                return
            msg = f"Your {token} deposit address ({chain}):\n{wallet.address}"
        else:
            # Show all deposit addresses
            wallets = db.query(Wallet).filter_by(user_id=user.id).all()
            lines = ["Your Solatran deposit addresses:"]
            for w in wallets:
                lines.append(f"\n{w.chain.upper()}:\n{w.address}")
            msg = "\n".join(lines)

    # Send as DM — address is sensitive
    send_dm(author_id, msg, access_token)
    post_reply(
        f"@{sender} 📬 I've sent your deposit address via DM.",
        tweet_id, access_token
    )


def handle_withdraw(tweet: dict, access_token: str):
    """Handle: @Solatran withdraw 0.1 ETH 0xABC..."""
    text     = tweet["text"]
    tweet_id = tweet["id"]
    sender   = tweet["author_username"]

    match = WITHDRAW_PATTERN.search(text)
    if not match:
        post_reply(
            f"@{sender} ❌ Invalid format. Use: @{BOT_HANDLE} withdraw [amount] [token] [address]",
            tweet_id, access_token
        )
        return

    amount_str, token, to_address = match.groups()

    try:
        amount = float(amount_str)
    except ValueError:
        post_reply(f"@{sender} ❌ Invalid amount.", tweet_id, access_token)
        return

    if not is_registered(sender):
        post_reply(
            f"@{sender} ❌ You're not registered. Sign up at {REGISTER_URL}",
            tweet_id, access_token
        )
        return

    post_reply(
        f"@{sender} ⏳ Processing your withdrawal of {amount} {token}...",
        tweet_id, access_token
    )

    result = withdraw(
        sender_handle=sender,
        token=token,
        amount_human=amount,
        to_address=to_address,
    )

    post_reply(result["message"], tweet_id, access_token)
    log.info(f"Withdrawal: @{sender} {amount} {token} → {to_address} | {result['success']}")


def handle_help(tweet: dict, access_token: str):
    """Handle unrecognized commands with a help message."""
    tweet_id = tweet["id"]
    sender   = tweet["author_username"]
    post_reply(
        f"@{sender} Here's how to use Solatran:\n"
        f"• send [amount] [token] to @user\n"
        f"• balance\n"
        f"• deposit [token]\n"
        f"• withdraw [amount] [token] [address]\n"
        f"Register at {REGISTER_URL}",
        tweet_id, access_token
    )


# Tweet router

def route_tweet(tweet: dict, access_token: str):
    """Decide which handler to call based on tweet content."""
    text = tweet["text"].lower()

    # Strip the bot mention before matching
    clean = re.sub(rf'@{BOT_HANDLE}', '', text, flags=re.IGNORECASE).strip()

    if SEND_PATTERN.search(clean) or SEND_WITH_CHAIN_PATTERN.search(clean):
        handle_send(tweet, access_token)
    elif BALANCE_PATTERN.search(clean):
        handle_balance(tweet, access_token)
    elif DEPOSIT_PATTERN.search(clean):
        handle_deposit(tweet, access_token)
    elif WITHDRAW_PATTERN.search(clean):
        handle_withdraw(tweet, access_token)
    else:
        handle_help(tweet, access_token)


# Main polling loop

def run_bot():
    log.info(f"🚀 Solatran bot starting — polling every {POLL_INTERVAL}s")
    access_token = get_access_token()
    if not access_token:
        log.error("❌ Could not get access token. Check TWITTER_CLIENT_ID and SECRET in .env")
        return

    since_id = None
    processed = set()   # in-memory dedup (DB handles persistent dedup)

    while True:
        try:
            log.info("Checking mentions...")
            tweets = get_mentions(access_token, since_id)

            if tweets:
                # Update since_id to the newest tweet so we don't re-fetch old ones
                since_id = tweets[0]["id"]

                for tweet in reversed(tweets):   # process oldest first
                    tweet_id = tweet["id"]
                    if tweet_id in processed:
                        continue
                    processed.add(tweet_id)

                    sender = tweet.get("author_username", "unknown")
                    log.info(f"Processing tweet {tweet_id} from @{sender}: {tweet['text'][:60]}")
                    route_tweet(tweet, access_token)

            else:
                log.info("No new mentions.")

        except Exception as e:
            log.error(f"Unexpected error in polling loop: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_bot()