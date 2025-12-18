from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from jose import JWTError, jwt
from app.database import get_db

# Use aliases to prevent name collision between SQLAlchemy model and Pydantic schema
from app.models.news import News as NewsModel
from app.schemas.news import News as NewsSchema, NewsCreate, NewsUpdate

from app.models.admin import Admin
from app.models.student import student as StudentModel
from app.models.subscriber import Subscriber
from app.services.s3_service import s3_service
from app.services.email_service import send_news_notification_email
from datetime import datetime
from typing import Optional
import logging
import os
from dotenv import load_dotenv

# Add Pillow for image processing
from PIL import Image
import io
import boto3  # Assuming you're using boto3 for S3; add if not already imported in s3_service

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
router = APIRouter(prefix="/admin/news", tags=["admin_news"])

# Public router for unauthenticated access
public_news_router = APIRouter(prefix="/news", tags=["public_news"])

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

def generate_unique_slug(db: Session, title: str, news_id: Optional[int] = None) -> str:
    """Generate a unique slug, appending numbers if necessary"""
    base_slug = NewsModel.generate_slug(title)
    slug = base_slug
    counter = 1
    
    while True:
        query = db.query(NewsModel).filter(NewsModel.slug == slug)
        if news_id:
            query = query.filter(NewsModel.id != news_id)
        
        if not query.first():
            return slug
        
        slug = f"{base_slug}-{counter}"
        counter += 1

