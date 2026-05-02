"""
transfer.py — Transfer engine for Solatran
Handles: internal transfers, on-chain withdrawals, balance checks
All internal transfers are just DB updates — zero fees, instant.
Only withdrawals touch the actual blockchain.
"""

import os
from decimal import Decimal
from datetime import datetime
from dotenv import load_dotenv
from models import Session, User, Balance, Transaction, Wallet
from wallets import (
    get_solana_keypair, get_ethereum_account, get_tron_key,
    get_contract, TOKEN_CHAINS
)

load_dotenv()

# ─── Token decimal places ─────────────────────────────────────────────────────
TOKEN_DECIMALS = {
    "SOL":   9,    # lamports
    "ETH":   18,   # wei
    "USDT":  6,    # micro-USDT (same on ETH and Tron)
    "USDC":  6,
    "BNB":   18,
    "MATIC": 18,
}

# RPC endpoints
SOLANA_RPC   = os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
ETHEREUM_RPC = os.getenv("ETHEREUM_RPC", "https://mainnet.infura.io/v3/YOUR_KEY")
TRON_RPC     = os.getenv("TRON_RPC", "https://api.trongrid.io")

FEE_PERCENT = Decimal("0.005")   # 0.5% on withdrawals only


def to_smallest_unit(amount: float, token: str) -> int:
    """Convert human-readable amount to smallest unit (lamports, wei, etc.)"""
    decimals = TOKEN_DECIMALS.get(token.upper(), 6)
    return int(Decimal(str(amount)) * (10 ** decimals))


def from_smallest_unit(amount: int, token: str) -> Decimal:
    """Convert smallest unit back to human-readable."""
    decimals = TOKEN_DECIMALS.get(token.upper(), 6)
    return Decimal(amount) / (10 ** decimals)


# ─── Balance helpers ──────────────────────────────────────────────────────────

def get_balance(user_id: int, chain: str, token: str) -> int:
    """Return user balance in smallest unit. Returns 0 if no row exists."""
    with Session() as session:
        bal = session.query(Balance).filter_by(
            user_id=user_id, chain=chain, token=token.upper()
        ).first()
        return int(bal.amount) if bal else 0


def get_all_balances(user_id: int) -> list[dict]:
    """Return all non-zero balances for a user."""
    with Session() as session:
        bals = session.query(Balance).filter(
            Balance.user_id == user_id,
            Balance.amount > 0
        ).all()
        return [
            {
                "chain": b.chain,
                "token": b.token,
                "amount": str(from_smallest_unit(int(b.amount), b.token)),
            }
            for b in bals
        ]


def credit_balance(session, user_id: int, chain: str, token: str, amount: int):
    """Add amount to user's balance. Creates row if needed."""
    token = token.upper()
    bal = session.query(Balance).filter_by(
        user_id=user_id, chain=chain, token=token
    ).first()
    if bal:
        bal.amount = int(bal.amount) + amount
        bal.updated_at = datetime.utcnow()
    else:
        session.add(Balance(user_id=user_id, chain=chain, token=token, amount=amount))


def debit_balance(session, user_id: int, chain: str, token: str, amount: int) -> bool:
    """
    Subtract amount from user's balance.
    Returns False if insufficient funds.
    """
    token = token.upper()
    bal = session.query(Balance).filter_by(
        user_id=user_id, chain=chain, token=token
    ).with_for_update().first()   # row-level lock prevents race conditions
    if not bal or int(bal.amount) < amount:
        return False
    bal.amount = int(bal.amount) - amount
    bal.updated_at = datetime.utcnow()
    return True


# ─── Internal transfer (Twitter tip) ─────────────────────────────────────────

def internal_transfer(
    sender_handle: str,
    recipient_handle: str,
    token: str,
    amount_human: float,
    tweet_id: str,
    chain: str = None,
) -> dict:
    """
    Transfer between two registered users — pure DB update, no blockchain.
    Automatically picks cheapest chain if not specified.
    Returns: {"success": bool, "message": str, "signature": str|None}
    """
    token = token.upper()

    # Auto-select chain (prefer cheapest: Tron > Solana > Ethereum)
    if not chain:
        chain = _pick_chain(token)
    if not chain:
        return {"success": False, "message": f"❌ {token} is not supported."}

    amount = to_smallest_unit(amount_human, token)
    if amount <= 0:
        return {"success": False, "message": "❌ Amount must be greater than zero."}

    with Session() as session:
        # Prevent duplicate tweet processing
        existing = session.query(Transaction).filter_by(tweet_id=tweet_id).first()
        if existing:
            return {"success": False, "message": "❌ This tweet was already processed."}

        sender = session.query(User).filter_by(twitter_handle=sender_handle.lstrip("@")).first()
        recipient = session.query(User).filter_by(twitter_handle=recipient_handle.lstrip("@")).first()

        if not sender:
            return {"success": False, "message": f"❌ @{sender_handle} is not registered. Visit solatran.xyz to register."}
        if not recipient:
            return {"success": False, "message": f"❌ @{recipient_handle} is not registered. They need to sign up at solatran.xyz first."}
        if sender.id == recipient.id:
            return {"success": False, "message": "❌ You can't send to yourself."}

        # Debit sender
        if not debit_balance(session, sender.id, chain, token, amount):
            current = from_smallest_unit(get_balance(sender.id, chain, token), token)
            return {
                "success": False,
                "message": f"❌ Insufficient balance. You have {current} {token} on {chain}."
            }

        # Credit recipient
        credit_balance(session, recipient.id, chain, token, amount)

        # Record transaction
        tx = Transaction(
            tweet_id=tweet_id,
            sender_id=sender.id,
            recipient_id=recipient.id,
            chain=chain,
            token=token,
            amount=amount,
            tx_type="transfer",
            status="success",
        )
        session.add(tx)
        session.commit()

        readable = from_smallest_unit(amount, token)
        return {
            "success": True,
            "message": f"✅ Sent {readable} {token} to @{recipient_handle} (via {chain}). Instant & free!",
            "tx_id": tx.id,
        }


