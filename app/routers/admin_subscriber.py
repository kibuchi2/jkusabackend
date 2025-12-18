# app/routes/subscriber.py
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from jose import JWTError, jwt
from app.database import get_db
from datetime import datetime
from typing import List
import logging
import os
from dotenv import load_dotenv

# Import models and schemas with aliases
from app.models.subscriber import Subscriber as SubscriberModel
from app.schemas.subscriber import (
    Subscriber as SubscriberSchema,
    SubscriberCreate,
    SubscriberUpdate,
    SubscriberStats
)
from app.models.admin import Admin

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# JWT settings
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")
admin_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="admin/auth/login")

# Admin router for authenticated endpoints
router = APIRouter(prefix="/admin/subscribers", tags=["admin_subscribers"])

# Public router for unauthenticated access
public_router = APIRouter(prefix="/subscribers", tags=["public_subscribers"])

def get_current_admin(token: str = Depends(admin_oauth2_scheme), db: Session = Depends(get_db)):
    """Validates the JWT token and returns the current admin user."""
    logger.debug(f"Validating token: {token[:10]}...")
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate admin credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        logger.debug(f"Token payload: {payload}")
        username: str = payload.get("sub")
        user_type: str = payload.get("type")
        if username is None or user_type != "admin":
            logger.warning(f"Invalid token: sub={username}, type={user_type}")
            raise credentials_exception
        admin = db.query(Admin).filter(Admin.username == username).first()
        if admin is None:
            logger.warning(f"Admin not found: {username}")
            raise credentials_exception
        logger.debug(f"Authenticated admin: {admin.username}")
        return admin
    except JWTError as e:
        logger.error(f"JWT decode error: {e}")
        raise credentials_exception

# ==================== PUBLIC ENDPOINTS ====================

@public_router.post("/subscribe", response_model=SubscriberSchema, status_code=status.HTTP_201_CREATED)
def subscribe(
    subscriber: SubscriberCreate,
    db: Session = Depends(get_db)
):
    """Public endpoint to subscribe to newsletter"""
    logger.debug(f"New subscription request: {subscriber.email}")
    
    # Validate email format
    if not SubscriberModel.is_valid_email(subscriber.email):
        logger.warning(f"Invalid email format: {subscriber.email}")
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    # Check if email already exists
    existing = db.query(SubscriberModel).filter(
        SubscriberModel.email == subscriber.email.lower()
    ).first()
    
    if existing:
        if existing.is_active:
            logger.info(f"Email already subscribed: {subscriber.email}")
            raise HTTPException(status_code=400, detail="Email already subscribed")
        else:
            # Reactivate subscription
            logger.info(f"Reactivating subscription: {subscriber.email}")
            existing.is_active = True
            existing.unsubscribed_at = None
            existing.subscribed_at = datetime.utcnow()
            db.commit()
            db.refresh(existing)
            return existing
    
    # Create new subscriber
    db_subscriber = SubscriberModel(
        email=subscriber.email.lower(),
        is_active=True
    )
    
    try:
        db.add(db_subscriber)
        db.commit()
        db.refresh(db_subscriber)
        logger.info(f"New subscriber created: {db_subscriber.email} (ID: {db_subscriber.id})")
        return db_subscriber
    except IntegrityError as e:
        db.rollback()
        logger.error(f"Database integrity error: {e}")
        raise HTTPException(status_code=400, detail="Email already exists")

@public_router.post("/unsubscribe/{email}")
def unsubscribe(
    email: str,
    db: Session = Depends(get_db)
):
    """Public endpoint to unsubscribe from newsletter"""
    logger.debug(f"Unsubscribe request: {email}")
    
    subscriber = db.query(SubscriberModel).filter(
        SubscriberModel.email == email.lower()
    ).first()
    
    if not subscriber:
        logger.warning(f"Subscriber not found: {email}")
        raise HTTPException(status_code=404, detail="Subscriber not found")
    
    if not subscriber.is_active:
        logger.info(f"Already unsubscribed: {email}")
        return {"detail": "Already unsubscribed"}
    
    subscriber.is_active = False
    subscriber.unsubscribed_at = datetime.utcnow()
    db.commit()
    logger.info(f"Subscriber unsubscribed: {email}")
    
    return {"detail": "Successfully unsubscribed"}

