from pydantic import BaseModel, EmailStr
from typing import Optional

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