from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# IMPORTANT: Import all models BEFORE creating Base.metadata
from app.database import engine, Base
from app.models.admin import Admin
from app.models.admin_role import AdminRole
from app.models.activity import Activity
from app.models.leadership import Leadership, CampusType, LeadershipCategory
from app.models.gallery import Gallery, GalleryCategory
from app.models.event import Event
from app.models.resource import Resource
from app.models.club import Club
from app.models.student import student
from app.models.lost_id import LostID, IDType, IDStatus, Station
from app.models.subscriber import Subscriber
from app.models.registration import Form, FormField, FormCondition, FormSubmission

from app.routers import (
    user_auth, 
    admin_auth, 
    admin_roles,
    admin_announcement, 
    admin_leadership, 
    admin_event, 
    admin_news,
    admin_gallery,
    admin_resource,
    admin_activity,
    admin_club,
    students_sso,
    ai_assistant,
    lost_id,
    admin_subscriber,
    admin_students,
    admin_registrations,
    student_registrations
)
from app.routers.admin_announcement import public_router as public_announcement_router
from app.routers.admin_news import public_news_router
from app.routers.admin_event import public_event_router
from app.routers.admin_leadership import public_leadership_router
from app.routers.admin_gallery import public_gallery_router
from app.routers.admin_resource import public_resource_router
from app.routers.admin_activity import public_activity_router
from app.routers.admin_club import public_club_router
from app.routers.admin_subscriber import public_router as public_subscriber_router

import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI(title="JKUSA CMS Backend with AI Assistant & Registration System")

# Enable CORS
origins = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "https://digikenya.co.ke",
    "http://localhost:8081",
    "https://dashboard.jkusa.org",
    "https://jkusa.org",
    "https://portal.jkusa.org",
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:3007",
    "http://localhost:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom exception handler for HTTPException
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={
            "Access-Control-Allow-Origin": request.headers.get("Origin", "*"),
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        },
    )

# Custom exception handler for unhandled exceptions
@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
        headers={
            "Access-Control-Allow-Origin": request.headers.get("Origin", "*"),
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        },
    )

# Create database tables - ALL models must be imported above for this to work
Base.metadata.create_all(bind=engine)

# Include routers - Authentication & Core
app.include_router(user_auth.router)
app.include_router(admin_auth.router)
app.include_router(admin_roles.router)

# Include routers - Content Management (Admin & Public)
app.include_router(admin_announcement.router)
app.include_router(public_announcement_router)
app.include_router(admin_leadership.router)
app.include_router(public_leadership_router)
app.include_router(admin_event.router)
app.include_router(public_event_router)
app.include_router(admin_news.router)
app.include_router(public_news_router)
app.include_router(admin_gallery.router)
app.include_router(public_gallery_router)
app.include_router(admin_resource.router)
app.include_router(public_resource_router)
app.include_router(admin_activity.router)
app.include_router(public_activity_router)
app.include_router(admin_club.router)
app.include_router(public_club_router)

# Include routers - Subscribers & Students
app.include_router(students_sso.router)
app.include_router(admin_subscriber.router)
app.include_router(public_subscriber_router)
app.include_router(admin_students.router)

# Include routers - Registration System (NEW)
app.include_router(admin_registrations.router)
app.include_router(student_registrations.router)

# Include routers - Utilities
app.include_router(ai_assistant.router) 
app.include_router(lost_id.router)

@app.get("/")
def read_root():
    logger.debug("Root endpoint accessed")
    return {
        "message": "JKUSA CMS Backend with AI Assistant & Registration System is running.",
        "version": "2.0",
        "features": [
            "Content Management System",
            "Event Management",
            "Club Management",
            "AI-Powered Assistant for JKUAT/JKUSA Information",
            "Dynamic Registration System with Conditional Fields",
            "AI-Powered Form Analytics using Gemini"
        ],
        "documentation": "/docs",
        "openapi_schema": "/openapi.json"
    }

@app.get("/health")
def health_check():
    """Health check endpoint for load balancers and monitoring"""
    return {
        "status": "healthy",
        "service": "JKUSA CMS Backend",
        "timestamp": datetime.utcnow().isoformat()  # <-- datetime is used but not imported
    }