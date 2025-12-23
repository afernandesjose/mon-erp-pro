import os
import io
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, or_
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from weasyprint import HTML

load_dotenv()

# --- DATABASE ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://myerp_user:admin@localhost/myerp_db")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-env")
SESSION_EXPIRE_SECONDS = 60 * 60 * 24 * 7  # 7 jours
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- TABLES ---
class Company(Base):
    __tablename__ = "company"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, default="Mon Entreprise")
    address = Column(String, default="1 Rue de la République, 69001 Lyon")
    siret = Column(String, default="000 000 000 00000")
    vat_number = Column(String, default="FR 00 000000000")
    iban = Column(String, default="FR76 0000 0000 0000 0000 0000 000")
    bic = Column(String, default="BANKFRPP")
    logo_url = Column(String, default="https://img.icons8.com/ios/100/000000/company.png")
    legal_terms = Column(Text, default="Indemnité forfaitaire pour frais de recouvrement : 40€.")
    theme_color = Column(String, default="#2c3e50") 
    payment_term = Column(Integer, default=30) 

class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String)
    address = Column(String, default="")
    siret = Column(String, default="")
    invoices = relationship("Invoice", back_populates="customer")

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    price = Column(Float)
    vat_rate = Column(Float, default=20.0)

class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, default=datetime.utcnow)
    due_date = Column(DateTime)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    
    customer = relationship("Customer", back_populates="invoices")
    lines = relationship("InvoiceLine", back_populates="invoice", cascade="all, delete-orphan")

    @property
    def total_ht(self):
        return sum(line.quantity * line.unit_price * (1 - (line.discount or 0)/100) for line in self.lines)
    
    @property
    def total_tax(self):
        return sum(line.quantity * line.unit_price * (1 - (line.discount or 0)/100) * ((line.vat_rate or 0)/100) for line in self.lines)

    @property
    def total_ttc(self):
        return self.total_ht + self.total_tax
    
    @property
    def lines_data(self):
        return [{"product_id": line.product_id, "quantity": line.quantity, "discount": line.discount, "vat_rate": line.vat_rate} for line in self.lines]

class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Integer, default=1)
    unit_price = Column(Float)
    discount = Column(Float, default=0.0)
    vat_rate = Column(Float, default=20.0)
    
    invoice = relationship("Invoice", back_populates="lines")
    product = relationship("Product")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)

# --- SCHEMAS ---
class CompanyUpdate(BaseModel):
    name: str
    address: str
    siret: str
    vat_number: str
    iban: str
    bic: str
    logo_url: str
    legal_terms: str
    theme_color: str
    payment_term: int

class CustomerCreate(BaseModel):
    name: str
    email: str
    address: str
    siret: str

class ProductCreate(BaseModel):
    name: str
    price: float
    vat_rate: float = 20.0

class InvoiceLineCreate(BaseModel):
    product_id: int
    quantity: int
    discount: float = 0.0
    vat_rate: float = 20.0

class InvoiceCreate(BaseModel):
    customer_id: int
    due_date: Optional[datetime] = None
    lines: List[InvoiceLineCreate]

# --- APP ---
app = FastAPI(title="Mon ERP Pro")
templates = Jinja2Templates(directory="templates")

def hash_password(password: str) -> str:
    salted = f"{password}:monerp_salt"
    return hashlib.sha256(salted.encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_password(password), hashed)


def create_session_token(user_id: int) -> str:
    expires_at = int(time.time()) + SESSION_EXPIRE_SECONDS
    payload = f"{user_id}:{expires_at}"
    signature = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def parse_session_token(token: str):
    try:
        user_id_str, expires_str, signature = token.split(":")
        payload = f"{user_id_str}:{expires_str}"
        expected_sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        if int(expires_str) < int(time.time()):
            return None
        return int(user_id_str)
    except Exception:
        return None


def get_current_user(request: Request, db: Session) -> Optional[User]:
    token = request.cookies.get("session_token")
    if not token:
        return None
    user_id = parse_session_token(token)
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


