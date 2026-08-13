import os
import re
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from bson import ObjectId
from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
PDF_EXTENSIONS = {"pdf"}

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "development-only-change-me"),
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_CONTENT_LENGTH", 16 * 1024 * 1024)),
)
use_memory_db = os.getenv("USE_IN_MEMORY_DB", "").lower() in {"1", "true", "yes"}
mongo_uri = os.getenv("MONGO_URI", "").strip()
if use_memory_db or not mongo_uri:
    # Keeps the app runnable for local UI development when Atlas has not been configured.
    # Production should always provide MONGO_URI and use the real MongoDB client below.
    import mongomock
    client = mongomock.MongoClient()
    app.config["USING_IN_MEMORY_DB"] = True
else:
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    app.config["USING_IN_MEMORY_DB"] = False
db = client[os.getenv("MONGO_DB", "student_portfolio")]
users, portfolios, messages, views = db.users, db.portfolios, db.contact_messages, db.portfolio_views
users.create_index([("email", ASCENDING)], unique=True)
portfolios.create_index([("owner_id", ASCENDING)], unique=True)
portfolios.create_index([("slug", ASCENDING)], unique=True)
messages.create_index([("owner_id", ASCENDING), ("created_at", DESCENDING)])
views.create_index([("portfolio_id", ASCENDING), ("viewed_at", DESCENDING)])

DEFAULT_PRIVACY = {k: True for k in ["email", "phone", "location", "resume", "social", "education", "certifications", "achievements", "experience"]}
THEMES = {"neon": "Dark Neon", "purple": "Purple Gradient", "blue": "Professional Blue", "minimal": "Minimal White", "glass": "Glassmorphism", "corporate": "Modern Corporate"}
PORTFOLIO_LIST_FIELDS = ["education", "skills", "projects", "certifications", "achievements", "experience"]

def now(): return datetime.now(timezone.utc)
def oid(value):
    try: return ObjectId(value)
    except Exception: abort(404)
def allowed(filename, extensions): return "." in filename and filename.rsplit(".", 1)[1].lower() in extensions
def current_user(): return users.find_one({"_id": oid(session["user_id"])}) if session.get("user_id") else None

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapped

def clean_slug(value):
    value = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return value[:55] or "student-details"

def unique_slug(seed, exclude=None):
    base = clean_slug(seed)
    slug = base
    while portfolios.find_one({"slug": slug, **({"_id": {"$ne": exclude}} if exclude else {})}):
        slug = f"{base[:45]}-{uuid.uuid4().hex[:7]}"
    return slug

def empty_portfolio(user):
    return {"owner_id": user["_id"], "slug": unique_slug(user["full_name"]), "name": f"{user['full_name']}'s Student Details", "personal": {"full_name": user["full_name"], "email": user["email"], "phone": user.get("phone", ""), "headline": "", "location": "", "intro": "", "photo": user.get("profile_photo", ""), "website": ""}, "about": {"about_me": "", "objective": "", "summary": "", "interests": "", "goals": ""}, "education": [], "skills": [], "projects": [], "certifications": [], "achievements": [], "experience": [], "social_links": {}, "resume": "", "theme": {"name": "neon", "accent": "#8b5cf6"}, "privacy": DEFAULT_PRIVACY.copy(), "section_visibility": {}, "seo": {"title": "", "description": ""}, "status": "DRAFT", "created_at": now(), "updated_at": now(), "published_at": None}

def normalize_portfolio(portfolio, user=None):
    if not portfolio: return portfolio
    portfolio.setdefault("name", f"{user['full_name']}'s Student Details" if user else "Student Details")
    portfolio.setdefault("slug", unique_slug(portfolio["name"], portfolio.get("_id")))
    portfolio.setdefault("personal", {})
    portfolio["personal"].setdefault("full_name", user.get("full_name", "") if user else "")
    portfolio["personal"].setdefault("email", user.get("email", "") if user else "")
    portfolio["personal"].setdefault("phone", user.get("phone", "") if user else "")
    portfolio["personal"].setdefault("headline", "")
    portfolio["personal"].setdefault("location", "")
    portfolio["personal"].setdefault("intro", "")
    portfolio["personal"].setdefault("photo", user.get("profile_photo", "") if user else "")
    portfolio["personal"].setdefault("website", "")
    portfolio.setdefault("about", {})
    for key in ["about_me", "objective", "summary", "interests", "goals"]:
        portfolio["about"].setdefault(key, "")
    for key in PORTFOLIO_LIST_FIELDS:
        portfolio.setdefault(key, [])
    portfolio.setdefault("social_links", {})
    portfolio.setdefault("resume", "")
    portfolio.setdefault("theme", {})
    portfolio["theme"].setdefault("name", "neon")
    portfolio["theme"].setdefault("accent", "#8b5cf6")
    portfolio["privacy"] = {**DEFAULT_PRIVACY, **portfolio.get("privacy", {})}
    portfolio.setdefault("section_visibility", {})
    portfolio.setdefault("seo", {})
    portfolio["seo"].setdefault("title", "")
    portfolio["seo"].setdefault("description", "")
    portfolio.setdefault("status", "DRAFT")
    return portfolio

