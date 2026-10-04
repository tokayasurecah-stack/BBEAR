from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from typing import Optional

from database import get_db
from models import User, Referral, Wallet
from schemas import (
    RedeemReferralRequest, RedeemReferralResponse,
    WalletOut,
)
from auth import decode_token

router = APIRouter(prefix="/referrals", tags=["referrals"])

REFERRAL_REWARD_UGX = 100
MIN_ACCOUNT_AGE_MINUTES = 2        # must have existed for at least this long
MIN_SUBSCRIPTION_DAYS = 1          # must have an active subscription


# ---------- Auth dependency ----------
def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token")
    data = decode_token(authorization.split(" ", 1)[1])
    if not data:
        raise HTTPException(401, "Invalid or expired token")
    user = db.query(User).filter(User.email == data["sub"]).first()
    if not user:
        raise HTTPException(404, "User not found")
    return user


def _ensure_wallet(db: Session, email: str) -> Wallet:
    w = db.query(Wallet).filter(Wallet.email == email).first()
    if not w:
        w = Wallet(email=email, balance_ugx=0, total_earned_ugx=0)
        db.add(w)
        db.commit()
        db.refresh(w)
    return w


# ---------- Endpoints ----------

@router.get("/wallet", response_model=WalletOut)
def my_wallet(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the logged-in user's wallet + how many referrals they've earned from."""
    w = _ensure_wallet(db, user.email)
    count = db.query(func.count(Referral.id)) \
              .filter(Referral.referrer_email == user.email) \
              .scalar() or 0
    return WalletOut(
        email=w.email,
        balance_ugx=w.balance_ugx,
        total_earned_ugx=w.total_earned_ugx,
        referral_count=int(count),
        updated_at=w.updated_at,
    )


@router.post("/redeem", response_model=RedeemReferralResponse)
def redeem_referral(
    payload: RedeemReferralRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Called by the REFERRED user after they've paid.
    Credits the referrer's wallet with 100 UGX.
    """
    me = user.email
    referrer_email = payload.referrer_email

    # ---- 1. Can't refer yourself ----
    if referrer_email == me:
        raise HTTPException(400, "You cannot refer yourself")

    # ---- 2. Referred user must have paid (active subscription) ----
    from main import _days_left   # reuse existing helper
    if _days_left(user) <= 0:
        raise HTTPException(402, "Please subscribe first to earn referral rewards")

    # ---- 3. Anti-fraud: account must be at least N minutes old ----
    if user.created_at and datetime.utcnow() - user.created_at < timedelta(minutes=MIN_ACCOUNT_AGE_MINUTES):
        raise HTTPException(429, "Please wait a moment before redeeming")

    # ---- 4. This user has never referred anyone before ----
    existing = db.query(Referral).filter(Referral.referred_email == me).first()
    if existing:
        raise HTTPException(409, "You have already redeemed a referral")

    # ---- 5. Referrer must exist ----
    referrer = db.query(User).filter(User.email == referrer_email).first()
    if not referrer:
        raise HTTPException(404, "No account found with that email")

    # ---- 6. Credit the referrer ----
    wallet = _ensure_wallet(db, referrer_email)
    wallet.balance_ugx += REFERRAL_REWARD_UGX
    wallet.total_earned_ugx += REFERRAL_REWARD_UGX

    # ---- 7. Record the referral (unique constraint blocks double-redeem) ----
    ref = Referral(
        referrer_email=referrer_email,
        referred_email=me,
        amount_ugx=REFERRAL_REWARD_UGX,
        note=payload.note,
    )
    db.add(ref)
    db.commit()

    return RedeemReferralResponse(
        status="success",
        message=f"Thank you! {REFERRAL_REWARD_UGX} UGX was sent to {referrer_email}",
        amount_ugx=REFERRAL_REWARD_UGX,
        referrer_email=referrer_email,
    )


@router.get("/history")
def my_referral_history(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List everyone the current user has referred + how much they earned."""
    rows = db.query(Referral) \
             .filter(Referral.referrer_email == user.email) \
             .order_by(Referral.created_at.desc()) \
             .all()

    # Mask the email of referred users for privacy
    def _mask(email: str) -> str:
        try:
            name, domain = email.split("@")
            if len(name) <= 2:
                return f"{name[0]}***@{domain}"
            return f"{name[:2]}***@{domain}"
        except Exception:
            return "***"

    return [
        {
            "referred_email_masked": _mask(r.referred_email),
            "amount_ugx": r.amount_ugx,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]