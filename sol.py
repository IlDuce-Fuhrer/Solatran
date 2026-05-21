"""
sol.py — Solatran Fetch.ai Agent
Acts as intelligent middleware between the Twitter bot and transfer engine.
Handles: validation, fraud detection, spending limits, confirmations.
Also exposes Solatran as an agent on the ASI:One / Agentverse network.

Commands accepted via chat:
  send 10 USDT to @friend
  balance
  deposit ETH
  withdraw 0.1 ETH 0xABC...
  history
  limits
"""

import os
import re
from datetime import datetime, timedelta
from uuid import uuid4
from dotenv import load_dotenv
from uagents import Agent, Protocol, Context
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)

from models import Session, User, Transaction
from transfer import (
    internal_transfer,
    withdraw,
    get_all_balances,
    from_smallest_unit,
)

load_dotenv()

# Agent setup
agent = Agent(
    name="solatran",
    seed=os.getenv("AGENT_SEED", "solatran-default-seed-change-this"),
    port=8000,
    endpoint=["http://127.0.0.1:8000/submit"],
)

chat_proto = Protocol(spec=chat_protocol_spec)

REGISTER_URL = "https://solatran.xyz"

# Spending limits (per user per day) 
DAILY_LIMITS = {
    "SOL":  100.0,
    "ETH":  1.0,
    "USDT": 500.0,
    "USDC": 500.0,
    "BNB":  5.0,
}

# Command patterns (same as main.py)
SEND_PATTERN         = re.compile(r'send\s+([\d.]+)\s+([A-Z]+)\s+to\s+@(\w+)', re.IGNORECASE)
SEND_WITH_CHAIN      = re.compile(r'send\s+([\d.]+)\s+([A-Z]+)\s+to\s+@(\w+)\s+on\s+(\w+)', re.IGNORECASE)
WITHDRAW_PATTERN     = re.compile(r'withdraw\s+([\d.]+)\s+([A-Z]+)\s+([A-Za-z0-9]{20,})', re.IGNORECASE)
DEPOSIT_PATTERN      = re.compile(r'deposit(?:\s+([A-Z]+))?', re.IGNORECASE)
BALANCE_PATTERN      = re.compile(r'balance', re.IGNORECASE)
HISTORY_PATTERN      = re.compile(r'history', re.IGNORECASE)
LIMITS_PATTERN       = re.compile(r'limits?', re.IGNORECASE)


# Fraud / limit checks

def get_daily_spent(user_id: int, token: str) -> float:
    """Return how much of a token the user has sent in the last 24 hours."""
    since = datetime.utcnow() - timedelta(hours=24)
    with Session() as db:
        txs = db.query(Transaction).filter(
            Transaction.sender_id == user_id,
            Transaction.token == token.upper(),
            Transaction.status == "success",
            Transaction.created_at >= since,
            Transaction.tx_type.in_(["transfer", "withdrawal"])
        ).all()
        total = sum(int(tx.amount) for tx in txs)

    # Convert from smallest unit to human readable
    return float(from_smallest_unit(total, token))


def check_daily_limit(user_id: int, token: str, amount: float) -> tuple[bool, str]:
    """
    Check if a transfer would exceed the daily limit.
    Returns (allowed: bool, message: str)
    """
    limit = DAILY_LIMITS.get(token.upper())
    if not limit:
        return False, f"❌ {token} is not supported."

    spent = get_daily_spent(user_id, token)
    remaining = limit - spent

    if amount > remaining:
        return False, (
            f"❌ Daily limit exceeded. You've sent {spent:.4f} {token} today. "
            f"Remaining: {remaining:.4f} {token}. Limit resets in 24h."
        )
    return True, ""


def is_registered(handle: str) -> tuple[bool, User | None]:
    """Check registration and return the user object."""
    with Session() as db:
        user = db.query(User).filter_by(
            twitter_handle=handle.lstrip("@")
        ).first()
        return (True, user) if user else (False, None)


# Response builder

def make_reply(text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.utcnow(),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=text)],
    )


# Command handlers

