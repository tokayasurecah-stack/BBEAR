from fastapi import FastAPI, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional

from database import Base, engine, get_db
from models import User
from schemas import (
    AuthPaymentRequest, AuthPaymentResponse,
    StatusResponse, MeResponse,
)
from auth import hash_password, verify_password, create_token, decode_token
from payments import initiate, check_status

Base.metadata.create_all(bind=engine)
app = FastAPI(title="Subscription API")
PLAN_DAYS = 34


@app.get("/")
def health(): return {"ok": True}


def _days_left(user: User) -> int:
    if not user.subscription_end:
        return 0
    delta = user.subscription_end - datetime.utcnow()
    return max(0, delta.days)


def _finalize_success(db: Session, reference: str) -> Optional[User]:
    """When a payment turns success, extend user's subscription."""
    from payments import _pending
    entry = _pending.get(reference)
    if not entry:
        return None
    user = db.query(User).filter(User.email == entry["email"]).first()
    if not user:
        return None
    # Idempotency: only extend once per reference
    if user.last_payment_ref == reference:
        return user

    now = datetime.utcnow()
    base = user.subscription_end if (user.subscription_end and user.subscription_end > now) else now
    user.subscription_end = base + timedelta(days=PLAN_DAYS)
    user.phone = entry.get("phone", user.phone)
    user.last_payment_ref = reference
    db.commit()
    db.refresh(user)
    return user


@app.post("/auth/pay", response_model=AuthPaymentResponse)
def auth_and_pay(payload: AuthPaymentRequest, db: Session = Depends(get_db)):
    """
    Step 1: Create/verify user, start payment, return 'pending' immediately.
    Kotlin will then poll /payment/status/{reference}.
    """
    email = payload.email.lower().strip()
    user = db.query(User).filter(User.email == email).first()

    # ---- AUTH ----
    if user is None:
        user = User(
            email=email,
            phone=payload.phone,
            hashed_password=hash_password(payload.password),
        )
        db.add(user); db.commit(); db.refresh(user)
    else:
        if not verify_password(payload.password, user.hashed_password):
            raise HTTPException(401, "Wrong email or password")

    # ---- INITIATE PAYMENT (non-blocking) ----
    res = initiate(
        amount=payload.amount or 3000,
        phone=payload.phone,
        network=payload.network.upper(),
        email=email,
    )

    if res["status"] == "failed":
        return AuthPaymentResponse(
            status="failed",
            message="Could not start payment. Try again.",
        )

    return AuthPaymentResponse(
        status="pending",
        message="Enter your PIN on the phone to confirm.",
        reference=res["reference"],
        days_left=_days_left(user),
    )


@app.get("/payment/status/{reference}", response_model=StatusResponse)
def payment_status(reference: str, db: Session = Depends(get_db)):
    """Kotlin polls this every ~5s until status != 'pending'."""
    result = check_status(reference)
    s = result["status"]

    if s == "unknown":
        return StatusResponse(status="unknown", message="Unknown reference")

    if s == "pending":
        return StatusResponse(status="pending", message="Waiting for PIN confirmation…")

    if s == "failed":
        return StatusResponse(status="failed", message="Payment failed or cancelled")

    # success → extend subscription, mint token
    user = _finalize_success(db, reference)
    if not user:
        return StatusResponse(status="failed", message="Payment OK but user missing")

    days = _days_left(user)
    token = create_token(user.email, days=days)
    return StatusResponse(
        status="success",
        days_left=days,
        message=f"Payment received. Enjoy {days} days!",
        access_token=token,
    )


@app.get("/me", response_model=MeResponse)
def me(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Kotlin calls this on app launch to check if subscription is still valid."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token")
    data = decode_token(authorization.split(" ", 1)[1])
    if not data:
        raise HTTPException(401, "Invalid token")

    user = db.query(User).filter(User.email == data["sub"]).first()
    if not user:
        raise HTTPException(404, "User not found")

    return MeResponse(
        email=user.email,
        days_left=_days_left(user),
        subscription_end=user.subscription_end.isoformat() if user.subscription_end else None,
    )


# ============================================================
# ⚠️ DEBUG ENDPOINTS — REMOVE BEFORE PRODUCTION ⚠️
# ============================================================

@app.get("/debug/users")
def debug_users(db: Session = Depends(get_db)):
    """List all users — helps you verify what's actually in the DB."""
    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "phone": u.phone,
            "hashed_password_prefix": (u.hashed_password or "")[:20],
            "hashed_password_length": len(u.hashed_password or ""),
            "subscription_end": u.subscription_end.isoformat() if u.subscription_end else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@app.delete("/debug/user/{email}")
def debug_delete_user(email: str, db: Session = Depends(get_db)):
    """Delete one user by email — for cleaning up broken signups."""
    target = email.lower().strip()
    user = db.query(User).filter(User.email == target).first()
    if not user:
        return {"deleted": False, "reason": "not found", "email": target}
    db.delete(user)
    db.commit()
    return {"deleted": True, "email": target}


@app.delete("/debug/all-users")
def debug_wipe(db: Session = Depends(get_db)):
    """Nuclear: wipe every user. Use with care."""
    count = db.query(User).delete()
    db.commit()
    return {"deleted": count}
