from sqlalchemy import Column, Integer, String, DateTime
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