def handle_send(text: str, sender_handle: str, tweet_id: str = None) -> str:
    """Process a send command with full validation."""
    chain = None
    match = SEND_WITH_CHAIN.search(text)
    if match:
        amount_str, token, recipient, chain = match.groups()
        chain = chain.lower()
    else:
        match = SEND_PATTERN.search(text)
        if not match:
            return "❌ Invalid send format. Try: send 10 USDT to @friend"
        amount_str, token, recipient = match.groups()

    try:
        amount = float(amount_str)
    except ValueError:
        return "❌ Invalid amount."

    if amount <= 0:
        return "❌ Amount must be greater than zero."

    token = token.upper()

    # Check sender registration
    registered, sender_user = is_registered(sender_handle)
    if not registered:
        return f"❌ You're not registered. Sign up at {REGISTER_URL}"

    # Check recipient registration
    registered, _ = is_registered(recipient)
    if not registered:
        return f"❌ @{recipient} isn't on Solatran yet. They can register at {REGISTER_URL}"

    # Check daily limit
    allowed, limit_msg = check_daily_limit(sender_user.id, token, amount)
    if not allowed:
        return limit_msg

    # Execute transfer
    result = internal_transfer(
        sender_handle=sender_handle,
        recipient_handle=recipient,
        token=token,
        amount_human=amount,
        tweet_id=tweet_id or str(uuid4()),
        chain=chain,
    )
    return result["message"]


def handle_balance(sender_handle: str) -> str:
    """Return formatted balance for a user."""
    registered, user = is_registered(sender_handle)
    if not registered:
        return f"❌ You're not registered. Sign up at {REGISTER_URL}"

    balances = get_all_balances(user.id)
    if not balances:
        return f"Your Solatran balance is empty.\nDeposit funds at {REGISTER_URL}"

    lines = ["💰 Your Solatran balances:\n"]
    for b in balances:
        lines.append(f"  {b['amount']} {b['token']} ({b['chain']})")
    return "\n".join(lines)


def handle_deposit(text: str, sender_handle: str) -> str:
    """Return deposit address(es) for a user."""
    from models import Wallet
    registered, user = is_registered(sender_handle)
    if not registered:
        return f"❌ You're not registered. Sign up at {REGISTER_URL}"

    match = DEPOSIT_PATTERN.search(text)
    token = match.group(1).upper() if match and match.group(1) else None

    chain_map = {
        "SOL": "solana", "ETH": "ethereum",
        "USDT": "tron",  "USDC": "ethereum", "BNB": "ethereum",
    }

    with Session() as db:
        if token:
            chain = chain_map.get(token)
            if not chain:
                return f"❌ {token} is not supported."
            wallet = db.query(Wallet).filter_by(user_id=user.id, chain=chain).first()
            if not wallet:
                return f"❌ No {chain} wallet found."
            return f"📥 Your {token} deposit address ({chain}):\n\n{wallet.address}"
        else:
            wallets = db.query(Wallet).filter_by(user_id=user.id).all()
            lines = ["📥 Your Solatran deposit addresses:\n"]
            for w in wallets:
                lines.append(f"{w.chain.upper()}:\n{w.address}\n")
            return "\n".join(lines)


def handle_withdraw(text: str, sender_handle: str) -> str:
    """Process a withdrawal with validation."""
    match = WITHDRAW_PATTERN.search(text)
    if not match:
        return "❌ Invalid format. Try: withdraw 0.1 ETH 0xYourAddress"

    amount_str, token, to_address = match.groups()

    try:
        amount = float(amount_str)
    except ValueError:
        return "❌ Invalid amount."

    registered, user = is_registered(sender_handle)
    if not registered:
        return f"❌ You're not registered. Sign up at {REGISTER_URL}"

    # Check daily limit
    allowed, limit_msg = check_daily_limit(user.id, token, amount)
    if not allowed:
        return limit_msg

    result = withdraw(
        sender_handle=sender_handle,
        token=token,
        amount_human=amount,
        to_address=to_address,
    )
    return result["message"]


def handle_history(sender_handle: str) -> str:
    """Return last 5 transactions for a user."""
    registered, user = is_registered(sender_handle)
    if not registered:
        return f"❌ You're not registered. Sign up at {REGISTER_URL}"

    with Session() as db:
        txs = db.query(Transaction).filter(
            (Transaction.sender_id == user.id) |
            (Transaction.recipient_id == user.id)
        ).order_by(Transaction.created_at.desc()).limit(5).all()

    if not txs:
        return "No transactions yet."

    lines = ["📋 Your last 5 transactions:\n"]
    for tx in txs:
        direction = "sent" if tx.sender_id == user.id else "received"
        amount = from_smallest_unit(int(tx.amount), tx.token)
        date = tx.created_at.strftime("%b %d %H:%M")
        lines.append(f"  {date} — {direction} {amount} {tx.token} [{tx.status}]")

    return "\n".join(lines)


