from sqlalchemy import Column, Integer, String, DateTime, Boolean, BigInteger
from datetime import datetime
from database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String, nullable=True)          # last used phone
    hashed_password = Column(String, nullable=False)
    subscription_end = Column(DateTime, nullable=True)  # when 34-day plan ends
    last_payment_ref = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, index=True)
    referrer_email = Column(String, index=True, nullable=False)      # who earns money
    referred_email = Column(String, unique=True, index=True, nullable=False)  # one-time per user
    amount_ugx = Column(Integer, default=100)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    paid_out = Column(Boolean, default=False)                        # future: cash-out tracking
    note = Column(String, nullable=True)


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    balance_ugx = Column(BigInteger, default=0)                      # current withdrawable balance
    total_earned_ugx = Column(BigInteger, default=0)                 # lifetime total
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