def get_db():
    db = SessionLocal()
    try:
        if db.query(Company).count() == 0:
            db.add(Company())
            db.commit()
        if db.query(User).count() == 0:
            admin = User(username="admin", password_hash=hash_password("admin123"))
            db.add(admin)
            db.commit()
        yield db
    finally:
        db.close()


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentification requise")
    return user


# --- ROUTES PAGES ---
@app.get("/", response_class=HTMLResponse)
def page_dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    recent_invoices = db.query(Invoice).order_by(Invoice.id.desc()).limit(5).all()
    all_invoices = db.query(Invoice).all()
    total_revenue = sum(inv.total_ht for inv in all_invoices)
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "page": "dashboard",
        "company": db.query(Company).first(), "customers": db.query(Customer).all(),
        "products": db.query(Product).order_by(Product.name).all(), "recent_invoices": recent_invoices,
        "total_revenue": total_revenue, "user": user
    })

@app.get("/invoices_page", response_class=HTMLResponse)
def page_invoices(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "page": "invoices",
        "company": db.query(Company).first(), "customers": db.query(Customer).all(),
        "products": db.query(Product).order_by(Product.name).all(), "invoices": db.query(Invoice).order_by(Invoice.id.desc()).all(),
        "user": user
    })

@app.get("/customers_page", response_class=HTMLResponse)
def page_customers(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "page": "customers",
        "company": db.query(Company).first(), "customers": db.query(Customer).all(),
        "products": db.query(Product).all(), "invoices": [], "user": user
    })

@app.get("/products_page", response_class=HTMLResponse)
def page_products(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "page": "products",
        "company": db.query(Company).first(), "customers": db.query(Customer).all(),
        "products": db.query(Product).order_by(Product.name).all(), "invoices": [], "user": user
    })