def get_portfolio(create=False):
    user = current_user()
    portfolio = portfolios.find_one({"owner_id": user["_id"]})
    if not portfolio and create:
        portfolio = empty_portfolio(user); portfolios.insert_one(portfolio)
    return normalize_portfolio(portfolio, user)

def completion(p):
    if not p: return 0
    p = normalize_portfolio(p)
    tests = [p["personal"].get("headline"), p["personal"].get("intro"), p["about"].get("about_me"), p["education"], p["skills"], p["projects"], p["certifications"], p["experience"], p["social_links"], p["resume"]]
    return round(sum(bool(x) for x in tests) / len(tests) * 100)

def save_upload(file, kind):
    if not file or not file.filename: return ""
    extensions = PDF_EXTENSIONS if kind in {"resume", "document"} else IMAGE_EXTENSIONS
    if not allowed(file.filename, extensions): abort(400, "Unsupported file type")
    name = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    file.save(UPLOAD_DIR / name)
    return name

@app.route("/")
def index(): return render_template("landing.html", themes=THEMES)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name, email = request.form.get("full_name", "").strip(), request.form.get("email", "").lower().strip()
        password, confirm = request.form.get("password", ""), request.form.get("confirm_password", "")
        if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(password) < 8 or password != confirm:
            flash("Enter a name, valid email, and matching password of at least 8 characters.", "error")
        else:
            try:
                users.insert_one({"full_name": name, "email": email, "password_hash": generate_password_hash(password), "phone": request.form.get("phone", "").strip(), "profile_photo": "", "created_at": now(), "updated_at": now(), "is_active": True})
                flash("Account created. You can log in now.", "success"); return redirect(url_for("login"))
            except DuplicateKeyError: flash("An account already uses that email address.", "error")
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = users.find_one({"email": request.form.get("email", "").lower().strip()})
        if user and user.get("is_active") and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear(); session["user_id"] = str(user["_id"]); return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    p, user = get_portfolio(), current_user()
    if not p: return render_template("dashboard.html", user=user, portfolio=None, stats={}, completion=0)
    stats = {"Projects": len(p["projects"]), "Skills": len(p["skills"]), "Certificates": len(p["certifications"]), "Achievements": len(p["achievements"]), "Education": len(p["education"]), "Views": views.count_documents({"portfolio_id": p["_id"]}), "Unread messages": messages.count_documents({"owner_id": user["_id"], "status": "unread"})}
    return render_template("dashboard.html", user=user, portfolio=p, stats=stats, completion=completion(p))

@app.route("/builder")
@login_required
def builder(): return render_template("builder.html", user=current_user(), portfolio=serialize(get_portfolio(True)), themes=THEMES)

@app.route("/api/portfolio", methods=["GET", "PUT"])
@login_required
def portfolio_api():
    p = get_portfolio(True)
    if request.method == "GET": return jsonify(serialize(p))
    data = request.get_json(force=True)
    allowed_keys = {"name", "personal", "about", "education", "skills", "projects", "certifications", "achievements", "experience", "social_links", "theme", "privacy", "section_visibility", "seo"}
    update = {k: data[k] for k in allowed_keys if k in data}; update["updated_at"] = now()
    if "slug" in data:
        update["slug"] = unique_slug(data["slug"], p["_id"])
    portfolios.update_one({"_id": p["_id"], "owner_id": current_user()["_id"]}, {"$set": update})
    return jsonify({"ok": True, "slug": update.get("slug", p["slug"])})

@app.route("/api/upload/<kind>", methods=["POST"])
@login_required
def upload(kind):
    if kind not in {"photo", "project", "certificate", "achievement", "resume", "document"}: abort(404)
    filename = save_upload(request.files.get("file"), "resume" if kind == "resume" else kind)
    return jsonify({"ok": True, "filename": filename, "url": url_for("uploaded_file", filename=filename)})

@app.route("/uploads/<path:filename>")
def uploaded_file(filename): return send_from_directory(UPLOAD_DIR, filename)

@app.route("/student-details/<slug>", endpoint="student_details_public")
@app.route("/portfolio/<slug>", endpoint="public_portfolio")
def public_portfolio(slug):
    p = portfolios.find_one({"slug": slug, "status": "PUBLISHED"})
    if not p: abort(404)
    p = normalize_portfolio(p)
    views.insert_one({"portfolio_id": p["_id"], "viewed_at": now()})
    return render_template("public_portfolio.html", portfolio=p, theme=THEMES.get(p["theme"].get("name"), "Dark Neon"))