def handle_limits(sender_handle: str) -> str:
    """Show daily spending limits and how much is remaining."""
    registered, user = is_registered(sender_handle)
    if not registered:
        return f"❌ You're not registered. Sign up at {REGISTER_URL}"

    lines = ["📊 Your daily transfer limits:\n"]
    for token, limit in DAILY_LIMITS.items():
        spent = get_daily_spent(user.id, token)
        remaining = max(0, limit - spent)
        lines.append(f"  {token}: {remaining:.4f} / {limit} remaining")

    return "\n".join(lines)


def handle_help() -> str:
    return (
        "👋 Welcome to Solatran!\n\n"
        "Commands:\n"
        "  send [amount] [token] to @user\n"
        "  balance\n"
        "  deposit [token]\n"
        "  withdraw [amount] [token] [address]\n"
        "  history\n"
        "  limits\n\n"
        f"Register at {REGISTER_URL}"
    )


# Message router

def route_message(text: str, sender_address: str) -> str:
    """
    Route an incoming message to the correct handler.
    sender_address is the Fetch.ai agent address of the sender.
    For Twitter-originated commands, sender_handle is passed in the text.
    """
    # Extract Twitter handle if message comes from the Twitter bot
    # Format: "TWITTER:@handle: <command>"
    twitter_handle = None
    tweet_id = None

    if text.startswith("TWITTER:"):
        # Message from the Twitter bot — extract handle and command
        parts = text.split(":", 2)
        if len(parts) >= 3:
            twitter_handle = parts[1].lstrip("@")
            text = parts[2].strip()
        if "TWEETID:" in text:
            text, tweet_id = text.split("TWEETID:", 1)
            tweet_id = tweet_id.strip()
            text = text.strip()

    # Use agent address as identifier if no Twitter handle
    identifier = twitter_handle or sender_address

    text_lower = text.lower().strip()

    if SEND_PATTERN.search(text_lower) or SEND_WITH_CHAIN.search(text_lower):
        return handle_send(text, identifier, tweet_id)
    elif BALANCE_PATTERN.search(text_lower):
        return handle_balance(identifier)
    elif DEPOSIT_PATTERN.search(text_lower):
        return handle_deposit(text, identifier)
    elif WITHDRAW_PATTERN.search(text_lower):
        return handle_withdraw(text, identifier)
    elif HISTORY_PATTERN.search(text_lower):
        return handle_history(identifier)
    elif LIMITS_PATTERN.search(text_lower):
        return handle_limits(identifier)
    else:
        return handle_help()


# Chat protocol handlers

@chat_proto.on_message(ChatMessage)
async def handle_message(ctx: Context, sender: str, msg: ChatMessage):
    ctx.logger.info(f"Message from {sender}")

    await ctx.send(sender, ChatAcknowledgement(
        timestamp=datetime.utcnow(),
        acknowledged_msg_id=msg.msg_id
    ))

    for item in msg.content:
        if isinstance(item, StartSessionContent):
            ctx.logger.info(f"Session started with {sender}")
            welcome = make_reply(handle_help())
            await ctx.send(sender, welcome)

        elif isinstance(item, TextContent):
            ctx.logger.info(f"Text from {sender}: {item.text[:60]}")
            response_text = route_message(item.text, sender)
            await ctx.send(sender, make_reply(response_text))

        elif isinstance(item, EndSessionContent):
            ctx.logger.info(f"Session ended with {sender}")

        else:
            ctx.logger.info(f"Unknown content type from {sender}")


@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    ctx.logger.info(f"ACK from {sender} for {msg.acknowledged_msg_id}")


# Run

agent.include(chat_proto, publish_manifest=True)

if __name__ == "__main__":
    print(f"🤖 Solatran agent starting...")
    print(f"   Address: {agent.address}")
    print(f"   Endpoint: http://127.0.0.1:8000/submit")
    agent.run()