# ==================== ADMIN ENDPOINTS ====================

@router.get("/", response_model=List[SubscriberSchema])
def get_all_subscribers(
    skip: int = 0,
    limit: int = 100,
    active_only: bool = False,
    db: Session = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin)
):
    """Get all subscribers (admin only)"""
    logger.debug(f"Fetching subscribers: skip={skip}, limit={limit}, active_only={active_only}")
    
    query = db.query(SubscriberModel)
    if active_only:
        query = query.filter(SubscriberModel.is_active == True)
    
    subscribers = query.order_by(SubscriberModel.subscribed_at.desc()).offset(skip).limit(limit).all()
    logger.debug(f"Retrieved {len(subscribers)} subscribers")
    return subscribers

@router.get("/stats", response_model=SubscriberStats)
def get_subscriber_stats(
    db: Session = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin)
):
    """Get subscriber statistics (admin only)"""
    logger.debug("Fetching subscriber statistics")
    
    total = db.query(func.count(SubscriberModel.id)).scalar()
    active = db.query(func.count(SubscriberModel.id)).filter(
        SubscriberModel.is_active == True
    ).scalar()
    unsubscribed = total - active
    
    stats = SubscriberStats(
        total_subscribers=total,
        active_subscribers=active,
        unsubscribed=unsubscribed
    )
    
    logger.debug(f"Stats: {stats}")
    return stats

@router.get("/{subscriber_id}", response_model=SubscriberSchema)
def get_subscriber(
    subscriber_id: int,
    db: Session = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin)
):
    """Get a specific subscriber by ID (admin only)"""
    logger.debug(f"Fetching subscriber ID: {subscriber_id}")
    
    subscriber = db.query(SubscriberModel).filter(
        SubscriberModel.id == subscriber_id
    ).first()
    
    if not subscriber:
        logger.warning(f"Subscriber ID {subscriber_id} not found")
        raise HTTPException(status_code=404, detail="Subscriber not found")
    
    return subscriber

@router.put("/{subscriber_id}", response_model=SubscriberSchema)
def update_subscriber(
    subscriber_id: int,
    subscriber_update: SubscriberUpdate,
    db: Session = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin)
):
    """Update subscriber status (admin only)"""
    logger.debug(f"Updating subscriber ID: {subscriber_id}")
    
    subscriber = db.query(SubscriberModel).filter(
        SubscriberModel.id == subscriber_id
    ).first()
    
    if not subscriber:
        logger.warning(f"Subscriber ID {subscriber_id} not found")
        raise HTTPException(status_code=404, detail="Subscriber not found")
    
    if subscriber_update.is_active is not None:
        subscriber.is_active = subscriber_update.is_active
        if not subscriber_update.is_active:
            subscriber.unsubscribed_at = datetime.utcnow()
        else:
            subscriber.unsubscribed_at = None
    
    db.commit()
    db.refresh(subscriber)
    logger.info(f"Updated subscriber ID: {subscriber_id}")
    
    return subscriber

@router.delete("/{subscriber_id}")
def delete_subscriber(
    subscriber_id: int,
    db: Session = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin)
):
    """Delete a subscriber (admin only)"""
    logger.debug(f"Deleting subscriber ID: {subscriber_id}")
    
    subscriber = db.query(SubscriberModel).filter(
        SubscriberModel.id == subscriber_id
    ).first()
    
    if not subscriber:
        logger.warning(f"Subscriber ID {subscriber_id} not found")
        raise HTTPException(status_code=404, detail="Subscriber not found")
    
    db.delete(subscriber)
    db.commit()
    logger.info(f"Deleted subscriber ID: {subscriber_id}")
    
    return {"detail": "Subscriber deleted"}

@router.get("/search/{email}", response_model=SubscriberSchema)
def search_subscriber_by_email(
    email: str,
    db: Session = Depends(get_db),
    current_admin: Admin = Depends(get_current_admin)
):
    """Search for a subscriber by email (admin only)"""
    logger.debug(f"Searching for subscriber: {email}")
    
    subscriber = db.query(SubscriberModel).filter(
        SubscriberModel.email == email.lower()
    ).first()
    
    if not subscriber:
        logger.warning(f"Subscriber not found: {email}")
        raise HTTPException(status_code=404, detail="Subscriber not found")
    
    return subscriber