@app.route("/preview")
@login_required
def preview(): return render_template("public_portfolio.html", portfolio=get_portfolio(True), preview=True, theme="Preview")

@app.route("/api/portfolio/publish", methods=["POST"])
@login_required
def publish():
    p = get_portfolio(True); action = request.get_json(silent=True) or {}; status = "PUBLISHED" if action.get("publish", True) else "UNPUBLISHED"
    portfolios.update_one({"_id": p["_id"], "owner_id": current_user()["_id"]}, {"$set": {"status": status, "published_at": now() if status == "PUBLISHED" else None, "updated_at": now()}})
    return jsonify({"ok": True, "status": status, "url": url_for("student_details_public", slug=p["slug"], _external=True)})

@app.route("/api/contact/<portfolio_id>", methods=["POST"])
def contact(portfolio_id):
    p = portfolios.find_one({"_id": oid(portfolio_id), "status": "PUBLISHED"})
    if not p: return jsonify({"ok": False, "error": "This portfolio is not available."}), 404
    data = request.get_json(force=True); name, email, body = data.get("name", "").strip(), data.get("email", "").strip(), data.get("message", "").strip()
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(body) < 2: return jsonify({"ok": False, "error": "Please complete every field with a valid email."}), 400
    messages.insert_one({"portfolio_id": p["_id"], "owner_id": p["owner_id"], "sender_name": name, "sender_email": email, "message": body, "status": "unread", "created_at": now()})
    return jsonify({"ok": True, "message": "Message sent successfully."})

@app.route("/messages")
@login_required
def inbox(): return render_template("messages.html", user=current_user(), messages=list(messages.find({"owner_id": current_user()["_id"]}).sort("created_at", DESCENDING)))

@app.route("/api/messages/<message_id>", methods=["PATCH", "DELETE"])
@login_required
def message_api(message_id):
    query = {"_id": oid(message_id), "owner_id": current_user()["_id"]}
    if request.method == "DELETE": messages.delete_one(query)
    else: messages.update_one(query, {"$set": {"status": "read"}})
    return jsonify({"ok": True})

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        update = {"full_name": request.form.get("full_name", "").strip(), "phone": request.form.get("phone", "").strip(), "location": request.form.get("location", "").strip(), "short_bio": request.form.get("short_bio", "").strip(), "updated_at": now()}
        photo = request.files.get("photo")
        if photo and photo.filename: update["profile_photo"] = save_upload(photo, "photo")
        users.update_one({"_id": user["_id"]}, {"$set": update}); flash("Profile saved.", "success"); return redirect(url_for("profile"))
    return render_template("profile.html", user=user)

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email, password_hash = os.getenv("ADMIN_EMAIL"), os.getenv("ADMIN_PASSWORD_HASH")
        if email and password_hash and request.form.get("email", "").lower() == email.lower() and check_password_hash(password_hash, request.form.get("password", "")):
            session.clear(); session["admin"] = True; return redirect(url_for("admin_dashboard"))
        flash("Invalid administrator credentials.", "error")
    return render_template("auth.html", mode="admin")

@app.route("/admin")
@admin_required
def admin_dashboard():
    stats = {"Students": users.count_documents({}), "Portfolios": portfolios.count_documents({}), "Published": portfolios.count_documents({"status": "PUBLISHED"}), "Drafts": portfolios.count_documents({"status": "DRAFT"}), "Projects": sum(len(x.get("projects", [])) for x in portfolios.find({}, {"projects": 1})), "Certificates": sum(len(x.get("certifications", [])) for x in portfolios.find({}, {"certifications": 1})), "Messages": messages.count_documents({}), "Views": views.count_documents({})}
    return render_template("admin.html", stats=stats, students=list(users.find({}, {"password_hash": 0}).sort("created_at", DESCENDING)))

@app.route("/admin/users/<user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id):
    user = users.find_one({"_id": oid(user_id)})
    if user: users.update_one({"_id": user["_id"]}, {"$set": {"is_active": not user.get("is_active", True)}})
    return redirect(url_for("admin_dashboard"))

def serialize(value):
    if isinstance(value, ObjectId): return str(value)
    if isinstance(value, datetime): return value.isoformat()
    if isinstance(value, list): return [serialize(v) for v in value]
    if isinstance(value, dict): return {k: serialize(v) for k, v in value.items()}
    return value

@app.errorhandler(413)
def too_large(error): return jsonify({"ok": False, "error": "File is too large."}), 413

if __name__ == "__main__": app.run(debug=True)