@app.get("/api/invoices/{invoice_id}")
def get_invoice_details(invoice_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv: raise HTTPException(status_code=404)
    return { "id": inv.id, "customer_id": inv.customer_id, "date": inv.date, "due_date": inv.due_date, "lines": inv.lines_data }

@app.get("/api/search")
def global_search(q: str, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    results = []
    if not q: return []
    customers = db.query(Customer).filter(or_(Customer.name.ilike(f"%{q}%"), Customer.email.ilike(f"%{q}%"))).limit(5).all()
    for c in customers: results.append({"type": "Client", "label": c.name, "id": c.id, "data": {"name": c.name, "email": c.email, "address": c.address, "siret": c.siret}})
    products = db.query(Product).filter(Product.name.ilike(f"%{q}%")).limit(5).all()
    for p in products: results.append({"type": "Produit", "label": p.name, "id": p.id, "data": {"name": p.name, "price": p.price, "vat_rate": p.vat_rate}})
    if q.isdigit():
        inv = db.query(Invoice).filter(Invoice.id == int(q)).first()
        if inv: results.append({"type": "Facture", "label": f"Facture #{inv.id} ({inv.customer.name})", "id": inv.id, "data": None})
    return results

# --- NOUVELLE ROUTE PDF ---
@app.get("/invoices/{invoice_id}/pdf")
def generate_pdf(invoice_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    # 1. Récupérer la facture et l'entreprise
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    company = db.query(Company).first()
    if not invoice: raise HTTPException(status_code=404)

    # 2. Générer le HTML avec Jinja2
    html_content = templates.TemplateResponse("invoice_pdf.html", {
        "request": request,
        "invoice": invoice,
        "company": company
    }).body.decode("utf-8")

    # 3. Convertir en PDF avec WeasyPrint
    pdf_file = io.BytesIO()
    HTML(string=html_content).write_pdf(pdf_file)
    pdf_file.seek(0)

    # 4. Renvoyer le fichier
    return StreamingResponse(pdf_file, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=Facture_{invoice.id}.pdf"})

# --- ACTIONS CRUD ---
@app.post("/company/")
def update_company(info: CompanyUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    company = db.query(Company).first()
    for key, value in info.dict().items(): setattr(company, key, value)
    db.commit()
    return {"message": "ok"}

@app.post("/customers/")
def create_customer(customer: CustomerCreate, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    new_c = Customer(**customer.dict()); db.add(new_c); db.commit(); db.refresh(new_c); return new_c

@app.put("/customers/{customer_id}")
def update_customer(customer_id: int, customer: CustomerCreate, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    db_c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not db_c: raise HTTPException(status_code=404)
    for key, value in customer.dict().items(): setattr(db_c, key, value)
    db.commit(); return {"message": "ok"}

@app.delete("/customers/{customer_id}")
def delete_customer(customer_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    if db.query(Invoice).filter(Invoice.customer_id == customer_id).count() > 0: raise HTTPException(status_code=400, detail="Impossible : ce client a des factures.")
    db_c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not db_c: raise HTTPException(status_code=404)
    db.delete(db_c); db.commit(); return {"message": "ok"}

@app.post("/products/")
def create_product(product: ProductCreate, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    new_p = Product(**product.dict()); db.add(new_p); db.commit(); db.refresh(new_p); return new_p

@app.put("/products/{product_id}")
def update_product(product_id: int, product: ProductCreate, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    db_prod = db.query(Product).filter(Product.id == product_id).first()
    if not db_prod: raise HTTPException(status_code=404)
    db_prod.name = product.name; db_prod.price = product.price; db_prod.vat_rate = product.vat_rate
    db.commit(); return {"message": "ok"}

@app.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    if db.query(InvoiceLine).filter(InvoiceLine.product_id == product_id).count() > 0: raise HTTPException(status_code=400, detail="Impossible : ce produit est utilisé.")
    db_prod = db.query(Product).filter(Product.id == product_id).first()
    if not db_prod: raise HTTPException(status_code=404)
    db.delete(db_prod); db.commit(); return {"message": "deleted"}

@app.post("/invoices/")
def create_invoice(invoice_data: InvoiceCreate, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    new_invoice = Invoice(customer_id=invoice_data.customer_id)
    if invoice_data.due_date: new_invoice.due_date = invoice_data.due_date
    else:
        company = db.query(Company).first()
        days = company.payment_term or 30
        new_invoice.due_date = datetime.utcnow() + timedelta(days=days)
    db.add(new_invoice); db.commit(); db.refresh(new_invoice)
    for line in invoice_data.lines:
        product = db.query(Product).filter(Product.id == line.product_id).first()
        if product: 
            vat = line.vat_rate if line.vat_rate is not None else product.vat_rate
            db.add(InvoiceLine(invoice_id=new_invoice.id, product_id=product.id, quantity=line.quantity, unit_price=product.price, discount=line.discount, vat_rate=vat))
    db.commit(); return new_invoice

@app.put("/invoices/{invoice_id}")
def update_invoice(invoice_id: int, invoice_data: InvoiceCreate, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    db_inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not db_inv: raise HTTPException(status_code=404)
    db_inv.customer_id = invoice_data.customer_id
    if invoice_data.due_date: db_inv.due_date = invoice_data.due_date
    db.query(InvoiceLine).filter(InvoiceLine.invoice_id == invoice_id).delete()
    for line in invoice_data.lines:
        product = db.query(Product).filter(Product.id == line.product_id).first()
        if product: 
            vat = line.vat_rate if line.vat_rate is not None else product.vat_rate
            db.add(InvoiceLine(invoice_id=db_inv.id, product_id=product.id, quantity=line.quantity, unit_price=product.price, discount=line.discount, vat_rate=vat))
    db.commit(); return {"message": "ok"}

@app.delete("/invoices/{invoice_id}")
def delete_invoice(invoice_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_user)):
    db_inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not db_inv: raise HTTPException(status_code=404, detail="Facture introuvable")
    db.delete(db_inv); db.commit(); return {"message": "deleted"}


# --- AUTH ---
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login_action(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Identifiants incorrects"}, status_code=401)
    token = create_session_token(user.id)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("session_token", token, httponly=True, max_age=SESSION_EXPIRE_SECONDS, samesite="lax")
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session_token")
    return response
