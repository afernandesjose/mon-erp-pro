from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime, Text, or_
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from datetime import datetime, timedelta
from typing import List, Optional

# --- DATABASE ---
DATABASE_URL = "postgresql://myerp_user:admin@localhost/myerp_db"
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
        # Calcul précis de la TVA par ligne
        return sum(line.quantity * line.unit_price * (1 - (line.discount or 0)/100) * ((line.vat_rate or 0)/100) for line in self.lines)

    @property
    def total_ttc(self):
        # HT + TVA réelle
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

def get_db():
    db = SessionLocal()
    try:
        if db.query(Company).count() == 0:
            db.add(Company())
            db.commit()
        yield db
    finally:
        db.close()

# --- PAGES ---
@app.get("/", response_class=HTMLResponse)
def page_dashboard(request: Request, db: Session = Depends(get_db)):
    recent_invoices = db.query(Invoice).order_by(Invoice.id.desc()).limit(5).all()
    all_invoices = db.query(Invoice).all()
    total_revenue = sum(inv.total_ht for inv in all_invoices)
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "page": "dashboard",
        "company": db.query(Company).first(), "customers": db.query(Customer).all(),
        "products": db.query(Product).order_by(Product.name).all(), "recent_invoices": recent_invoices, "total_revenue": total_revenue
    })

@app.get("/invoices_page", response_class=HTMLResponse)
def page_invoices(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "page": "invoices",
        "company": db.query(Company).first(), "customers": db.query(Customer).all(),
        "products": db.query(Product).order_by(Product.name).all(), "invoices": db.query(Invoice).order_by(Invoice.id.desc()).all()
    })

@app.get("/customers_page", response_class=HTMLResponse)
def page_customers(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "page": "customers",
        "company": db.query(Company).first(), "customers": db.query(Customer).all(),
        "products": db.query(Product).all(), "invoices": []
    })

@app.get("/products_page", response_class=HTMLResponse)
def page_products(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "page": "products",
        "company": db.query(Company).first(), "customers": db.query(Customer).all(),
        "products": db.query(Product).order_by(Product.name).all(), "invoices": []
    })

# --- API ---
@app.get("/api/invoices/{invoice_id}")
def get_invoice_details(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv: raise HTTPException(status_code=404)
    return { "id": inv.id, "customer_id": inv.customer_id, "date": inv.date, "due_date": inv.due_date, "lines": inv.lines_data }

@app.get("/api/search")
def global_search(q: str, db: Session = Depends(get_db)):
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

# --- ACTIONS CRUD ---
@app.post("/company/")
def update_company(info: CompanyUpdate, db: Session = Depends(get_db)):
    company = db.query(Company).first()
    for key, value in info.dict().items(): setattr(company, key, value)
    db.commit()
    return {"message": "ok"}

@app.post("/customers/")
def create_customer(customer: CustomerCreate, db: Session = Depends(get_db)):
    new_c = Customer(**customer.dict()); db.add(new_c); db.commit(); db.refresh(new_c); return new_c

@app.put("/customers/{customer_id}")
def update_customer(customer_id: int, customer: CustomerCreate, db: Session = Depends(get_db)):
    db_c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not db_c: raise HTTPException(status_code=404)
    for key, value in customer.dict().items(): setattr(db_c, key, value)
    db.commit(); return {"message": "ok"}

@app.delete("/customers/{customer_id}")
def delete_customer(customer_id: int, db: Session = Depends(get_db)):
    if db.query(Invoice).filter(Invoice.customer_id == customer_id).count() > 0: raise HTTPException(status_code=400, detail="Impossible : ce client a des factures.")
    db_c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not db_c: raise HTTPException(status_code=404)
    db.delete(db_c); db.commit(); return {"message": "ok"}

@app.post("/products/")
def create_product(product: ProductCreate, db: Session = Depends(get_db)):
    new_p = Product(**product.dict()); db.add(new_p); db.commit(); db.refresh(new_p); return new_p

@app.put("/products/{product_id}")
def update_product(product_id: int, product: ProductCreate, db: Session = Depends(get_db)):
    db_prod = db.query(Product).filter(Product.id == product_id).first()
    if not db_prod: raise HTTPException(status_code=404)
    db_prod.name = product.name; db_prod.price = product.price; db_prod.vat_rate = product.vat_rate
    db.commit(); return {"message": "ok"}

@app.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    if db.query(InvoiceLine).filter(InvoiceLine.product_id == product_id).count() > 0: raise HTTPException(status_code=400, detail="Impossible : ce produit est utilisé.")
    db_prod = db.query(Product).filter(Product.id == product_id).first()
    if not db_prod: raise HTTPException(status_code=404)
    db.delete(db_prod); db.commit(); return {"message": "deleted"}

@app.post("/invoices/")
def create_invoice(invoice_data: InvoiceCreate, db: Session = Depends(get_db)):
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
def update_invoice(invoice_id: int, invoice_data: InvoiceCreate, db: Session = Depends(get_db)):
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
def delete_invoice(invoice_id: int, db: Session = Depends(get_db)):
    db_inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not db_inv: raise HTTPException(status_code=404, detail="Facture introuvable")
    db.delete(db_inv); db.commit(); return {"message": "deleted"}
