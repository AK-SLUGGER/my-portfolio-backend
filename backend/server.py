from fastapi import FastAPI, APIRouter, HTTPException, Depends, File, UploadFile
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import httpx
import base64
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr, ConfigDict
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone
import bcrypt

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
# Note: Use MONGO_URL as required by your environment
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ.get('DB_NAME', 'test')]

app = FastAPI()
api_router = APIRouter(prefix="/api")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Formspree configuration
FORMSPREE_FORM_ID = os.environ.get('FORMSPREE_FORM_ID', 'xpzbbgrn')

# Static files directory for uploads
UPLOAD_DIR = ROOT_DIR / 'uploads'
UPLOAD_DIR.mkdir(exist_ok=True)

# Mount static files
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

# ============== Models ==============

class ContactFormSubmission(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    message: str = Field(..., min_length=10, max_length=5000)

class LoginRequest(BaseModel):
    email: str
    password: str

class Experience(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    company: str
    location: str
    period: str
    current: bool = False
    description: List[str] = []

class Recommendation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    role: str
    text: str
    initials: str

class Certification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    icon: str
    title: str
    issuer: str
    period: str

class ThemeSettings(BaseModel):
    primaryColor: str = "#0066cc"
    accentColor: str = "#00bcd4"
    backgroundColor: str = "#0a0a0a"
    textColor: str = "#ffffff"
    displayFont: str = "Space Grotesk"
    bodyFont: str = "Inter"
    fontSize: int = 16
    cardStyle: str = "rounded"

class HeroStats(BaseModel):
    yearsExp: str = "5+"
    leadTimeCut: str = "53%"
    teamLed: str = "150+"

class ProfileData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    heroStats: Optional[HeroStats] = None
    heroSubtitle: Optional[str] = None
    experiences: Optional[List[Experience]] = None
    recommendations: Optional[List[Recommendation]] = None
    certifications: Optional[List[Certification]] = None
    theme: Optional[ThemeSettings] = None
    profileImage: Optional[str] = None
    aboutText: Optional[str] = None
    name: Optional[str] = None
    title: Optional[str] = None
    resumeUrl: Optional[str] = None

class FormspreeConfig(BaseModel):
    formId: str

# ============== Auth Helpers (UPDATED) ==============

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "raoabhi001@gmail.com")
# Fallback to default if variable is missing to prevent crash
RAW_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Abhishek@123")

async def get_or_create_admin():
    """Ensure admin user exists and matches the password in Railway Variables"""
    # Create a fresh hash of whatever is currently in the environment variables
    current_password_hash = bcrypt.hashpw(RAW_PASSWORD.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    admin = await db.users.find_one({"email": ADMIN_EMAIL})
    
    if not admin:
        admin_doc = {
            "id": str(uuid.uuid4()),
            "email": ADMIN_EMAIL,
            "password_hash": current_password_hash,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.users.insert_one(admin_doc)
        logger.info(f"Admin user created: {ADMIN_EMAIL}")
    else:
        # Update existing user password to match CURRENT Railway variable
        await db.users.update_one(
            {"email": ADMIN_EMAIL},
            {"$set": {"password_hash": current_password_hash}}
        )
        logger.info(f"Admin user password synced with environment variables")
    return admin

# ============== Routes ==============

@api_router.get("/")
async def root():
    return {"message": "Portfolio API running"}

@api_router.post("/contact/submit")
async def submit_contact_form(submission: ContactFormSubmission):
    try:
        contact_doc = {
            "id": str(uuid.uuid4()),
            **submission.model_dump(),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "status": "received"
        }
        await db.contact_submissions.insert_one(contact_doc)
        logger.info(f"Contact form saved from {submission.email}")
        
        if FORMSPREE_FORM_ID and FORMSPREE_FORM_ID != "YOUR_FORMSPREE_ID":
            try:
                url = f"https://formspree.io/f/{FORMSPREE_FORM_ID}"
                form_data = {
                    "name": submission.name,
                    "email": submission.email,
                    "message": submission.message,
                    "_replyto": submission.email,
                    "_subject": f"Portfolio Contact: {submission.name}"
                }
                
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.post(
                        url,
                        data=form_data,
                        headers={"Accept": "application/json"},
                        timeout=10.0
                    )
                    
                    if response.status_code in [200, 201, 302]:
                        await db.contact_submissions.update_one(
                            {"id": contact_doc["id"]},
                            {"$set": {"status": "sent_via_formspree"}}
                        )
                        logger.info(f"Contact form sent via Formspree from {submission.email}")
            except Exception as e:
                logger.warning(f"Formspree submission failed: {str(e)}")
        
        return {
            "success": True,
            "message": "Thank you for your message! I'll get back to you soon."
        }
    except Exception as e:
        logger.error(f"Contact form error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to process form submission")

@api_router.post("/auth/login")
async def login(request: LoginRequest):
    """Admin login"""
    # Ensure admin is initialized/updated
    await get_or_create_admin()
    
    user = await db.users.find_one({"email": request.email})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Check typed password against the hash in database
    if not bcrypt.checkpw(request.password.encode('utf-8'), user['password_hash'].encode('utf-8')):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    logger.info(f"Admin login successful: {request.email}")
    return {
        "success": True,
        "email": user['email'],
        "token": str(uuid.uuid4())
    }

@api_router.get("/profile")
async def get_profile():
    profile = await db.profile.find_one({"type": "main"}, {"_id": 0})
    if not profile:
        return get_default_profile()
    return profile

@api_router.post("/profile")
async def save_profile(data: ProfileData):
    try:
        profile_dict = data.model_dump(exclude_none=True)
        profile_dict["type"] = "main"
        profile_dict["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        existing = await db.profile.find_one({"type": "main"}, {"_id": 0})
        if existing:
            for key in ["experiences", "recommendations", "certifications"]:
                if key not in profile_dict or (isinstance(profile_dict.get(key), list) and len(profile_dict[key]) == 0):
                    if key in existing and len(existing[key]) > 0:
                        profile_dict[key] = existing[key]
        
        await db.profile.update_one(
            {"type": "main"},
            {"$set": profile_dict},
            upsert=True
        )
        
        logger.info("Profile data saved successfully")
        return {"success": True, "message": "Profile saved successfully"}
    except Exception as e:
        logger.error(f"Profile save error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to save profile")

@api_router.post("/upload/image")
async def upload_image(file: UploadFile = File(...)):
    try:
        allowed_types = ["image/jpeg", "image/png", "image/webp"]
        if file.content_type not in allowed_types:
            raise HTTPException(status_code=400, detail="Invalid file type.")
        
        contents = await file.read()
        if len(contents) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large.")
        
        base64_image = base64.b64encode(contents).decode('utf-8')
        data_url = f"data:{file.content_type};base64,{base64_image}"
        
        return {"success": True, "imageUrl": data_url}
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to upload image")

@api_router.post("/settings/formspree")
async def update_formspree(config: FormspreeConfig):
    global FORMSPREE_FORM_ID
    FORMSPREE_FORM_ID = config.formId
    await db.settings.update_one(
        {"type": "formspree"},
        {"$set": {"formId": config.formId}},
        upsert=True
    )
    return {"success": True, "message": "Formspree ID updated"}

@api_router.get("/contact/submissions")
async def get_contact_submissions():
    submissions = await db.contact_submissions.find({}, {"_id": 0}).sort("submitted_at", -1).to_list(100)
    return {"submissions": submissions}

@api_router.post("/upload/resume")
async def upload_resume(file: UploadFile = File(...)):
    try:
        if file.content_type != "application/pdf":
            raise HTTPException(status_code=400, detail="Only PDF files allowed.")
        
        contents = await file.read()
        base64_pdf = base64.b64encode(contents).decode('utf-8')
        data_url = f"data:{file.content_type};base64,{base64_pdf}"
        
        return {"success": True, "resumeUrl": data_url}
    except Exception as e:
        logger.error(f"Resume upload error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to upload resume")

def get_default_profile():
    return {
        "name": "Abhishek Yadav",
        "title": "Supply Chain & Operations Manager",
        "heroStats": {"yearsExp": "5+", "leadTimeCut": "53%", "teamLed": "150+"},
        "heroSubtitle": "MBA in SCM | SAP S/4HANA | WMS · Last Mile · Cross-Border | Prompt Engineering · Vibe Coding",
        "aboutText": "With 5+ years of progressive experience across Germany and India...",
        "experiences": [
            {
                "id": "1",
                "title": "Operations Manager",
                "company": "RIVAFY (Connect) Germany GmbH",
                "location": "Konstanz, Germany",
                "period": "Jan 2025 – Present",
                "current": True,
                "description": ["Cut product delivery lead time by 53%..."]
            }
        ],
        "recommendations": [],
        "certifications": [],
        "theme": {
            "primaryColor": "#0ea5e9",
            "accentColor": "#22d3ee",
            "backgroundColor": "#0a0a0a",
            "textColor": "#ffffff",
            "displayFont": "Space Grotesk",
            "bodyFont": "Inter",
            "fontSize": 16,
            "cardStyle": "rounded"
        },
        "profileImage": "https://abhishekyadav.de/Image_Abhishek.png",
        "resumeUrl": ""
    }

# Include router
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    await get_or_create_admin()
    logger.info("Application started")

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
