from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional
import os
import uuid

from database import get_db
from models import User, Wallet, Withdrawal
from schemas import WithdrawRequest, WithdrawalOut, WithdrawalResponse
from auth import decode_token, verify_password
from payments import get_client as get_nylon_client

router = APIRouter(prefix="/withdrawals", tags=["withdrawals"])

MIN_WITHDRAWAL_UGX = int(os.getenv("MIN_WITHDRAWAL_UGX", "5000"))
WITHDRAWAL_COOLDOWN_MINUTES = 60
AUTO_PAYOUT_ENABLED = os.getenv("AUTO_PAYOUT_ENABLED", "false").lower() == "true"


# ---------- Auth ----------
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


def _days_left(user: User) -> int:
    if not user.subscription_end:
        return 0
    delta = user.subscription_end - datetime.utcnow()
    return max(0, delta.days)


def _ensure_wallet(db: Session, email: str) -> Wallet:
    w = db.query(Wallet).filter(Wallet.email == email).first()
    if not w:
        w = Wallet(email=email, balance_ugx=0, total_earned_ugx=0)
        db.add(w)
        db.commit()
        db.refresh(w)
    return w


def _normalize_phone(phone: str) -> str:
    """Convert 07XXXXXXXX to 2567XXXXXXXX."""
    p = phone.strip().replace(" ", "").replace("+", "")
    if p.startswith("0"):
        p = "256" + p[1:]
    return p


def _send_payout_via_nylonpay(withdrawal: Withdrawal) -> dict:
    """
    Call NylonPay to actually send the money.
    Returns {'status': 'successful'|'failed', 'reference': str, 'raw': ...}
    """
    try:
        client = get_nylon_client()
        payout = client.make_payout(
            amount=withdrawal.amount_ugx,
            currency="UGX",
            customer={
                "name": withdrawal.email.split("@")[0],
                "phone_number": withdrawal.phone,
            },
            destination={
                "account_holder_name": withdrawal.email.split("@")[0],
                "phone_number": withdrawal.phone,
            },
            description=f"Referral withdrawal #{withdrawal.id}",
            reference=f"WD{withdrawal.id:011d}",   # 13 chars, required
        )
        tx = payout.wait()

        if tx and getattr(tx, "status", None) == "successful":
            return {
                "status": "successful",
                "reference": getattr(tx, "reference", None),
                "raw": str(tx),
            }
        return {
            "status": "failed",
            "reference": getattr(tx, "reference", None) if tx else None,
            "raw": str(tx) if tx else "timeout",
        }
    except Exception as e:
        return {"status": "failed", "reference": None, "raw": str(e)}


# ---------- Endpoints ----------

@router.post("/request", response_model=WithdrawalResponse)
def request_withdrawal(
    payload: WithdrawRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    User requests a withdrawal.
    Requires password confirmation.
    Deducts the amount, then either auto-pays or marks pending for admin.
    """
    # 1. Must have active subscription
    if _days_left(user) <= 0:
        raise HTTPException(402, "Your subscription must be active to withdraw")

    # 2. Confirm password
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(401, "Wrong password")

    # 3. Wallet check
    wallet = _ensure_wallet(db, user.email)
    if wallet.balance_ugx < MIN_WITHDRAWAL_UGX:
        raise HTTPException(
            400,
            f"Minimum withdrawal is {MIN_WITHDRAWAL_UGX} UGX. "
            f"Your balance is {wallet.balance_ugx} UGX."
        )

    # 4. Cooldown
    cutoff = datetime.utcnow() - timedelta(minutes=WITHDRAWAL_COOLDOWN_MINUTES)
    recent = db.query(Withdrawal).filter(
        Withdrawal.email == user.email,
        Withdrawal.created_at > cutoff,
    ).first()
    if recent:
        raise HTTPException(
            429,
            f"Please wait {WITHDRAWAL_COOLDOWN_MINUTES} minutes between withdrawals"
        )

    # 5. No pending withdrawal
    pending = db.query(Withdrawal).filter(
        Withdrawal.email == user.email,
        Withdrawal.status.in_(["pending", "processing"]),
    ).first()
    if pending:
        raise HTTPException(409, "You already have a pending withdrawal")

    # 6. Normalize phone
    phone = _normalize_phone(payload.phone)
    if not phone.startswith("256") or len(phone) != 12:
        raise HTTPException(400, "Phone must be in 256XXXXXXXXX or 0XXXXXXXXX format")

    # 7. Deduct wallet + create withdrawal record
    amount = MIN_WITHDRAWAL_UGX
    wallet.balance_ugx -= amount

    w = Withdrawal(
        email=user.email,
        amount_ugx=amount,
        phone=phone,
        network=payload.network.upper(),
        status="pending",
        reference=str(uuid.uuid4()),
        created_at=datetime.utcnow(),
    )
    db.add(w)
    db.commit()
    db.refresh(w)

    # 8. Optionally auto-pay via NylonPay
    if AUTO_PAYOUT_ENABLED:
        w.status = "processing"
        db.commit()

        result = _send_payout_via_nylonpay(w)

        if result["status"] == "successful":
            w.status = "completed"
            w.reference = result.get("reference") or w.reference
            w.processed_at = datetime.utcnow()
            db.commit()
            return WithdrawalResponse(
                status="completed",
                message=f"{amount} UGX has been sent to {phone}.",
                withdrawal_id=w.id,
                amount_ugx=amount,
                new_balance=wallet.balance_ugx,
            )
        else:
            # Refund and mark failed
            wallet.balance_ugx += amount
            w.status = "failed"
            w.note = f"NylonPay: {result.get('raw')}"
            w.processed_at = datetime.utcnow()
            db.commit()
            return WithdrawalResponse(
                status="failed",
                message="Payout failed. Your balance has been refunded. Try again later.",
                withdrawal_id=w.id,
                amount_ugx=amount,
                new_balance=wallet.balance_ugx,
            )

    # 9. Manual mode — leave pending for admin
    return WithdrawalResponse(
        status="pending",
        message=f"Withdrawal request received. {amount} UGX will be sent to {phone}.",
        withdrawal_id=w.id,
        amount_ugx=amount,
        new_balance=wallet.balance_ugx,
    )


@router.get("/my", response_model=list[WithdrawalOut])
def my_withdrawals(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(Withdrawal) \
             .filter(Withdrawal.email == user.email) \
             .order_by(Withdrawal.created_at.desc()) \
             .all()
    return [WithdrawalOut.model_validate(r) for r in rows]


@router.get("/{withdrawal_id}", response_model=WithdrawalOut)
def withdrawal_detail(
    withdrawal_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    w = db.query(Withdrawal).filter(Withdrawal.id == withdrawal_id).first()
    if not w:
        raise HTTPException(404, "Withdrawal not found")
    if w.email != user.email:
        raise HTTPException(403, "Not your withdrawal")
    return WithdrawalOut.model_validate(w)
