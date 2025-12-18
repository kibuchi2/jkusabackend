from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import bcrypt
from datetime import datetime, timedelta
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models.user import User
from app.models.admin import Admin
from app.schemas.user import User as UserSchema
from app.schemas.admin import Admin as AdminSchema
from app.schemas.user import Token
import os
from dotenv import load_dotenv
import logging

load_dotenv()

# ==================== LOGGER ====================
logger = logging.getLogger(__name__)

# ==================== CONFIG ====================
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

user_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="user/auth/login")
admin_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="admin/auth/login")


# ==================== PASSWORD UTILITIES ====================
def verify_password(plain_password, hashed_password):
    """Verifies a plaintext password against a bcrypt hash."""
    try:
        if not isinstance(plain_password, str):
            plain_password = plain_password.decode('utf-8', 'ignore')

        if isinstance(hashed_password, str):
            hashed_password = hashed_password.encode('utf-8')

        password_bytes = plain_password.encode('utf-8')
        if len(password_bytes) > 72:
            password_bytes = password_bytes[:72]

        return bcrypt.checkpw(password_bytes, hashed_password)
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False


def get_password_hash(password):
    """Hashes a password using bcrypt directly."""
    try:
        if not isinstance(password, str):
            password = str(password)

        password_bytes = password.encode('utf-8')
        if len(password_bytes) > 72:
            password_bytes = password_bytes[:72]

        salt = bcrypt.gensalt(rounds=12)
        hashed = bcrypt.hashpw(password_bytes, salt)
        return hashed.decode('utf-8')
    except Exception as e:
        logger.error(f"Error hashing password: {e}")
        raise


# ==================== TOKEN CREATION ====================
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# ==================== DB HELPERS ====================
def get_user(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()

def get_admin(db: Session, username: str):
    """Get admin with role relationship loaded"""
    return db.query(Admin).options(joinedload(Admin.role)).filter(Admin.username == username).first()

def get_admin_by_identifier(db: Session, identifier: str):
    """Get admin by username or email with role relationship loaded"""
    return db.query(Admin).options(joinedload(Admin.role)).filter(
        (Admin.username == identifier) | (Admin.email == identifier)
    ).first()

def get_user_by_identifier(db: Session, identifier: str):
    return db.query(User).filter(
        (User.username == identifier) | (User.email == identifier)
    ).first()


# ==================== AUTH VALIDATION ====================
async def get_current_user(token: str = Depends(user_oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate user credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        user_type: str = payload.get("type")
        if username is None or user_type != "user":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = get_user(db, username=username)
    if user is None:
        raise credentials_exception

    return UserSchema(
        id=user.id,
        username=user.username,
        email=user.email,
        first_name=user.first_name if hasattr(user, "first_name") else None,
        last_name=user.last_name if hasattr(user, "last_name") else None,
        phone_number=user.phone_number if hasattr(user, "phone_number") else None,
        is_active=user.is_active if hasattr(user, "is_active") else True
    )


async def get_current_admin(token: str = Depends(admin_oauth2_scheme), db: Session = Depends(get_db)):
    """
    Get the current authenticated admin with role relationship loaded.
    Returns the SQLAlchemy Admin model (not Pydantic schema) to preserve relationships.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate admin credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        user_type: str = payload.get("type")
        if username is None or user_type != "admin":
            raise credentials_exception
    except JWTError as e:
        logger.error(f"JWT decode error: {e}")
        raise credentials_exception

    # Load admin with role relationship eagerly loaded
    admin = db.query(Admin).options(joinedload(Admin.role)).filter(Admin.username == username).first()
    
    if admin is None:
        logger.warning(f"Admin not found for username: {username}")
        raise credentials_exception

    if not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin account is inactive"
        )

    # Log role information for debugging
    if admin.role:
        logger.debug(f"Admin {admin.username} loaded with role: {admin.role.name}, permissions: {admin.role.permissions}")
    else:
        logger.warning(f"Admin {admin.username} has no role assigned (role_id: {admin.role_id})")

    # Return the SQLAlchemy model directly to preserve the role relationship
    return admin