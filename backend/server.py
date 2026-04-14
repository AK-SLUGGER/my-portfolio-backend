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
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

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

# ============== Auth Helpers ==============

ADMIN_EMAIL = "raoabhi001@gmail.com"
ADMIN_PASSWORD_HASH = bcrypt.hashpw("Abhishek@123".encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

async def get_or_create_admin():
    """Ensure admin user exists in database"""
    admin = await db.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0})
    if not admin:
        admin_doc = {
            "id": str(uuid.uuid4()),
            "email": ADMIN_EMAIL,
            "password_hash": ADMIN_PASSWORD_HASH,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.users.insert_one(admin_doc)
        logger.info(f"Admin user created: {ADMIN_EMAIL}")
    return admin

# ============== Routes ==============

@api_router.get("/")
async def root():
    return {"message": "Portfolio API running"}

@api_router.post("/contact/submit")
async def submit_contact_form(submission: ContactFormSubmission):
    """Submit contact form - saves to database and attempts Formspree submission"""
    try:
        # Always save to database first
        contact_doc = {
            "id": str(uuid.uuid4()),
            **submission.model_dump(),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "status": "received"
        }
        await db.contact_submissions.insert_one(contact_doc)
        logger.info(f"Contact form saved from {submission.email}")
        
        # Try Formspree if configured
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
    await get_or_create_admin()
    
    user = await db.users.find_one({"email": request.email}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not bcrypt.checkpw(request.password.encode('utf-8'), user['password_hash'].encode('utf-8')):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    logger.info(f"Admin login successful: {request.email}")
    return {
        "success": True,
        "email": user['email'],
        "token": str(uuid.uuid4())  # Simple session token
    }

@api_router.get("/profile")
async def get_profile():
    """Get profile data"""
    profile = await db.profile.find_one({"type": "main"}, {"_id": 0})
    if not profile:
        # Return default profile data
        return get_default_profile()
    return profile

@api_router.post("/profile")
async def save_profile(data: ProfileData):
    """Save profile data - merges with existing data"""
    try:
        profile_dict = data.model_dump(exclude_none=True)
        profile_dict["type"] = "main"
        profile_dict["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        # For list fields, only overwrite if the new data has items
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
    """Upload profile image"""
    try:
        # Validate file type
        allowed_types = ["image/jpeg", "image/png", "image/webp"]
        if file.content_type not in allowed_types:
            raise HTTPException(status_code=400, detail="Invalid file type. Only JPEG, PNG, WebP allowed.")
        
        # Read and encode as base64
        contents = await file.read()
        if len(contents) > 5 * 1024 * 1024:  # 5MB limit
            raise HTTPException(status_code=400, detail="File too large. Max 5MB.")
        
        base64_image = base64.b64encode(contents).decode('utf-8')
        data_url = f"data:{file.content_type};base64,{base64_image}"
        
        return {"success": True, "imageUrl": data_url}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to upload image")

@api_router.post("/settings/formspree")
async def update_formspree(config: FormspreeConfig):
    """Update Formspree form ID"""
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
    """Get all contact form submissions"""
    submissions = await db.contact_submissions.find({}, {"_id": 0}).sort("submitted_at", -1).to_list(100)
    return {"submissions": submissions}

@api_router.post("/upload/resume")
async def upload_resume(file: UploadFile = File(...)):
    """Upload resume PDF"""
    try:
        allowed_types = ["application/pdf"]
        if file.content_type not in allowed_types:
            raise HTTPException(status_code=400, detail="Only PDF files allowed.")
        
        contents = await file.read()
        if len(contents) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large. Max 10MB.")
        
        base64_pdf = base64.b64encode(contents).decode('utf-8')
        data_url = f"data:{file.content_type};base64,{base64_pdf}"
        
        return {"success": True, "resumeUrl": data_url}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resume upload error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to upload resume")

def get_default_profile():
    """Return default profile data for Abhishek Yadav"""
    return {
        "name": "Abhishek Yadav",
        "title": "Supply Chain & Operations Manager",
        "heroStats": {
            "yearsExp": "5+",
            "leadTimeCut": "53%",
            "teamLed": "150+"
        },
        "heroSubtitle": "MBA in SCM | SAP S/4HANA | WMS · Last Mile · Cross-Border | Prompt Engineering · Vibe Coding",
        "aboutText": "With 5+ years of progressive experience across Germany and India, I specialise in end-to-end supply chain operations — warehouse intralogistics, cross-border logistics, last-mile delivery, and e-commerce fulfilment.\n\nI lead daily UTR and OTR operations, oversee inbound/outbound logistics, handle escalations, and drive strategic improvements in supply chain performance. I've optimised first-to-last mile efficiency and led both CRM and Order Management teams to scale customer experience.\n\nBeyond core operations, I actively leverage AI through Prompt Engineering and Vibe Coding — building automation tools, MIS dashboards, and logistics workflows that make teams faster and smarter.",
        "experiences": [
            {
                "id": "1",
                "title": "Operations Manager",
                "company": "RIVAFY (Connect) Germany GmbH",
                "location": "Konstanz, Germany",
                "period": "Jan 2025 – Present",
                "current": True,
                "description": [
                    "Cut product delivery lead time by 53% (15 to 7 days) through end-to-end supply chain re-engineering.",
                    "Designed and deployed 5S methodology across all warehouse zones, eliminating unplanned operational stoppages.",
                    "Steered the full logistics lifecycle — first-mile, middle-mile, and last-mile — covering slot allocation and route scheduling.",
                    "Introduced proactive bottleneck detection, cutting daily operational delays by 30%.",
                    "Supervised Order Management team of 12 staff, ensuring accurate order execution across all channels.",
                    "Led CRM operations, reducing escalation-to-resolution time by 20% within three months."
                ]
            },
            {
                "id": "2",
                "title": "Working Student – Warehouse & Fulfilment",
                "company": "METAMORPH GmbH",
                "location": "Berlin, Germany",
                "period": "Sep 2024 – Jan 2025",
                "current": False,
                "description": [
                    "Executed e-commerce fulfilment for 500+ daily orders using JTL Warehouse Management System.",
                    "Improved inventory picking accuracy by 15% and reduced mis-shipment rate by 10%."
                ]
            },
            {
                "id": "3",
                "title": "Site Lead – Operations",
                "company": "Mahindra Logistics (Amazon Heavy & Bulky Sort Centre)",
                "location": "Gurgaon, India",
                "period": "Jan 2024 – May 2024",
                "current": False,
                "description": [
                    "Set up and operationalised a new Amazon Heavy & Bulky Sort Centre, building a 100-person team from scratch.",
                    "Applied Kaizen to double seller pickup points from 60 to 120 — recognised as a regional benchmark.",
                    "Managed daily MIS tracking for 120+ inbound and outbound vehicles with 100% route visibility.",
                    "Reduced return unsalability by 90% by redesigning reverse shipment processes.",
                    "Achieved 99% on-time pickup rate — highest-performing site regionally."
                ]
            },
            {
                "id": "4",
                "title": "Shift In-Charge – Sort Centre Operations",
                "company": "Mahindra Logistics Limited",
                "location": "Gurgaon, India",
                "period": "Jun 2021 – Dec 2023",
                "current": False,
                "description": [
                    "Coordinated daily dispatch of 90+ vehicles to 30+ North India distribution points within 12-hour windows.",
                    "Led 50-person shift team sustaining 99% sort centre productivity through performance coaching.",
                    "Processed 4,000+ Heavy & Bulky shipments during peak seasons with zero SLA breaches.",
                    "Promoted to Site Lead in 2.5 years for 25% improvement in operational excellence."
                ]
            },
            {
                "id": "5",
                "title": "Graduate Trainee Engineer",
                "company": "Honda Motorcycles & Scooters Pvt. Ltd.",
                "location": "Gurgaon, India",
                "period": "Jul 2017 – Jan 2018",
                "current": False,
                "description": [
                    "Completed a 6-month structured engineering training programme in automotive manufacturing, quality control systems, and lean production methodologies."
                ]
            }
        ],
        "recommendations": [
            {
                "id": "1",
                "name": "Anand Pareek",
                "role": "Entrepreneur · Senior colleague at RIVAFY",
                "text": "Abhishek is an exceptional operations expert and a dependable leader. During our time at RIVAFY (Connect) Germany GmbH, he proved his ability to transform strategy into tangible results. Whether tackling supply chain bottlenecks or overseeing complex transitions, his approach remained calm and grounded in data. His commitment to excellence is reflected in his track record of 99% on-time delivery. Abhishek is a Senior Operations leader who truly understands how to drive a business forward, and I recommend him without reservation.",
                "initials": "AP"
            },
            {
                "id": "2",
                "name": "Cornel Bösch",
                "role": "MSc ETH, MBA · Senior colleague at RIVAFY",
                "text": "I had the opportunity to work with Abhishek during a very challenging transition period at RIVAFY. I was impressed not only by his deep operational expertise, but also by his professionalism, reliability, and integrity. Abhishek consistently went above and beyond his responsibilities, providing critical support in navigating complex administrative and legal processes during the company's restructuring. He became a key stabilising force for the team.",
                "initials": "CB"
            },
            {
                "id": "3",
                "name": "Major Sukhwinder Singh Khera",
                "role": "Amazon / MDI / Indian Army / Reliance · Colleague",
                "text": "Abhishek's warehouse management experience is impressive, with remarkable analytical skills to tackle complex operational issues. Detail-oriented and knowledgeable about logistics and supply chain principles, he drives business growth through data-driven insights. He collaborates effectively with cross-functional teams and communicates well, making him invaluable. Dedicated, hardworking, and passionate, he's poised to excel in any role.",
                "initials": "SK"
            },
            {
                "id": "4",
                "name": "Dhruv Singh",
                "role": "Operations & Supply Chain Planning · Ex-Amazon",
                "text": "I've had the chance to work closely with Abhishek, and one thing that always stood out to me was his strong desire to learn, grow, and help others do the same. He's not someone who only focuses on his own work — he spends a lot of time supporting his team, especially the ground staff. He works side by side with them, guiding them on how to improve processes, which has made the overall work smoother and with fewer mistakes.",
                "initials": "DS"
            },
            {
                "id": "5",
                "name": "Prof. Dr. Thomas Bolz",
                "role": "AI in Healthcare & Education · MBA Thesis Supervisor",
                "text": "It is with great pleasure that I recommend Abhishek Yadav, whose Master's thesis I had the opportunity to supervise at IU International University of Applied Sciences. He approached his thesis with a high degree of diligence, intellectual curiosity, and independence. His critical thinking and problem-solving skills were consistently evident. What stood out was not only the quality of his analysis but also the clarity with which he communicated complex ideas. Abhishek is a motivated and dependable individual with a professional attitude and excellent interpersonal skills.",
                "initials": "TB"
            }
        ],
        "certifications": [
            {
                "id": "1",
                "icon": "clipboard-list",
                "title": "Business Processes in SAP S/4HANA – Sourcing & Procurement",
                "issuer": "SAP Learning",
                "period": "Jan–Feb 2026"
            },
            {
                "id": "2",
                "icon": "target",
                "title": "Six Sigma: Black Belt",
                "issuer": "LinkedIn Learning",
                "period": "Process Improvement & Quality Management"
            },
            {
                "id": "3",
                "icon": "bot",
                "title": "Prompt Engineering for Operations",
                "issuer": "Self-Directed AI Practice",
                "period": "2024–2025"
            }
        ],
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
        "resumeUrl": "https://customer-assets.emergentagent.com/job_portfolio-fix-36/artifacts/c97tsroa_Abhishek_Yadav_cv.pdf"
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