@router.post("/", response_model=NewsSchema)
def create_news(
    title: str = Form(...),
    content: str = Form(...),
    published_at: str = Form(...),
    featured_image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Create a new news article with optional featured image"""
    logger.debug(f"Creating news article by user: {current_user.id}")
    
    # Parse published_at
    try:
        parsed_published_at = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
        parsed_published_at = parsed_published_at.replace(second=0, microsecond=0)
    except ValueError as e:
        logger.error(f"Invalid published_at format: {e}")
        raise HTTPException(status_code=400, detail="Invalid published_at format. Use ISO 8601")

    # Generate unique slug
    slug = generate_unique_slug(db, title)
    logger.debug(f"Generated slug: {slug}")

    # Validate and upload featured image
    featured_image_url = None
    if featured_image:
        if not featured_image.content_type.startswith('image/'):
            logger.error(f"Invalid file type: {featured_image.content_type}")
            raise HTTPException(status_code=400, detail="File must be an image")
        if featured_image.size > 5 * 1024 * 1024:
            logger.error(f"Image too large: {featured_image.size} bytes")
            raise HTTPException(status_code=400, detail="Image must be less than 5MB")
        featured_image_url = s3_service.upload_image(featured_image)
        if not featured_image_url:
            logger.error("Failed to upload image to S3")
            raise HTTPException(status_code=500, detail="Failed to upload image")
    
    # Create news article
    db_news = NewsModel(
        title=title.strip(),
        slug=slug,
        content=content.strip(),
        featured_image_url=featured_image_url,
        published_at=parsed_published_at,
        publisher_id=current_user.id
    )
    
    try:
        db.add(db_news)
        db.commit()
        db.refresh(db_news)
        logger.info(f"Created article ID {db_news.id}: {db_news.title} (slug: {slug})")
        
        # Send email notifications to students and subscribers
        try:
            # Get all active students
            students = db.query(StudentModel).filter(StudentModel.is_active == True).all()
            # Get all active subscribers
            subscribers = db.query(Subscriber).filter(Subscriber.is_active == True).all()
            
            # Combine all recipients (avoid duplicates)
            all_recipients = []
            student_emails = set()
            
            # Add students
            for student in students:
                if student.email not in student_emails:
                    all_recipients.append({
                        "email": student.email,
                        "name": student.full_name,
                        "type": "student"
                    })
                    student_emails.add(student.email)
            
            # Add subscribers (excluding students)
            for subscriber in subscribers:
                if subscriber.email not in student_emails:
                    all_recipients.append({
                        "email": subscriber.email,
                        "name": "Subscriber",
                        "type": "subscriber"
                    })
            
            # Send emails
            publisher_name = f"{current_user.first_name} {current_user.last_name}"
            successful_emails = 0
            failed_emails = 0
            
            for recipient in all_recipients:
                try:
                    success = send_news_notification_email(
                        email=recipient["email"],
                        title=title,
                        content=content,
                        image_url=featured_image_url,
                        publisher_name=publisher_name
                    )
                    if success:
                        successful_emails += 1
                    else:
                        failed_emails += 1
                except Exception as e:
                    logger.error(f"Failed to send news email to {recipient['email']}: {str(e)}")
                    failed_emails += 1
            
            logger.info(f"News notification emails sent: {successful_emails} successful, {failed_emails} failed")
            
        except Exception as e:
            logger.error(f"Error sending news notification emails: {str(e)}")
            # Don't fail the news creation if email sending fails
        
    except IntegrityError as e:
        db.rollback()
        logger.error(f"Database integrity error: {e}")
        raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")
    
    return db_news

@router.get("/", response_model=list[NewsSchema])
def read_news_list(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Get list of news articles (admin only)"""
    logger.debug(f"Fetching news list: skip={skip}, limit={limit}")
    return db.query(NewsModel).order_by(NewsModel.published_at.desc()).offset(skip).limit(limit).all()

@public_news_router.get("/", response_model=list[NewsSchema])
def read_public_news_list(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get list of news articles (public access)"""
    logger.debug(f"Fetching public news list: skip={skip}, limit={limit}")
    return db.query(NewsModel).order_by(NewsModel.published_at.desc()).offset(skip).limit(limit).all()

@router.get("/{news_id}", response_model=NewsSchema)
def read_news(
    news_id: int,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Get a specific news article by ID (admin only)"""
    logger.debug(f"Fetching article ID: {news_id}")
    db_news = db.query(NewsModel).filter(NewsModel.id == news_id).first()
    if db_news is None:
        logger.warning(f"Article ID {news_id} not found")
        raise HTTPException(status_code=404, detail="News not found")
    return db_news

@public_news_router.get("/{news_id}", response_model=NewsSchema)
def read_public_news(
    news_id: int,
    db: Session = Depends(get_db)
):
    """Get a specific news article by ID (public access)"""
    logger.debug(f"Fetching public article ID: {news_id}")
    db_news = db.query(NewsModel).filter(NewsModel.id == news_id).first()
    if db_news is None:
        logger.warning(f"Article ID {news_id} not found")
        raise HTTPException(status_code=404, detail="News not found")
    return db_news

@public_news_router.get("/slug/{slug}", response_model=NewsSchema)
def read_news_by_slug(
    slug: str,
    db: Session = Depends(get_db)
):
    """Get a specific news article by slug (public access)"""
    logger.debug(f"Fetching article by slug: {slug}")
    db_news = db.query(NewsModel).filter(NewsModel.slug == slug).first()
    if db_news is None:
        logger.warning(f"Article with slug '{slug}' not found")
        raise HTTPException(status_code=404, detail="News not found")
    return db_news

@router.put("/{news_id}", response_model=NewsSchema)
def update_news(
    news_id: int,
    title: Optional[str] = Form(None), 
    content: Optional[str] = Form(None),
    published_at: Optional[str] = Form(None),
    featured_image: Optional[UploadFile] = File(None),
    remove_image: Optional[str] = Form("false"),
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Update a news article"""
    logger.debug(f"Updating news article ID: {news_id} by user: {current_user.id}")
    logger.debug(f"Received parameters:")
    logger.debug(f"  - title: {title}")
    logger.debug(f"  - content: {content[:50] + '...' if content and len(content) > 50 else content}")
    logger.debug(f"  - published_at: {published_at}")
    logger.debug(f"  - featured_image: {featured_image.filename if featured_image else None}")
    logger.debug(f"  - remove_image: {remove_image}")

    # Fetch the existing article
    db_news = db.query(NewsModel).filter(NewsModel.id == news_id).first()
    if db_news is None:
        logger.warning(f"Article ID {news_id} not found")
        raise HTTPException(status_code=404, detail="News not found")

    # Log current article state
    logger.debug(f"Current article state:")
    logger.debug(f"  - title: {db_news.title}")
    logger.debug(f"  - slug: {db_news.slug}")
    logger.debug(f"  - content: {db_news.content[:50] + '...' if len(db_news.content) > 50 else db_news.content}")
    logger.debug(f"  - published_at: {db_news.published_at}")
    logger.debug(f"  - featured_image_url: {db_news.featured_image_url}")

    updated = False
    changes_made = []

    # Check and update title (and regenerate slug if title changes)
    if title is not None:
        title_trimmed = title.strip()
        if title_trimmed != db_news.title:
            if len(title_trimmed) < 10 or len(title_trimmed) > 255:
                logger.error(f"Invalid title length: {len(title_trimmed)}")
                raise HTTPException(status_code=400, detail="Title must be 10-255 characters")
            
            logger.debug(f"Title change detected: '{db_news.title}' -> '{title_trimmed}'")
            db_news.title = title_trimmed
            
            # Regenerate slug when title changes
            new_slug = generate_unique_slug(db, title_trimmed, news_id)
            logger.debug(f"Slug updated: '{db_news.slug}' -> '{new_slug}'")
            db_news.slug = new_slug
            
            updated = True
            changes_made.append("title")
            changes_made.append("slug")
        else:
            logger.debug("Title unchanged")

    # Check and update content
    if content is not None:
        content_trimmed = content.strip()
        if content_trimmed != db_news.content:
            if len(content_trimmed) < 50:
                logger.error(f"Invalid content length: {len(content_trimmed)}")
                raise HTTPException(status_code=400, detail="Content must be at least 50 characters")
            
            logger.debug(f"Content change detected (length: {len(db_news.content)} -> {len(content_trimmed)})")
            db_news.content = content_trimmed
            updated = True
            changes_made.append("content")
        else:
            logger.debug("Content unchanged")

    # Check and update published_at with improved date handling
    if published_at is not None:
        try:
            # Parse the incoming date
            parsed_date = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
            # Normalize to remove seconds and microseconds for consistent comparison
            parsed_date = parsed_date.replace(second=0, microsecond=0)
            
            # Also normalize the existing date for comparison
            existing_date = db_news.published_at.replace(second=0, microsecond=0)
            
            logger.debug(f"Date comparison:")
            logger.debug(f"  - Parsed date: {parsed_date}")
            logger.debug(f"  - Existing date: {existing_date}")
            logger.debug(f"  - Are equal: {parsed_date == existing_date}")
            
            if parsed_date != existing_date:
                logger.debug(f"Date change detected: '{existing_date}' -> '{parsed_date}'")
                db_news.published_at = parsed_date
                updated = True
                changes_made.append("published_at")
            else:
                logger.debug("Published date unchanged")
                
        except ValueError as e:
            logger.error(f"Invalid published_at format: {e}")
            raise HTTPException(status_code=400, detail="Invalid published_at format. Use ISO 8601")

    # Handle image update or removal
    if featured_image:
        # Validate image
        if not featured_image.content_type.startswith('image/'):
            logger.error(f"Invalid file type: {featured_image.content_type}")
            raise HTTPException(status_code=400, detail="File must be an image")
        if featured_image.size > 5 * 1024 * 1024:
            logger.error(f"Image too large: {featured_image.size} bytes")
            raise HTTPException(status_code=400, detail="Image must be less than 5MB")
        
        # Delete old image if it exists
        if db_news.featured_image_url:
            logger.debug(f"Deleting old image: {db_news.featured_image_url}")
            s3_service.delete_image(db_news.featured_image_url)
        
        # Upload new image
        new_image_url = s3_service.upload_image(featured_image)
        if not new_image_url:
            logger.error("Failed to upload image to S3")
            raise HTTPException(status_code=500, detail="Failed to upload image")
        
        logger.debug(f"Image change detected: '{db_news.featured_image_url}' -> '{new_image_url}'")
        db_news.featured_image_url = new_image_url
        updated = True
        changes_made.append("featured_image")
        
    elif remove_image == "true" and db_news.featured_image_url:
        logger.debug(f"Removing existing image: {db_news.featured_image_url}")
        s3_service.delete_image(db_news.featured_image_url)
        db_news.featured_image_url = None
        updated = True
        changes_made.append("removed_image")

    # Log update summary
    logger.debug(f"Update summary:")
    logger.debug(f"  - Changes detected: {updated}")
    logger.debug(f"  - Fields changed: {changes_made}")

    # Handle the case when no changes are detected
    if not updated:
        logger.info("No changes detected - returning existing article without error")
        return db_news

    # Commit changes to database
    try:
        db.commit()
        db.refresh(db_news)
        logger.info(f"Successfully updated article ID {news_id}. Changes: {', '.join(changes_made)}")
        
        # Send email notifications for updates (only if significant changes)
        if updated and any(field in changes_made for field in ["title", "content", "featured_image"]):
            try:
                # Get all active students
                students = db.query(StudentModel).filter(StudentModel.is_active == True).all()
                # Get all active subscribers
                subscribers = db.query(Subscriber).filter(Subscriber.is_active == True).all()
                
                # Combine all recipients (avoid duplicates)
                all_recipients = []
                student_emails = set()
                
                # Add students
                for student in students:
                    if student.email not in student_emails:
                        all_recipients.append({
                            "email": student.email,
                            "name": student.full_name,
                            "type": "student"
                        })
                        student_emails.add(student.email)
                
                # Add subscribers (excluding students)
                for subscriber in subscribers:
                    if subscriber.email not in student_emails:
                        all_recipients.append({
                            "email": subscriber.email,
                            "name": "Subscriber",
                            "type": "subscriber"
                        })
                
                # Send emails
                publisher_name = f"{current_user.first_name} {current_user.last_name}"
                successful_emails = 0
                failed_emails = 0
                
                for recipient in all_recipients:
                    try:
                        success = send_news_notification_email(
                            email=recipient["email"],
                            title=db_news.title,
                            content=db_news.content,
                            image_url=db_news.featured_image_url,
                            publisher_name=publisher_name
                        )
                        if success:
                            successful_emails += 1
                        else:
                            failed_emails += 1
                    except Exception as e:
                        logger.error(f"Failed to send news update email to {recipient['email']}: {str(e)}")
                        failed_emails += 1
                
                logger.info(f"News update notification emails sent: {successful_emails} successful, {failed_emails} failed")
                
            except Exception as e:
                logger.error(f"Error sending news update notification emails: {str(e)}")
                # Don't fail the news update if email sending fails
        
    except IntegrityError as e:
        db.rollback()
        logger.error(f"Database integrity error: {e}")
        raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error during commit: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

    return db_news

@router.delete("/{news_id}")
def delete_news(
    news_id: int,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Delete a news article"""
    logger.debug(f"Deleting article ID: {news_id} by user: {current_user.id}")
    db_news = db.query(NewsModel).filter(NewsModel.id == news_id).first()
    if db_news is None:
        logger.warning(f"Article ID {news_id} not found")
        raise HTTPException(status_code=404, detail="News not found")
    
    if db_news.featured_image_url:
        logger.debug(f"Deleting image: {db_news.featured_image_url}")
        s3_service.delete_image(db_news.featured_image_url)
    
    db.delete(db_news)
    db.commit()
    logger.info(f"Deleted article ID: {news_id}")
    return {"detail": "News deleted"}

@router.get("/my/articles", response_model=list[NewsSchema])
def get_my_articles(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Get news articles published by the current admin"""
    logger.debug(f"Fetching articles for user: {current_user.id}, skip={skip}, limit={limit}")
    return db.query(NewsModel).filter(NewsModel.publisher_id == current_user.id).order_by(NewsModel.published_at.desc()).offset(skip).limit(limit).all()

# Assuming s3_service.py is a separate file; here's the updated version with image optimization
# s3_service.py content (updated):

BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
REGION = os.getenv("AWS_REGION")
s3_client = boto3.client('s3')  # Assuming AWS credentials are configured

def upload_image(file: UploadFile) -> str:
    """Upload and optimize image to S3"""
    # Read the file
    contents = file.file.read()
    image = Image.open(io.BytesIO(contents))
    
    # Resize to 1200x630 (preserve aspect ratio, crop/pad if needed)
    image.thumbnail((1200, 630))  # Resize proportionally
    if image.width < 1200 or image.height < 630:
        # Pad to exact size with white background
        new_image = Image.new("RGB", (1200, 630), (255, 255, 255))
        new_image.paste(image, ((1200 - image.width) // 2, (630 - image.height) // 2))
        image = new_image
    
    # Compress (aim for <300KB)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=85, optimize=True)  # Adjust quality if needed
    output.seek(0)
    
    # Generate unique key
    key = f"news/images/{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename.replace(' ', '_')}"
    
    # Upload to S3
    s3_client.upload_fileobj(
        output,
        BUCKET_NAME,
        key,
        ExtraArgs={'ContentType': 'image/jpeg', 'ACL': 'public-read'}  # Make public
    )
    
    return f"https://{BUCKET_NAME}.s3.{REGION}.amazonaws.com/{key}"

def delete_image(image_url: str):
    """Delete image from S3"""
    key = image_url.split(f"https://{BUCKET_NAME}.s3.{REGION}.amazonaws.com/")[1]
    s3_client.delete_object(Bucket=BUCKET_NAME, Key=key)