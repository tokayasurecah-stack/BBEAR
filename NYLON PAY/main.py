from fastapi import FastAPI, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional
import os

from database import Base, engine, get_db
from models import User
from schemas import (
    AuthPaymentRequest, AuthPaymentResponse,
    StatusResponse, MeResponse,
)
from auth import hash_password, verify_password, create_token, decode_token
from payments import initiate, check_status
from pydantic import BaseModel as _BaseModel

Base.metadata.create_all(bind=engine)
app = FastAPI(title="Subscription API")
PLAN_DAYS = 34

# ============================================================
# ADMIN AUTH
# ============================================================
ADMIN_KEY = os.getenv("ADMIN_KEY", "")

def require_admin(x_admin_key: Optional[str] = Header(None)):
    if not ADMIN_KEY:
        raise HTTPException(500, "ADMIN_KEY not configured on server")
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(403, "Forbidden")


# ============================================================
# HELPERS
# ============================================================

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


# ============================================================
# AUTH ENDPOINTS
# ============================================================

@app.post("/auth/login")
def auth_login(payload: dict, db: Session = Depends(get_db)):
    """
    Pure login endpoint. Verifies credentials and returns a token if the
    user's subscription is still active. Does NOT start a payment.

    Body: {"email": "...", "password": "..."}
    """
    email = (payload.get("email") or "").lower().strip()
    password = payload.get("password") or ""

    if not email or not password:
        raise HTTPException(400, "Email and password are required")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(401, "Wrong email or password")

    if not verify_password(password, user.hashed_password):
        raise HTTPException(401, "Wrong email or password")

    days = _days_left(user)
    if days <= 0:
        # 402 Payment Required — semantic signal that they need to pay
        raise HTTPException(402, "Subscription expired — please pay to continue")

    token = create_token(user.email, days=days)
    return {
        "status": "success",
        "message": f"Welcome back! {days} days left.",
        "days_left": days,
        "access_token": token,
    }


@app.post("/auth/pay", response_model=AuthPaymentResponse)
def auth_and_pay(payload: AuthPaymentRequest, db: Session = Depends(get_db)):
    """
    Signup + pay, or login + pay (if expired).

    Behavior:
      - New email          → create user, initiate NylonPay
      - Existing + wrong password → 401
      - Existing + active subscription → skip payment, return success with token
      - Existing + expired → initiate NylonPay
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
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if not verify_password(payload.password, user.hashed_password):
            raise HTTPException(401, "Wrong email or password")

        # ⭐ NEW: active subscription → skip payment entirely
        days = _days_left(user)
        if days > 0:
            token = create_token(user.email, days=days)
            return AuthPaymentResponse(
                status="success",
                message=f"Welcome back! {days} days left.",
                days_left=days,
                access_token=token,
            )

    # ---- INITIATE PAYMENT (new user or expired) ----
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
# ADMIN ENDPOINTS — require X-Admin-Key header
# ============================================================

class GrantDaysRequest(_BaseModel):
    days: int = PLAN_DAYS
    reason: Optional[str] = None


class SetSubscriptionRequest(_BaseModel):
    subscription_end: str
    reason: Optional[str] = None


class ResetPasswordRequest(_BaseModel):
    new_password: str


@app.get("/admin/users", dependencies=[Depends(require_admin)])
def admin_list_users(db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.id).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "phone": u.phone,
            "subscription_end": u.subscription_end.isoformat() if u.subscription_end else None,
            "days_left": _days_left(u),
            "last_payment_ref": u.last_payment_ref,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@app.post("/admin/grant-days/{email}", dependencies=[Depends(require_admin)])
def admin_grant_days(
    email: str,
    payload: GrantDaysRequest,
    db: Session = Depends(get_db),
):
    target = email.lower().strip()
    user = db.query(User).filter(User.email == target).first()
    if not user:
        raise HTTPException(404, "User not found")

    now = datetime.utcnow()
    base = user.subscription_end if (user.subscription_end and user.subscription_end > now) else now
    user.subscription_end = base + timedelta(days=payload.days)
    db.commit()
    db.refresh(user)

    return {
        "email": user.email,
        "days_added": payload.days,
        "reason": payload.reason,
        "subscription_end": user.subscription_end.isoformat(),
        "days_left": _days_left(user),
    }


@app.post("/admin/set-subscription/{email}", dependencies=[Depends(require_admin)])
def admin_set_subscription(
    email: str,
    payload: SetSubscriptionRequest,
    db: Session = Depends(get_db),
):
    target = email.lower().strip()
    user = db.query(User).filter(User.email == target).first()
    if not user:
        raise HTTPException(404, "User not found")

    try:
        new_end = datetime.fromisoformat(payload.subscription_end)
    except Exception:
        raise HTTPException(400, "subscription_end must be ISO 8601, e.g. 2026-11-15T20:00:00")

    user.subscription_end = new_end
    db.commit()
    db.refresh(user)

    return {
        "email": user.email,
        "subscription_end": user.subscription_end.isoformat(),
        "days_left": _days_left(user),
        "reason": payload.reason,
    }


@app.post("/admin/reset-password/{email}", dependencies=[Depends(require_admin)])
def admin_reset_password(
    email: str,
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    """
    Force-reset a user's password. Keeps subscription_end intact.
    Useful when a user forgot their password or the stored hash is broken.
    """
    target = email.lower().strip()
    user = db.query(User).filter(User.email == target).first()
    if not user:
        raise HTTPException(404, "User not found")

    user.hashed_password = hash_password(payload.new_password)
    db.commit()
    db.refresh(user)

    return {
        "email": user.email,
        "password_reset": True,
        "days_left": _days_left(user),
        "subscription_end": user.subscription_end.isoformat() if user.subscription_end else None,
    }


@app.post("/admin/revoke/{email}", dependencies=[Depends(require_admin)])
def admin_revoke(email: str, db: Session = Depends(get_db)):
    target = email.lower().strip()
    user = db.query(User).filter(User.email == target).first()
    if not user:
        raise HTTPException(404, "User not found")
    user.subscription_end = None
    db.commit()
    return {"email": user.email, "revoked": True}


@app.delete("/admin/user/{email}", dependencies=[Depends(require_admin)])
def admin_delete_user(email: str, db: Session = Depends(get_db)):
    target = email.lower().strip()
    user = db.query(User).filter(User.email == target).first()
    if not user:
        return {"deleted": False, "reason": "not found"}
    db.delete(user)
    db.commit()
    return {"deleted": True, "email": target}


# ============================================================
# ⚠️ DEBUG ENDPOINTS — REMOVE BEFORE PRODUCTION ⚠️
# ============================================================

@app.get("/debug/users")
def debug_users(db: Session = Depends(get_db)):
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
    target = email.lower().strip()
    user = db.query(User).filter(User.email == target).first()
    if not user:
        return {"deleted": False, "reason": "not found", "email": target}
    db.delete(user)
    db.commit()
    return {"deleted": True, "email": target}


@app.delete("/debug/all-users")
def debug_wipe(db: Session = Depends(get_db)):
    count = db.query(User).delete()
    db.commit()
    return {"deleted": count}
