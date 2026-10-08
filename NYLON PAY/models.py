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


class Withdrawal(Base):
    """
    A withdrawal request. When the user submits one, we deduct from their
    wallet immediately and create this record with status='pending'.
    Admin then either pays via NylonPay (status='completed') or refunds
    (status='failed').
    """
    __tablename__ = "withdrawals"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    amount_ugx = Column(Integer, nullable=False)
    phone = Column(String, nullable=False)                    # normalized: 256XXXXXXXXX
    network = Column(String, nullable=False)                  # "MTN" | "AIRTEL"

    # statuses: pending | processing | completed | failed
    status = Column(String, default="pending", index=True, nullable=False)

    reference = Column(String, nullable=True)                 # NylonPay reference
    note = Column(String, nullable=True)                      # admin note / failure reason

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
