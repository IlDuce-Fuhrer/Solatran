"""
watcher.py — Solatran Deposit Watcher
Runs as a background process, monitoring every user's deposit wallets
on all chains. When funds arrive on-chain, credits the user's balance
in the database automatically.

Checks every 30 seconds.
Tracks last-seen transaction signatures to avoid double-crediting.
"""

import os
import time
import logging
from datetime import datetime
from dotenv import load_dotenv
from models import Session, User, Wallet, Transaction, init_db
from transfer import credit_balance, to_smallest_unit, from_smallest_unit

load_dotenv()

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHER] %(message)s",
    handlers=[
        logging.FileHandler("watcher.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
SOLANA_RPC   = os.getenv("SOLANA_RPC", "https://api.devnet.solana.com")
ETHEREUM_RPC = os.getenv("ETHEREUM_RPC", "https://mainnet.infura.io/v3/YOUR_KEY")
TRON_RPC     = os.getenv("TRON_RPC", "https://api.trongrid.io")

POLL_INTERVAL = 30   # seconds between each check

# SPL token mints on Solana
SPL_MINTS = {
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
}

# ERC-20 contracts on Ethereum
ERC20_CONTRACTS = {
    "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
}

# TRC-20 contracts on Tron
TRC20_CONTRACTS = {
    "USDT": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
    "USDC": "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8",
}

# Minimum deposit amounts (ignore dust/spam)
MIN_DEPOSIT = {
    "SOL":  0.001,
    "ETH":  0.0001,
    "USDT": 0.5,
    "USDC": 0.5,
    "BNB":  0.001,
}


# ─── Deposit helpers ──────────────────────────────────────────────────────────

def is_seen(on_chain_sig: str) -> bool:
    """Check if we already processed this on-chain transaction."""
    with Session() as db:
        return db.query(Transaction).filter_by(
            on_chain_sig=on_chain_sig,
            tx_type="deposit"
        ).first() is not None


def record_deposit(user_id: int, chain: str, token: str,
                   amount_smallest: int, on_chain_sig: str):
    """Credit a user's balance and record the deposit in the DB."""
    with Session() as db:
        credit_balance(db, user_id, chain, token, amount_smallest)
        db.add(Transaction(
            sender_id=None,
            recipient_id=user_id,
            chain=chain,
            token=token,
            amount=amount_smallest,
            tx_type="deposit",
            status="success",
            on_chain_sig=on_chain_sig,
        ))
        db.commit()

    readable = from_smallest_unit(amount_smallest, token)
    log.info(f"✅ Credited {readable} {token} to user {user_id} | sig: {on_chain_sig[:16]}...")


# ─── Solana ───────────────────────────────────────────────────────────────────

def check_solana_wallet(user_id: int, address: str):
    try:
        from solana.rpc.api import Client
        from solders.pubkey import Pubkey

        client = Client(SOLANA_RPC)
        pubkey = Pubkey.from_string(address)

        # Check recent signatures for incoming SOL
        sigs_resp = client.get_signatures_for_address(pubkey, limit=10)
        for sig_info in sigs_resp.value:
            sig = str(sig_info.signature)
            if is_seen(sig):
                continue

            tx_resp = client.get_transaction(sig_info.signature, encoding="jsonParsed")
            if not tx_resp.value:
                continue

            meta = tx_resp.value.transaction.meta
            if not meta or meta.err:
                continue

            account_keys = [
                str(k) for k in
                tx_resp.value.transaction.transaction.message.account_keys
            ]
            if address not in account_keys:
                continue

            idx = account_keys.index(address)
            diff = meta.post_balances[idx] - meta.pre_balances[idx]

            if diff >= to_smallest_unit(MIN_DEPOSIT["SOL"], "SOL"):
                record_deposit(user_id, "solana", "SOL", diff, sig)

        # Check SPL tokens (USDT, USDC)
        for token, mint in SPL_MINTS.items():
            try:
                token_accounts = client.get_token_accounts_by_owner(
                    pubkey,
                    {"mint": Pubkey.from_string(mint)},
                    encoding="jsonParsed"
                )
                for account in token_accounts.value:
                    token_pubkey = Pubkey.from_string(str(account.pubkey))
                    token_sigs = client.get_signatures_for_address(token_pubkey, limit=5)

                    for sig_info in token_sigs.value:
                        sig = str(sig_info.signature)
                        if is_seen(sig):
                            continue

                        token_tx = client.get_transaction(sig_info.signature, encoding="jsonParsed")
                        if not token_tx.value:
                            continue

                        token_meta = token_tx.value.transaction.meta
                        if not token_meta or token_meta.err:
                            continue

                        pre_bals = {
                            b.account_index: b
                            for b in (token_meta.pre_token_balances or [])
                        }
                        for bal in (token_meta.post_token_balances or []):
                            if bal.mint == mint and bal.owner == address:
                                pre_amount = int(
                                    pre_bals[bal.account_index].ui_token_amount.amount
                                ) if bal.account_index in pre_bals else 0
                                diff = int(bal.ui_token_amount.amount) - pre_amount
                                min_amt = to_smallest_unit(MIN_DEPOSIT.get(token, 0.5), token)
                                if diff >= min_amt:
                                    record_deposit(user_id, "solana", token, diff, sig)

            except Exception as e:
                log.warning(f"SPL {token} check failed for {address[:8]}: {e}")

    except Exception as e:
        log.error(f"Solana check failed for {address[:8]}: {e}")


# ─── Ethereum ─────────────────────────────────────────────────────────────────

def check_ethereum_wallet(user_id: int, address: str):
    try:
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(ETHEREUM_RPC))
        if not w3.is_connected():
            log.warning("Ethereum RPC not connected")
            return

        latest_block = w3.eth.block_number

        # Check last 5 blocks for incoming ETH
        for block_num in range(latest_block - 4, latest_block + 1):
            block = w3.eth.get_block(block_num, full_transactions=True)
            for tx in block.transactions:
                if tx["to"] and tx["to"].lower() == address.lower():
                    sig = tx["hash"].hex()
                    if is_seen(sig):
                        continue
                    min_wei = to_smallest_unit(MIN_DEPOSIT["ETH"], "ETH")
                    if tx["value"] >= min_wei:
                        record_deposit(user_id, "ethereum", "ETH", tx["value"], sig)

        # Check ERC-20 tokens (USDT, USDC)
        transfer_topic = w3.keccak(text="Transfer(address,address,uint256)").hex()
        for token, contract_addr in ERC20_CONTRACTS.items():
            try:
                logs = w3.eth.get_logs({
                    "address": Web3.to_checksum_address(contract_addr),
                    "topics": [
                        transfer_topic,
                        None,
                        "0x" + "0" * 24 + address[2:].lower()
                    ],
                    "fromBlock": latest_block - 10,
                    "toBlock": latest_block,
                })
                for entry in logs:
                    sig = entry["transactionHash"].hex()
                    if is_seen(sig):
                        continue
                    amount = int(entry["data"], 16)
                    min_amt = to_smallest_unit(MIN_DEPOSIT.get(token, 0.5), token)
                    if amount >= min_amt:
                        record_deposit(user_id, "ethereum", token, amount, sig)

            except Exception as e:
                log.warning(f"ERC-20 {token} check failed for {address[:8]}: {e}")

    except Exception as e:
        log.error(f"Ethereum check failed for {address[:8]}: {e}")


# ─── Tron ─────────────────────────────────────────────────────────────────────

def check_tron_wallet(user_id: int, address: str):
    try:
        import requests

        for token, contract in TRC20_CONTRACTS.items():
            url = f"{TRON_RPC}/v1/accounts/{address}/transactions/trc20"
            params = {"limit": 10, "contract_address": contract}

            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()

            for tx in r.json().get("data", []):
                sig = tx.get("transaction_id", "")
                if not sig or is_seen(sig):
                    continue
                if tx.get("to") != address:
                    continue
                amount = int(tx.get("value", 0))
                min_amt = to_smallest_unit(MIN_DEPOSIT.get(token, 0.5), token)
                if amount >= min_amt:
                    record_deposit(user_id, "tron", token, amount, sig)

    except Exception as e:
        log.error(f"Tron check failed for {address[:8]}: {e}")


# ─── Main loop ────────────────────────────────────────────────────────────────

CHAIN_CHECKERS = {
    "solana":   check_solana_wallet,
    "ethereum": check_ethereum_wallet,
    "tron":     check_tron_wallet,
}


def check_all_wallets():
    with Session() as db:
        wallets = db.query(Wallet).all()
        wallet_list = [(w.user_id, w.chain, w.address) for w in wallets]

    if not wallet_list:
        log.info("No wallets to watch yet.")
        return

    log.info(f"Checking {len(wallet_list)} wallets...")
    for user_id, chain, address in wallet_list:
        checker = CHAIN_CHECKERS.get(chain)
        if checker:
            checker(user_id, address)


def run_watcher():
    log.info("👁️  Solatran deposit watcher starting...")
    init_db()
    while True:
        try:
            check_all_wallets()
        except Exception as e:
            log.error(f"Watcher error: {e}")
        log.info(f"Sleeping {POLL_INTERVAL}s...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_watcher()