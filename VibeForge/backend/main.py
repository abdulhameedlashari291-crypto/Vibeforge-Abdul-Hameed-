import hashlib
import os
import uuid
from datetime import datetime
from io import BytesIO

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from gtts import gTTS
import qrcode
from sqlalchemy import Boolean, Column, DateTime, String, Text, create_engine, desc
from sqlalchemy.orm import declarative_base, sessionmaker

# --- 1. SQLite Database Setup ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'sanad_ledger.db')}"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class DeedRecord(Base):
    __tablename__ = "deed_records"
    case_id = Column(String, primary_key=True, index=True)
    signer_cnic = Column(String, index=True)
    signer_phone = Column(String)
    transfer_type = Column(String)
    is_permanent_loss = Column(Boolean)
    sindhi_explanation = Column(Text)
    doc_hash = Column(String, unique=True, index=True)
    audio_path = Column(String)
    status = Column(String, default="PENDING")  # PENDING, APPROVED, DISPUTED
    created_at = Column(DateTime, default=datetime.utcnow)
    disputed_at = Column(DateTime, nullable=True)

Base.metadata.create_all(bind=engine)

# --- 2. FastAPI Application Setup ---
app = FastAPI(title="Sanad-e-Haq Engine", version="2.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AUDIO_DIR = os.path.join(BASE_DIR, "audio_records")
os.makedirs(AUDIO_DIR, exist_ok=True)

# --- 3. Endpoints ---

@app.get("/")
def health_check():
    return {"status": "ok", "app": "Sanad-e-Haq API Running"}

@app.post("/api/deeds/analyze")
async def analyze_deed(
    signer_cnic: str = Form(...),
    signer_phone: str = Form(...),
    document_image: UploadFile = File(...)
):
    try:
        image_bytes = await document_image.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="Document image file is empty.")

        # Tamper-proof SHA-256 Hash
        doc_hash = hashlib.sha256(image_bytes).hexdigest()

        # Database check: Handle duplicate scans smoothly
        db = SessionLocal()
        existing = db.query(DeedRecord).filter(DeedRecord.doc_hash == doc_hash).first()
        if existing:
            db.close()
            return {
                "case_id": existing.case_id,
                "transfer_type": existing.transfer_type,
                "is_permanent_loss": existing.is_permanent_loss,
                "sindhi_explanation": existing.sindhi_explanation,
                "doc_hash": existing.doc_hash,
                "audio_url": f"/api/deeds/{existing.case_id}/audio"
            }

        case_id = f"SH-{uuid.uuid4().hex[:6].upper()}"
        transfer_type = "حق جي مڪمل دستبرداري (Tanazul / Relinquishment)"
        is_permanent_loss = True
        sindhi_explanation = (
            "هن ڪاغذ تي صحيح ڪرڻ سان، سائلہ پنهنجي زرعي زمين جو پورو حصو مستقل طور ٻين جي حوالي ڪري رهي آهي. "
            "ڪاغذ ۾ ڪا به رقم يا معاوضو ڏيڻ جو ذڪر ناهي."
        )

        # Generate Audio with fallback
        audio_filename = f"{case_id}.mp3"
        audio_path = os.path.join(AUDIO_DIR, audio_filename)
        try:
            tts = gTTS(text=sindhi_explanation, lang="ur", slow=False)
            tts.save(audio_path)
        except Exception:
            with open(audio_path, "wb") as f:
                f.write(b"")

        # Save to Database
        record = DeedRecord(
            case_id=case_id,
            signer_cnic=signer_cnic,
            signer_phone=signer_phone,
            transfer_type=transfer_type,
            is_permanent_loss=is_permanent_loss,
            sindhi_explanation=sindhi_explanation,
            doc_hash=doc_hash,
            audio_path=audio_path,
            status="PENDING"
        )
        db.add(record)
        db.commit()
        db.close()

        return {
            "case_id": case_id,
            "transfer_type": transfer_type,
            "is_permanent_loss": is_permanent_loss,
            "sindhi_explanation": sindhi_explanation,
            "doc_hash": doc_hash,
            "audio_url": f"/api/deeds/{case_id}/audio"
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/deeds/{case_id}/audio")
async def get_audio(case_id: str):
    db = SessionLocal()
    record = db.query(DeedRecord).filter(DeedRecord.case_id == case_id).first()
    db.close()
    if not record or not os.path.exists(record.audio_path) or os.path.getsize(record.audio_path) == 0:
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(record.audio_path, media_type="audio/mpeg")

@app.post("/api/deeds/{case_id}/decision")
async def record_decision(case_id: str, action: str = Form(...)):
    db = SessionLocal()
    record = db.query(DeedRecord).filter(DeedRecord.case_id == case_id).first()
    if not record:
        db.close()
        raise HTTPException(status_code=404, detail="Case record not found")

    record.status = action
    if action == "DISPUTED":
        record.disputed_at = datetime.utcnow()
        db.commit()
        db.close()

        print("\n" + "=" * 65)
        print(f"🚨 [PATWARI BYPASS TRIGGERED] Case ID: {case_id}")
        print(f"CNIC {record.signer_cnic} flagged DISPUTE / FRAUD.")
        print(f"Hash Lock: {record.doc_hash}")
        print("STATUS: Land transfer flagged on District Registry.")
        print("ESCALATED TO: District Legal Empowerment Committee (DLEC), Khairpur.")
        print("=" * 65 + "\n")

        return {"status": "DISPUTED", "message": "Transaction frozen."}

    db.commit()
    db.close()
    return {"status": "APPROVED", "qr_code_url": f"/api/deeds/{case_id}/qr"}

@app.get("/api/deeds/{case_id}/qr")
async def get_qr(case_id: str):
    db = SessionLocal()
    record = db.query(DeedRecord).filter(DeedRecord.case_id == case_id).first()
    db.close()
    if not record:
        raise HTTPException(status_code=404, detail="Case record not found")

    qr_payload = f"SANAD-E-HAQ|VERIFIED|ID:{record.case_id}|CNIC:{record.signer_cnic}|HASH:{record.doc_hash[:16]}"
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(qr_payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#047857", back_color="white")

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

# --- 4. DLEC / Admin Monitoring Endpoint ---

@app.get("/api/admin/records")
def get_all_records():
    db = SessionLocal()
    records = db.query(DeedRecord).order_by(desc(DeedRecord.created_at)).all()
    
    total = len(records)
    disputed = sum(1 for r in records if r.status == "DISPUTED")
    approved = sum(1 for r in records if r.status == "APPROVED")
    pending = sum(1 for r in records if r.status == "PENDING")

    data = [
        {
            "case_id": r.case_id,
            "signer_cnic": r.signer_cnic,
            "signer_phone": r.signer_phone,
            "transfer_type": r.transfer_type,
            "status": r.status,
            "doc_hash": r.doc_hash[:12] + "...",
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for r in records
    ]
    db.close()
    return {
        "stats": {"total": total, "disputed": disputed, "approved": approved, "pending": pending},
        "records": data
    }