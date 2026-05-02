"""
models.py — SQLAlchemy database models for Solatran
Tables: users, wallets, balances, transactions
"""

from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Numeric,
    DateTime, ForeignKey, UniqueConstraint, Text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

Base = declarative_base()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/solatran")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


class User(Base):
    """
    A registered Solatran user.
    Linked to their Twitter account via OAuth.
    """
    __tablename__ = "users"

    id              = Column(Integer, primary_key=True)
    twitter_id      = Column(String, unique=True, nullable=False)   # Twitter numeric ID
    twitter_handle  = Column(String, unique=True, nullable=False)   # e.g. "elonmusk"
    created_at      = Column(DateTime, default=datetime.utcnow)

    wallets         = relationship("Wallet", back_populates="user", cascade="all, delete")
    balances        = relationship("Balance", back_populates="user", cascade="all, delete")
    sent_txs        = relationship("Transaction", foreign_keys="Transaction.sender_id", back_populates="sender")
    received_txs    = relationship("Transaction", foreign_keys="Transaction.recipient_id", back_populates="recipient")

    def __repr__(self):
        return f"<User @{self.twitter_handle}>"


class Wallet(Base):
    """
    One deposit address per user per chain.
    e.g. user 1 has an ETH address and a Solana address.
    Private keys are stored encrypted — never in plaintext.
    """
    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("user_id", "chain"),)

    id          = Column(Integer, primary_key=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    chain       = Column(String, nullable=False)    # 'solana' | 'ethereum' | 'tron'
    address     = Column(String, nullable=False)    # public address
    encrypted_key = Column(Text, nullable=False)    # AES-encrypted private key

    user        = relationship("User", back_populates="wallets")

    def __repr__(self):
        return f"<Wallet {self.chain}:{self.address[:8]}...>"


class Balance(Base):
    """
    Each user has one row per (chain, token) they hold.
    All amounts stored as smallest unit strings to avoid float precision issues.
    e.g. SOL in lamports, ETH in wei, USDT in micro-USDT (6 decimals)
    """
    __tablename__ = "balances"
    __table_args__ = (UniqueConstraint("user_id", "chain", "token"),)

    id          = Column(Integer, primary_key=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    chain       = Column(String, nullable=False)    # 'solana' | 'ethereum' | 'tron'
    token       = Column(String, nullable=False)    # 'SOL' | 'ETH' | 'USDT' | 'USDC'
    amount      = Column(Numeric(36, 0), default=0, nullable=False)  # in smallest unit
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user        = relationship("User", back_populates="balances")

    def __repr__(self):
        return f"<Balance {self.token}@{self.chain}: {self.amount}>"


class Transaction(Base):
    """
    Full audit trail of every transfer.
    tweet_id is unique — prevents the same tweet being processed twice.
    Internal transfers (between users) have no on-chain signature.
    Withdrawals have an on-chain signature.
    """
    __tablename__ = "transactions"

    id              = Column(Integer, primary_key=True)
    tweet_id        = Column(String, unique=True, nullable=True)    # null for manual/withdrawal
    sender_id       = Column(Integer, ForeignKey("users.id"), nullable=True)   # null = external deposit
    recipient_id    = Column(Integer, ForeignKey("users.id"), nullable=True)   # null = external withdrawal
    chain           = Column(String, nullable=False)
    token           = Column(String, nullable=False)
    amount          = Column(Numeric(36, 0), nullable=False)        # in smallest unit
    tx_type         = Column(String, nullable=False)                # 'transfer' | 'deposit' | 'withdrawal'
    status          = Column(String, default="pending")             # 'pending' | 'success' | 'failed'
    on_chain_sig    = Column(String, nullable=True)                 # blockchain tx hash
    error_msg       = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)

    sender          = relationship("User", foreign_keys=[sender_id], back_populates="sent_txs")
    recipient       = relationship("User", foreign_keys=[recipient_id], back_populates="received_txs")

    def __repr__(self):
        return f"<Tx {self.tx_type} {self.amount} {self.token} [{self.status}]>"


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(engine)
    print("✅ Database tables created.")


if __name__ == "__main__":
    init_db()
