from pydantic import BaseModel, EmailStr, Field, validator
from datetime import datetime
from typing import Optional


# ============================================================
# EXISTING SCHEMAS
# ============================================================

class AuthPaymentRequest(BaseModel):
    email: EmailStr
    password: str
    network: str            # "MTN" | "AIRTEL"
    phone: str
    amount: int = 3000


class AuthPaymentResponse(BaseModel):
    status: str             # "pending" | "success" | "failed" | "error"
    message: str
    reference: Optional[str] = None
    days_left: int = 0
    access_token: Optional[str] = None


class StatusResponse(BaseModel):
    status: str             # "pending" | "success" | "failed" | "unknown"
    days_left: int = 0
    message: str = ""
    access_token: Optional[str] = None


class MeResponse(BaseModel):
    email: str
    days_left: int
    subscription_end: Optional[str] = None


# ============================================================
# REFERRAL SCHEMAS
# ============================================================

class RedeemReferralRequest(BaseModel):
    """Body sent by a NEW user when they enter their referrer's email."""
    referrer_email: EmailStr
    note: Optional[str] = Field(default=None, max_length=200)

    @validator("referrer_email")
    def _clean_email(cls, v: str) -> str:
        return v.lower().strip()


class RedeemReferralResponse(BaseModel):
    """Response after a successful redeem."""
    status: str                     # "success" | "failed" | "error"
    message: str
    amount_ugx: int = 0
    referrer_email: Optional[str] = None


class WalletOut(BaseModel):
    """The logged-in user's wallet summary."""
    email: str
    balance_ugx: int
    total_earned_ugx: int
    referral_count: int
    updated_at: Optional[datetime] = None


class ReferralHistoryItem(BaseModel):
    """One entry in the referral list — the referred user's email is masked."""
    referred_email_masked: str
    amount_ugx: int
    created_at: datetime
