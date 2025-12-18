from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_, or_
from jose import JWTError, jwt
from app.database import get_db

# Import models and schemas
from app.models.leadership import Leadership as LeadershipModel, CampusType, LeadershipCategory
from app.schemas.leadership import (
    Leadership as LeadershipSchema, 
    LeadershipCreate, 
    LeadershipUpdate,
    LeadershipReorderRequest,
    CampusLeadershipResponse,
    OrganizationalStructureResponse,
    LeadershipSummary
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
router = APIRouter(prefix="/admin/leadership", tags=["admin_leadership"])

# Public router for unauthenticated access
public_leadership_router = APIRouter(prefix="/leadership", tags=["public_leadership"])

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

@router.post("/", response_model=LeadershipSchema)
def create_leadership(
    name: str = Form(...),
    bio: str = Form(None),
    year_of_service: str = Form(...),
    campus: str = Form(...),
    category: str = Form(...),
    position_title: str = Form(...),
    school_name: str = Form(None),
    hall_name: str = Form(None),
    display_order: int = Form(0),
    profile_image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Create a new leadership profile with optional profile image (Admin only)"""
    logger.debug(f"Creating leadership profile by user: {current_user.id}")
    logger.debug(f"Received campus: '{campus}', category: '{category}'")
    
    try:
        # Convert to uppercase and validate enum values
        campus_upper = campus.upper()
        category_upper = category.upper()
        
        logger.debug(f"Converting to uppercase - campus: '{campus_upper}', category: '{category_upper}'")
        
        campus_enum = CampusType(campus_upper)
        category_enum = LeadershipCategory(category_upper)
        
        logger.debug(f"Enum validation successful - campus: {campus_enum}, category: {category_enum}")
    except ValueError as e:
        logger.error(f"Invalid enum value: {e}")
        logger.error(f"Available campus values: {[c.value for c in CampusType]}")
        logger.error(f"Available category values: {[c.value for c in LeadershipCategory]}")
        raise HTTPException(status_code=400, detail=f"Invalid enum value: {str(e)}")
    
    # Validate and upload profile image
    profile_image_url = None
    if profile_image:
        if not profile_image.content_type.startswith('image/'):
            logger.error(f"Invalid file type: {profile_image.content_type}")
            raise HTTPException(status_code=400, detail="File must be an image")
        if profile_image.size > 5 * 1024 * 1024:
            logger.error(f"Image too large: {profile_image.size} bytes")
            raise HTTPException(status_code=400, detail="Image must be less than 5MB")
        
        profile_image_url = s3_service.upload_image(profile_image, "leadership/profiles")
        if not profile_image_url:
            logger.error("Failed to upload image to S3")
            raise HTTPException(status_code=500, detail="Failed to upload image")
    
    # Get the next display order for this category if not provided
    if display_order == 0:
        max_order = db.query(LeadershipModel).filter(
            and_(LeadershipModel.campus == campus_enum, LeadershipModel.category == category_enum)
        ).count()
        display_order = max_order + 1
    
    # Create leadership profile
    db_leadership = LeadershipModel(
        name=name.strip(),
        bio=bio.strip() if bio else None,
        year_of_service=year_of_service.strip(),
        campus=campus_enum,
        category=category_enum,
        position_title=position_title.strip(),
        school_name=school_name.strip() if school_name else None,
        hall_name=hall_name.strip() if hall_name else None,
        display_order=display_order,
        profile_image_url=profile_image_url
    )
    
    try:
        db.add(db_leadership)
        db.commit()
        db.refresh(db_leadership)
        logger.info(f"Created leadership profile ID {db_leadership.id}: {db_leadership.name}")
    except IntegrityError as e:
        db.rollback()
        logger.error(f"Database integrity error: {e}")
        raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error during creation: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
    
    return db_leadership

@router.get("/", response_model=List[LeadershipSchema])
def read_leadership_list(
    campus: Optional[str] = None,
    category: Optional[str] = None,
    year_of_service: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Get list of leadership profiles with optional filtering (Admin only)"""
    logger.debug(f"Fetching leadership list: campus={campus}, category={category}, year={year_of_service}")
    
    query = db.query(LeadershipModel)
    
    # Apply filters
    if campus:
        try:
            campus_upper = campus.upper()
            campus_enum = CampusType(campus_upper)
            query = query.filter(LeadershipModel.campus == campus_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid campus value")
    
    if category:
        try:
            category_upper = category.upper()
            category_enum = LeadershipCategory(category_upper)
            query = query.filter(LeadershipModel.category == category_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid category value")
    
    if year_of_service:
        query = query.filter(LeadershipModel.year_of_service == year_of_service)
    
    # Order by display_order and created_at
    query = query.order_by(LeadershipModel.display_order, LeadershipModel.created_at)
    
    return query.offset(skip).limit(limit).all()

@public_leadership_router.get("/", response_model=List[LeadershipSchema])
def read_public_leadership_list(
    campus: Optional[str] = None,
    category: Optional[str] = None,
    year_of_service: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get list of leadership profiles with optional filtering (Public access)"""
    logger.debug(f"Fetching public leadership list: campus={campus}, category={category}, year={year_of_service}")
    
    try:
        query = db.query(LeadershipModel)
        
        # Apply filters
        if campus:
            try:
                campus_upper = campus.upper()
                campus_enum = CampusType(campus_upper)
                query = query.filter(LeadershipModel.campus == campus_enum)
            except ValueError:
                logger.error(f"Invalid campus value: {campus}")
                raise HTTPException(status_code=400, detail="Invalid campus value")
        
        if category:
            try:
                category_upper = category.upper()
                category_enum = LeadershipCategory(category_upper)
                query = query.filter(LeadershipModel.category == category_enum)
            except ValueError:
                logger.error(f"Invalid category value: {category}")
                raise HTTPException(status_code=400, detail="Invalid category value")
        
        if year_of_service:
            query = query.filter(LeadershipModel.year_of_service == year_of_service)
        
        # Order by display_order and created_at
        query = query.order_by(LeadershipModel.display_order, LeadershipModel.created_at)
        
        leaders = query.offset(skip).limit(limit).all()
        logger.info(f"Retrieved {len(leaders)} public leadership profiles")
        return leaders
    except Exception as e:
        logger.error(f"Error fetching public leadership profiles: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/organizational-structure")
def get_organizational_structure(
    year_of_service: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Get the complete organizational structure grouped by campus and category (Admin only)"""
    logger.debug(f"Fetching organizational structure for year: {year_of_service}")
    
    query = db.query(LeadershipModel)
    if year_of_service:
        query = query.filter(LeadershipModel.year_of_service == year_of_service)
    
    # Order by display_order
    leaders = query.order_by(LeadershipModel.campus, LeadershipModel.category, LeadershipModel.display_order).all()
    
    # Group by campus and category
    structure = {
        "main_campus": {},
        "karen_campus": {},
        "cbd_campus": {},
        "nakuru_campus": {},
        "mombasa_campus": {}
    }
    
    campus_mapping = {
        CampusType.MAIN: "main_campus",
        CampusType.KAREN: "karen_campus",
        CampusType.CBD: "cbd_campus",
        CampusType.NAKURU: "nakuru_campus",
        CampusType.MOMBASA: "mombasa_campus"
    }
    
    for leader in leaders:
        campus_key = campus_mapping.get(leader.campus)
        if campus_key:
            category_key = leader.category.value
            if category_key not in structure[campus_key]:
                structure[campus_key][category_key] = []
            
            leader_data = {
                "id": leader.id,
                "name": leader.name,
                "position_title": leader.position_title,
                "bio": leader.bio,
                "profile_image_url": leader.profile_image_url,
                "school_name": leader.school_name,
                "hall_name": leader.hall_name,
                "display_order": leader.display_order,
                "year_of_service": leader.year_of_service
            }
            structure[campus_key][category_key].append(leader_data)
    
    return structure

@public_leadership_router.get("/organizational-structure")
def get_public_organizational_structure(
    year_of_service: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get the complete organizational structure grouped by campus and category (Public access)"""
    logger.debug(f"Fetching public organizational structure for year: {year_of_service}")
    
    try:
        query = db.query(LeadershipModel)
        if year_of_service:
            query = query.filter(LeadershipModel.year_of_service == year_of_service)
        
        # Order by display_order
        leaders = query.order_by(LeadershipModel.campus, LeadershipModel.category, LeadershipModel.display_order).all()
        
        # Group by campus and category
        structure = {
            "main_campus": {},
            "karen_campus": {},
            "cbd_campus": {},
            "nakuru_campus": {},
            "mombasa_campus": {}
        }
        
        campus_mapping = {
            CampusType.MAIN: "main_campus",
            CampusType.KAREN: "karen_campus",
            CampusType.CBD: "cbd_campus",
            CampusType.NAKURU: "nakuru_campus",
            CampusType.MOMBASA: "mombasa_campus"
        }
        
        for leader in leaders:
            campus_key = campus_mapping.get(leader.campus)
            if campus_key:
                category_key = leader.category.value
                if category_key not in structure[campus_key]:
                    structure[campus_key][category_key] = []
                
                leader_data = {
                    "id": leader.id,
                    "name": leader.name,
                    "position_title": leader.position_title,
                    "bio": leader.bio,
                    "profile_image_url": leader.profile_image_url,
                    "school_name": leader.school_name,
                    "hall_name": leader.hall_name,
                    "display_order": leader.display_order,
                    "year_of_service": leader.year_of_service
                }
                structure[campus_key][category_key].append(leader_data)
        
        logger.info(f"Retrieved public organizational structure for year: {year_of_service}")
        return structure
    except Exception as e:
        logger.error(f"Error fetching public organizational structure: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/{leadership_id}", response_model=LeadershipSchema)
def read_leadership(
    leadership_id: int,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Get a specific leadership profile (Admin only)"""
    logger.debug(f"Fetching leadership profile ID: {leadership_id}")
    db_leadership = db.query(LeadershipModel).filter(LeadershipModel.id == leadership_id).first()
    if db_leadership is None:
        logger.warning(f"Leadership profile ID {leadership_id} not found")
        raise HTTPException(status_code=404, detail="Leadership profile not found")
    return db_leadership

@public_leadership_router.get("/{leadership_id}", response_model=LeadershipSchema)
def read_public_leadership(
    leadership_id: int,
    db: Session = Depends(get_db)
):
    """Get a specific leadership profile (Public access)"""
    logger.debug(f"Fetching public leadership profile ID: {leadership_id}")
    try:
        db_leadership = db.query(LeadershipModel).filter(LeadershipModel.id == leadership_id).first()
        if db_leadership is None:
            logger.warning(f"Leadership profile ID {leadership_id} not found")
            raise HTTPException(status_code=404, detail="Leadership profile not found")
        logger.info(f"Retrieved public leadership profile ID: {leadership_id}")
        return db_leadership
    except Exception as e:
        logger.error(f"Error fetching public leadership profile ID {leadership_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.put("/{leadership_id}", response_model=LeadershipSchema)
def update_leadership(
    leadership_id: int,
    name: Optional[str] = Form(None),
    bio: Optional[str] = Form(None),
    year_of_service: Optional[str] = Form(None),
    campus: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    position_title: Optional[str] = Form(None),
    school_name: Optional[str] = Form(None),
    hall_name: Optional[str] = Form(None),
    display_order: Optional[int] = Form(None),
    profile_image: Optional[UploadFile] = File(None),
    remove_image: Optional[str] = Form("false"),
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Update a leadership profile (Admin only)"""
    logger.debug(f"Updating leadership profile ID: {leadership_id} by user: {current_user.id}")
    
    db_leadership = db.query(LeadershipModel).filter(LeadershipModel.id == leadership_id).first()
    if db_leadership is None:
        logger.warning(f"Leadership profile ID {leadership_id} not found")
        raise HTTPException(status_code=404, detail="Leadership profile not found")

    updated = False
    changes_made = []

    # Update fields if provided
    if name is not None and name.strip() != db_leadership.name:
        db_leadership.name = name.strip()
        updated = True
        changes_made.append("name")

    if bio is not None and bio.strip() != (db_leadership.bio or ""):
        db_leadership.bio = bio.strip() if bio.strip() else None
        updated = True
        changes_made.append("bio")

    if year_of_service is not None and year_of_service.strip() != db_leadership.year_of_service:
        db_leadership.year_of_service = year_of_service.strip()
        updated = True
        changes_made.append("year_of_service")

    if campus is not None:
        try:
            campus_enum = CampusType(campus)
            if campus_enum != db_leadership.campus:
                db_leadership.campus = campus_enum
                updated = True
                changes_made.append("campus")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid campus value")

    if category is not None:
        try:
            category_enum = LeadershipCategory(category)
            if category_enum != db_leadership.category:
                db_leadership.category = category_enum
                updated = True
                changes_made.append("category")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid category value")

    if position_title is not None and position_title.strip() != db_leadership.position_title:
        db_leadership.position_title = position_title.strip()
        updated = True
        changes_made.append("position_title")

    if school_name is not None and school_name.strip() != (db_leadership.school_name or ""):
        db_leadership.school_name = school_name.strip() if school_name.strip() else None
        updated = True
        changes_made.append("school_name")

    if hall_name is not None and hall_name.strip() != (db_leadership.hall_name or ""):
        db_leadership.hall_name = hall_name.strip() if hall_name.strip() else None
        updated = True
        changes_made.append("hall_name")

    if display_order is not None and display_order != db_leadership.display_order:
        db_leadership.display_order = display_order
        updated = True
        changes_made.append("display_order")

    # Handle image update or removal
    if profile_image:
        if not profile_image.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="File must be an image")
        if profile_image.size > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image must be less than 5MB")
        
        # Delete old image if it exists
        if db_leadership.profile_image_url:
            s3_service.delete_image(db_leadership.profile_image_url)
        
        new_image_url = s3_service.upload_image(profile_image, "leadership/profiles")
        if not new_image_url:
            raise HTTPException(status_code=500, detail="Failed to upload image")
        
        db_leadership.profile_image_url = new_image_url
        updated = True
        changes_made.append("profile_image")
        
    elif remove_image == "true" and db_leadership.profile_image_url:
        s3_service.delete_image(db_leadership.profile_image_url)
        db_leadership.profile_image_url = None
        updated = True
        changes_made.append("removed_image")

    if not updated:
        logger.info("No changes detected - returning existing profile")
        return db_leadership

    try:
        db.commit()
        db.refresh(db_leadership)
        logger.info(f"Successfully updated leadership profile ID {leadership_id}. Changes: {', '.join(changes_made)}")
    except IntegrityError as e:
        db.rollback()
        logger.error(f"Database integrity error: {e}")
        raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

    return db_leadership

@router.put("/reorder")
def reorder_leadership(
    request: LeadershipReorderRequest,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Reorder leadership profiles (for drag-and-drop functionality) (Admin only)"""
    logger.debug(f"Reordering leadership profiles by user: {current_user.id}")
    
    try:
        for item in request.leadership_items:
            leadership_id = item['id']
            new_order = item['display_order']
            
            db_leadership = db.query(LeadershipModel).filter(LeadershipModel.id == leadership_id).first()
            if db_leadership:
                db_leadership.display_order = new_order
                logger.debug(f"Updated leadership ID {leadership_id} to order {new_order}")
        
        db.commit()
        logger.info(f"Successfully reordered {len(request.leadership_items)} leadership profiles")
        return {"detail": "Leadership profiles reordered successfully"}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error reordering leadership profiles: {e}")
        raise HTTPException(status_code=500, detail="Failed to reorder leadership profiles")

@router.delete("/{leadership_id}")
def delete_leadership(
    leadership_id: int,
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Delete a leadership profile (Admin only)"""
    logger.debug(f"Deleting leadership profile ID: {leadership_id} by user: {current_user.id}")
    
    db_leadership = db.query(LeadershipModel).filter(LeadershipModel.id == leadership_id).first()
    if db_leadership is None:
        logger.warning(f"Leadership profile ID {leadership_id} not found")
        raise HTTPException(status_code=404, detail="Leadership profile not found")
    
    # Delete associated image if it exists
    if db_leadership.profile_image_url:
        logger.debug(f"Deleting image: {db_leadership.profile_image_url}")
        s3_service.delete_image(db_leadership.profile_image_url)
    
    db.delete(db_leadership)
    db.commit()
    logger.info(f"Deleted leadership profile ID: {leadership_id}")
    return {"detail": "Leadership profile deleted"}

@router.get("/years/available")
def get_available_years(
    db: Session = Depends(get_db),
    current_user: Admin = Depends(get_current_admin)
):
    """Get all available years of service (Admin only)"""
    logger.debug("Fetching available years of service")
    try:
        years = db.query(LeadershipModel.year_of_service).distinct().all()
        logger.info(f"Retrieved {len(years)} available years")
        return {"years": [year[0] for year in years if year[0]]}
    except Exception as e:
        logger.error(f"Error fetching available years: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@public_leadership_router.get("/years/available")
def get_public_available_years(
    db: Session = Depends(get_db)
):
    """Get all available years of service (Public access)"""
    logger.debug("Fetching public available years of service")
    try:
        years = db.query(LeadershipModel.year_of_service).distinct().all()
        logger.info(f"Retrieved {len(years)} available years")
        return {"years": [year[0] for year in years if year[0]]}
    except Exception as e:
        logger.error(f"Error fetching public available years: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/enums/campus-types")
def get_campus_types():
    """Get all available campus types (Admin only)"""
    logger.debug("Fetching campus types")
    try:
        campus_types = [{"value": campus.value, "label": campus.value.replace('_', ' ').title()} for campus in CampusType]
        logger.info(f"Retrieved {len(campus_types)} campus types")
        return {"campus_types": campus_types}
    except Exception as e:
        logger.error(f"Error fetching campus types: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@public_leadership_router.get("/enums/campus-types")
def get_public_campus_types():
    """Get all available campus types (Public access)"""
    logger.debug("Fetching public campus types")
    try:
        campus_types = [{"value": campus.value, "label": campus.value.replace('_', ' ').title()} for campus in CampusType]
        logger.info(f"Retrieved {len(campus_types)} campus types")
        return {"campus_types": campus_types}
    except Exception as e:
        logger.error(f"Error fetching public campus types: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/enums/leadership-categories")
def get_leadership_categories():
    """Get all available leadership categories (Admin only)"""
    logger.debug("Fetching leadership categories")
    try:
        categories = [{"value": category.value, "label": category.value.replace('_', ' ').title()} for category in LeadershipCategory]
        logger.info(f"Retrieved {len(categories)} leadership categories")
        return {"categories": categories}
    except Exception as e:
        logger.error(f"Error fetching leadership categories: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@public_leadership_router.get("/enums/leadership-categories")
def get_public_leadership_categories():
    """Get all available leadership categories (Public access)"""
    logger.debug("Fetching public leadership categories")
    try:
        categories = [{"value": category.value, "label": category.value.replace('_', ' ').title()} for category in LeadershipCategory]
        logger.info(f"Retrieved {len(categories)} leadership categories")
        return {"categories": categories}
    except Exception as e:
        logger.error(f"Error fetching public leadership categories: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")