# ─── On-chain withdrawal ──────────────────────────────────────────────────────

def withdraw(
    sender_handle: str,
    token: str,
    amount_human: float,
    to_address: str,
    chain: str = None,
) -> dict:
    """
    Withdraw funds on-chain to an external wallet.
    Deducts 0.5% fee. Hits the actual blockchain.
    """
    token = token.upper()
    if not chain:
        chain = _pick_chain(token)
    if not chain:
        return {"success": False, "message": f"❌ {token} is not supported."}

    amount = to_smallest_unit(amount_human, token)
    fee = int(Decimal(amount) * FEE_PERCENT)
    net_amount = amount - fee

    with Session() as session:
        sender = session.query(User).filter_by(twitter_handle=sender_handle.lstrip("@")).first()
        if not sender:
            return {"success": False, "message": "❌ You are not registered."}

        if not debit_balance(session, sender.id, chain, token, amount):
            current = from_smallest_unit(get_balance(sender.id, chain, token), token)
            return {
                "success": False,
                "message": f"❌ Insufficient balance. You have {current} {token}."
            }

        # Execute on-chain tx
        sig = None
        try:
            bot_wallet = session.query(Wallet).filter_by(
                user_id=sender.id, chain=chain
            ).first()
            if chain == "solana":
                sig = _withdraw_solana(bot_wallet.encrypted_key, to_address, net_amount, token)
            elif chain == "ethereum":
                sig = _withdraw_ethereum(bot_wallet.encrypted_key, to_address, net_amount, token)
            elif chain == "tron":
                sig = _withdraw_tron(bot_wallet.encrypted_key, to_address, net_amount, token)
        except Exception as e:
            # Rollback balance deduction on failure
            credit_balance(session, sender.id, chain, token, amount)
            session.commit()
            return {"success": False, "message": f"❌ On-chain error: {str(e)}"}

        tx = Transaction(
            sender_id=sender.id,
            chain=chain,
            token=token,
            amount=amount,
            tx_type="withdrawal",
            status="success",
            on_chain_sig=sig,
        )
        session.add(tx)
        session.commit()

        readable = from_smallest_unit(net_amount, token)
        return {
            "success": True,
            "message": f"✅ Withdrew {readable} {token} to {to_address[:8]}... Tx: {sig}",
        }


# ─── On-chain implementations ─────────────────────────────────────────────────

def _withdraw_solana(encrypted_key: str, to_address: str, lamports: int, token: str) -> str:
    from solana.rpc.api import Client
    from solana.transaction import Transaction as SolTx
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer

    client = Client(SOLANA_RPC)
    kp = get_solana_keypair(encrypted_key)

    if token == "SOL":
        blockhash = client.get_latest_blockhash().value.blockhash
        ix = transfer(TransferParams(
            from_pubkey=kp.pubkey(),
            to_pubkey=Pubkey.from_string(to_address),
            lamports=lamports
        ))
        tx = SolTx()
        tx.recent_blockhash = blockhash
        tx.add(ix)
        resp = client.send_transaction(tx, kp)
        return str(resp.value)
    else:
        # SPL token transfer (USDT, USDC)
        # TODO: implement SPL token transfer
        raise NotImplementedError(f"SPL {token} withdrawal coming soon")


def _withdraw_ethereum(encrypted_key: str, to_address: str, amount_wei: int, token: str) -> str:
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(ETHEREUM_RPC))
    acct = get_ethereum_account(encrypted_key)

    if token == "ETH":
        tx = {
            "to": to_address,
            "value": amount_wei,
            "gas": 21000,
            "gasPrice": w3.eth.gas_price,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "chainId": 1,
        }
    else:
        # ERC-20 transfer
        contract_addr = get_contract("ethereum", token)
        erc20_abi = [{"inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"}]
        contract = w3.eth.contract(address=contract_addr, abi=erc20_abi)
        tx = contract.functions.transfer(to_address, amount_wei).build_transaction({
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "gasPrice": w3.eth.gas_price,
            "chainId": 1,
        })

    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    return tx_hash.hex()


def _withdraw_tron(encrypted_key: str, to_address: str, amount: int, token: str) -> str:
    from tronpy import Tron
    from tronpy.keys import PrivateKey
    client = Tron()
    priv = get_tron_key(encrypted_key)

    if token == "USDT":
        contract_addr = get_contract("tron", "USDT")
        contract = client.get_contract(contract_addr)
        txn = (
            contract.functions.transfer(to_address, amount)
            .with_owner(priv.public_key.to_base58check_address())
            .fee_limit(10_000_000)
            .build()
            .sign(priv)
        )
        result = txn.broadcast().wait()
        return result["id"]
    else:
        raise NotImplementedError(f"Tron {token} withdrawal not yet supported")


# ─── Chain picker ─────────────────────────────────────────────────────────────

def _pick_chain(token: str) -> str | None:
    """
    Auto-select the cheapest chain for a token.
    Priority: tron (cheapest) > solana > ethereum (most expensive)
    """
    chains = TOKEN_CHAINS.get(token.upper(), [])
    priority = ["tron", "solana", "ethereum"]
    for chain in priority:
        if chain in chains:
            return chain
    return chains[0] if chains else None
