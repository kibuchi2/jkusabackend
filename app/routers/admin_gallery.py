from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from jose import JWTError, jwt
from app.database import get_db

# Import models and schemas
from app.models.gallery import Gallery as GalleryModel, GalleryCategory
from app.schemas.gallery import (
    Gallery as GallerySchema,
    GalleryCreate,
    GalleryUpdate,
    GalleryReorderRequest,
    CategoryGalleryResponse,
    GallerySummary
)

from app.models.admin import Admin
from app.services.s3_service import s3_service
from datetime import datetime
from typing import Optional, List
import logging
import os
from dotenv import load_dotenv

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
router = APIRouter(prefix="/admin/gallery", tags=["admin_gallery"])

# Public router for unauthenticated access
public_gallery_router = APIRouter(prefix="/gallery", tags=["public_gallery"])

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

# ==================== ADMIN ENDPOINTS ====================

@router.post("/", response_model=GallerySchema)
def create_gallery(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    category: str = Form(...),
    year: Optional[str] = Form(None),
    display_order: int = Form(0),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Create a new gallery item with image (Admin only)"""
    logger.debug(f"Creating gallery item by user: {current_user.id}")
    logger.debug(f"Received category: '{category}'")
    
    try:
        # Convert to uppercase and validate enum value
        category_upper = category.upper()
        logger.debug(f"Converting to uppercase - category: '{category_upper}'")
        category_enum = GalleryCategory(category_upper)
        logger.debug(f"Enum validation successful - category: {category_enum}")
    except ValueError as e:
        logger.error(f"Invalid enum value: {e}")
        logger.error(f"Available category values: {[c.value for c in GalleryCategory]}")
        raise HTTPException(status_code=400, detail=f"Invalid category value: {str(e)}")
    
    # Validate and upload image
    if not image.content_type.startswith('image/'):
        logger.error(f"Invalid file type: {image.content_type}")
        raise HTTPException(status_code=400, detail="File must be an image")
    if image.size > 10 * 1024 * 1024:  # 10MB limit for gallery images
        logger.error(f"Image too large: {image.size} bytes")
        raise HTTPException(status_code=400, detail="Image must be less than 10MB")
    
    image_url = s3_service.upload_image(image, "gallery")
    if not image_url:
        logger.error("Failed to upload image to S3")
        raise HTTPException(status_code=500, detail="Failed to upload image")
    
    # Get the next display order for this category if not provided
    if display_order == 0:
        max_order = db.query(GalleryModel).filter(
            GalleryModel.category == category_enum
        ).count()
        display_order = max_order + 1
    
    # Create gallery item
    db_gallery = GalleryModel(
        title=title.strip(),
        description=description.strip() if description else None,
        category=category_enum,
        year=year.strip() if year else None,
        display_order=display_order,
        image_url=image_url
    )
    
    try:
        db.add(db_gallery)
        db.commit()
        db.refresh(db_gallery)
        logger.info(f"Created gallery item ID {db_gallery.id}: {db_gallery.title}")
    except IntegrityError as e:
        db.rollback()
        # Delete uploaded image if database insertion fails
        s3_service.delete_image(image_url)
        logger.error(f"Database integrity error: {e}")
        raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")
    except Exception as e:
        db.rollback()
        # Delete uploaded image if database insertion fails
        s3_service.delete_image(image_url)
        logger.error(f"Unexpected error during creation: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
    
    return db_gallery

@router.get("/", response_model=List[GallerySchema])
def read_gallery_list(
    category: Optional[str] = None,
    year: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Get list of gallery items with optional filtering (Admin only)"""
    logger.debug(f"Fetching gallery list: category={category}, year={year}")
    
    query = db.query(GalleryModel)
    
    # Apply filters
    if category:
        try:
            category_upper = category.upper()
            category_enum = GalleryCategory(category_upper)
            query = query.filter(GalleryModel.category == category_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid category value")
    
    if year:
        query = query.filter(GalleryModel.year == year)
    
    # Order by display_order and created_at
    query = query.order_by(GalleryModel.display_order, GalleryModel.created_at.desc())
    
    return query.offset(skip).limit(limit).all()

@router.get("/summary", response_model=GallerySummary)
def get_gallery_summary(
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Get gallery summary statistics (Admin only)"""
    logger.debug("Fetching gallery summary")
    
    try:
        total_count = db.query(GalleryModel).count()
        
        # Count by category
        categories = {}
        for cat in GalleryCategory:
            count = db.query(GalleryModel).filter(GalleryModel.category == cat).count()
            categories[cat.value] = count
        
        # Get all available years
        years = db.query(GalleryModel.year).distinct().all()
        years_list = sorted([year[0] for year in years if year[0]], reverse=True)
        
        return GallerySummary(
            total_count=total_count,
            categories=categories,
            years=years_list
        )
    except Exception as e:
        logger.error(f"Error fetching gallery summary: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/by-category")
def get_gallery_by_category(
    year: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Get gallery items grouped by category (Admin only)"""
    logger.debug(f"Fetching gallery by category for year: {year}")
    
    query = db.query(GalleryModel)
    if year:
        query = query.filter(GalleryModel.year == year)
    
    # Order by category and display_order
    items = query.order_by(GalleryModel.category, GalleryModel.display_order).all()
    
    # Group by category
    grouped = {}
    for item in items:
        category_key = item.category.value
        if category_key not in grouped:
            grouped[category_key] = []
        grouped[category_key].append(item)
    
    return grouped

@router.get("/{gallery_id}", response_model=GallerySchema)
def read_gallery(
    gallery_id: int,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Get a specific gallery item (Admin only)"""
    logger.debug(f"Fetching gallery item ID: {gallery_id}")
    db_gallery = db.query(GalleryModel).filter(GalleryModel.id == gallery_id).first()
    if db_gallery is None:
        logger.warning(f"Gallery item ID {gallery_id} not found")
        raise HTTPException(status_code=404, detail="Gallery item not found")
    return db_gallery

@router.put("/{gallery_id}", response_model=GallerySchema)
def update_gallery(
    gallery_id: int,
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    year: Optional[str] = Form(None),
    display_order: Optional[int] = Form(None),
    image: Optional[UploadFile] = File(None),
    remove_image: Optional[str] = Form("false"),
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Update a gallery item (Admin only)"""
    logger.debug(f"Updating gallery item ID: {gallery_id} by user: {current_user.id}")
    
    db_gallery = db.query(GalleryModel).filter(GalleryModel.id == gallery_id).first()
    if db_gallery is None:
        logger.warning(f"Gallery item ID {gallery_id} not found")
        raise HTTPException(status_code=404, detail="Gallery item not found")

    updated = False
    changes_made = []

    # Update fields if provided
    if title is not None and title.strip() != db_gallery.title:
        db_gallery.title = title.strip()
        updated = True
        changes_made.append("title")

    if description is not None and description.strip() != (db_gallery.description or ""):
        db_gallery.description = description.strip() if description.strip() else None
        updated = True
        changes_made.append("description")

    if category is not None:
        try:
            category_upper = category.upper()
            category_enum = GalleryCategory(category_upper)
            if category_enum != db_gallery.category:
                db_gallery.category = category_enum
                updated = True
                changes_made.append("category")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid category value")

    if year is not None and year.strip() != (db_gallery.year or ""):
        db_gallery.year = year.strip() if year.strip() else None
        updated = True
        changes_made.append("year")

    if display_order is not None and display_order != db_gallery.display_order:
        db_gallery.display_order = display_order
        updated = True
        changes_made.append("display_order")

    # Handle image update or removal
    if image:
        if not image.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="File must be an image")
        if image.size > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image must be less than 10MB")
        
        # Delete old image
        if db_gallery.image_url:
            s3_service.delete_image(db_gallery.image_url)
        
        new_image_url = s3_service.upload_image(image, "gallery")
        if not new_image_url:
            raise HTTPException(status_code=500, detail="Failed to upload image")
        
        db_gallery.image_url = new_image_url
        updated = True
        changes_made.append("image")
        
    elif remove_image == "true" and db_gallery.image_url:
        # Cannot remove image entirely - it's required
        raise HTTPException(status_code=400, detail="Gallery items must have an image. Upload a new image instead.")

    if not updated:
        logger.info("No changes detected - returning existing gallery item")
        return db_gallery

    try:
        db.commit()
        db.refresh(db_gallery)
        logger.info(f"Successfully updated gallery item ID {gallery_id}. Changes: {', '.join(changes_made)}")
    except IntegrityError as e:
        db.rollback()
        logger.error(f"Database integrity error: {e}")
        raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

    return db_gallery

@router.put("/reorder")
def reorder_gallery(
    request: GalleryReorderRequest,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Reorder gallery items (for drag-and-drop functionality) (Admin only)"""
    logger.debug(f"Reordering gallery items by user: {current_user.id}")
    
    try:
        for item in request.gallery_items:
            gallery_id = item['id']
            new_order = item['display_order']
            
            db_gallery = db.query(GalleryModel).filter(GalleryModel.id == gallery_id).first()
            if db_gallery:
                db_gallery.display_order = new_order
                logger.debug(f"Updated gallery ID {gallery_id} to order {new_order}")
        
        db.commit()
        logger.info(f"Successfully reordered {len(request.gallery_items)} gallery items")
        return {"detail": "Gallery items reordered successfully"}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error reordering gallery items: {e}")
        raise HTTPException(status_code=500, detail="Failed to reorder gallery items")

@router.delete("/{gallery_id}")
def delete_gallery(
    gallery_id: int,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Delete a gallery item (Admin only)"""
    logger.debug(f"Deleting gallery item ID: {gallery_id} by user: {current_user.id}")
    
    db_gallery = db.query(GalleryModel).filter(GalleryModel.id == gallery_id).first()
    if db_gallery is None:
        logger.warning(f"Gallery item ID {gallery_id} not found")
        raise HTTPException(status_code=404, detail="Gallery item not found")
    
    # Delete associated image
    if db_gallery.image_url:
        logger.debug(f"Deleting image: {db_gallery.image_url}")
        s3_service.delete_image(db_gallery.image_url)
    
    db.delete(db_gallery)
    db.commit()
    logger.info(f"Deleted gallery item ID: {gallery_id}")
    return {"detail": "Gallery item deleted"}

@router.get("/years/available")
def get_available_years(
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Get all available years (Admin only)"""
    logger.debug("Fetching available years")
    try:
        years = db.query(GalleryModel.year).distinct().all()
        years_list = sorted([year[0] for year in years if year[0]], reverse=True)
        logger.info(f"Retrieved {len(years_list)} available years")
        return {"years": years_list}
    except Exception as e:
        logger.error(f"Error fetching available years: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/enums/categories")
def get_gallery_categories(
    current_user: Admin = Depends(get_current_admin)
):
    """Get all available gallery categories (Admin only)"""
    logger.debug("Fetching gallery categories")
    try:
        categories = [
            {"value": category.value, "label": category.value.replace('_', ' ').title()} 
            for category in GalleryCategory
        ]
        logger.info(f"Retrieved {len(categories)} gallery categories")
        return {"categories": categories}
    except Exception as e:
        logger.error(f"Error fetching gallery categories: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ==================== PUBLIC ENDPOINTS ====================

@public_gallery_router.get("/", response_model=List[GallerySchema])
def read_public_gallery_list(
    category: Optional[str] = None,
    year: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get list of gallery items with optional filtering (Public access)"""
    logger.debug(f"Fetching public gallery list: category={category}, year={year}")
    
    try:
        query = db.query(GalleryModel)
        
        # Apply filters
        if category:
            try:
                category_upper = category.upper()
                category_enum = GalleryCategory(category_upper)
                query = query.filter(GalleryModel.category == category_enum)
            except ValueError:
                logger.error(f"Invalid category value: {category}")
                raise HTTPException(status_code=400, detail="Invalid category value")
        
        if year:
            query = query.filter(GalleryModel.year == year)
        
        # Order by display_order and created_at
        query = query.order_by(GalleryModel.display_order, GalleryModel.created_at.desc())
        
        items = query.offset(skip).limit(limit).all()
        logger.info(f"Retrieved {len(items)} public gallery items")
        return items
    except Exception as e:
        logger.error(f"Error fetching public gallery items: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@public_gallery_router.get("/by-category")
def get_public_gallery_by_category(
    year: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get gallery items grouped by category (Public access)"""
    logger.debug(f"Fetching public gallery by category for year: {year}")
    
    try:
        query = db.query(GalleryModel)
        if year:
            query = query.filter(GalleryModel.year == year)
        
        # Order by category and display_order
        items = query.order_by(GalleryModel.category, GalleryModel.display_order).all()
        
        # Group by category
        grouped = {}
        for item in items:
            category_key = item.category.value
            if category_key not in grouped:
                grouped[category_key] = []
            
            item_data = {
                "id": item.id,
                "title": item.title,
                "description": item.description,
                "image_url": item.image_url,
                "category": item.category.value,
                "year": item.year,
                "display_order": item.display_order,
                "created_at": item.created_at
            }
            grouped[category_key].append(item_data)
        
        logger.info(f"Retrieved public gallery grouped by category for year: {year}")
        return grouped
    except Exception as e:
        logger.error(f"Error fetching public gallery by category: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@public_gallery_router.get("/{gallery_id}", response_model=GallerySchema)
def read_public_gallery(
    gallery_id: int,
    db: Session = Depends(get_db)
):
    """Get a specific gallery item (Public access)"""
    logger.debug(f"Fetching public gallery item ID: {gallery_id}")
    try:
        db_gallery = db.query(GalleryModel).filter(GalleryModel.id == gallery_id).first()
        if db_gallery is None:
            logger.warning(f"Gallery item ID {gallery_id} not found")
            raise HTTPException(status_code=404, detail="Gallery item not found")
        logger.info(f"Retrieved public gallery item ID: {gallery_id}")
        return db_gallery
    except Exception as e:
        logger.error(f"Error fetching public gallery item ID {gallery_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@public_gallery_router.get("/years/available")
def get_public_available_years(
    db: Session = Depends(get_db)
):
    """Get all available years (Public access)"""
    logger.debug("Fetching public available years")
    try:
        years = db.query(GalleryModel.year).distinct().all()
        years_list = sorted([year[0] for year in years if year[0]], reverse=True)
        logger.info(f"Retrieved {len(years_list)} available years")
        return {"years": years_list}
    except Exception as e:
        logger.error(f"Error fetching public available years: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@public_gallery_router.get("/enums/categories")
def get_public_gallery_categories():
    """Get all available gallery categories (Public access)"""
    logger.debug("Fetching public gallery categories")
    try:
        categories = [
            {"value": category.value, "label": category.value.replace('_', ' ').title()} 
            for category in GalleryCategory
        ]
        logger.info(f"Retrieved {len(categories)} gallery categories")
        return {"categories": categories}
    except Exception as e:
        logger.error(f"Error fetching public gallery categories: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")