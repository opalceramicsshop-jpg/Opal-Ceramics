import csv, io, os, sqlite3, time, uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

load_dotenv()
BASE = Path(__file__).resolve().parent
UPLOAD_DIR = BASE / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
DB_PATH = os.getenv("DB_PATH", str(BASE / "opal_broadcast.db"))
GRAPH_VERSION = os.getenv("WA_GRAPH_VERSION", "v27.0")
ALLOWED = {"png", "jpg", "jpeg", "webp", "pdf"}

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "replace-this-secret-before-deploying")
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS contacts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '',
          phone TEXT NOT NULL UNIQUE, opted_in INTEGER NOT NULL DEFAULT 0,
          consent_source TEXT DEFAULT '', consent_at TEXT, status TEXT NOT NULL DEFAULT 'pending',
          error TEXT, sent_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS campaigns (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
          template_name TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'en',
          media_path TEXT, media_type TEXT, status TEXT NOT NULL DEFAULT 'draft',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS campaign_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL,
          contact_id INTEGER NOT NULL, result TEXT NOT NULL, detail TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)


def normalize_phone(value):
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return digits if 11 <= len(digits) <= 15 else ""


def stats():
    with db() as con:
        return {
            "contacts": con.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
            "eligible": con.execute("SELECT COUNT(*) FROM contacts WHERE opted_in=1 AND status='pending'").fetchone()[0],
            "sent": con.execute("SELECT COUNT(*) FROM contacts WHERE status='sent'").fetchone()[0],
            "failed": con.execute("SELECT COUNT(*) FROM contacts WHERE status='failed'").fetchone()[0],
            "campaigns": con.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],
        }


@app.route("/")
def home():
    init_db()
    with db() as con:
        recent = con.execute("SELECT * FROM campaigns ORDER BY id DESC LIMIT 5").fetchall()
    return render_template("index.html", s=stats(), recent=recent)


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(BASE / "static", "manifest.webmanifest", mimetype="application/manifest+json")


@app.route("/service-worker.js")
def service_worker():
    return send_from_directory(BASE / "static", "service-worker.js", mimetype="application/javascript")


@app.route("/contacts", methods=["GET", "POST"])
def contacts():
    init_db()
    if request.method == "POST":
        upload = request.files.get("csv_file")
        if not upload or not upload.filename.lower().endswith(".csv"):
            flash("Choose a CSV file first.", "error")
            return redirect(url_for("contacts"))
        try:
            reader = csv.DictReader(io.StringIO(upload.stream.read().decode("utf-8-sig")))
            headers = {h.strip().lower() for h in (reader.fieldnames or [])}
            if not {"name", "phone"}.issubset(headers):
                raise ValueError("CSV must contain name and phone columns.")
            added = skipped = 0
            for row in reader:
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                phone = normalize_phone(row.get("phone"))
                if not phone:
                    skipped += 1
                    continue
                consent = row.get("opted_in", "").lower() in {"yes", "true", "1", "y"}
                source = row.get("consent_source", "")
                consent_at = datetime.now(timezone.utc).isoformat() if consent else None
                with db() as con:
                    cur = con.execute("""INSERT OR IGNORE INTO contacts
                        (name, phone, opted_in, consent_source, consent_at)
                        VALUES (?,?,?,?,?)""",
                        (row.get("name", ""), phone, int(consent), source, consent_at))
                    added += cur.rowcount
                    skipped += 1 - cur.rowcount
            flash(f"Imported {added} new contacts; {skipped} skipped or duplicate.", "success")
        except Exception as exc:
            flash(f"Import error: {exc}", "error")
        return redirect(url_for("contacts"))
    q = request.args.get("q", "").strip()
    with db() as con:
        if q:
            rows = con.execute("SELECT * FROM contacts WHERE name LIKE ? OR phone LIKE ? ORDER BY id DESC",
                               (f"%{q}%", f"%{q}%")).fetchall()
        else:
            rows = con.execute("SELECT * FROM contacts ORDER BY id DESC LIMIT 300").fetchall()
    return render_template("contacts.html", rows=rows, q=q)


@app.post("/contacts/<int:cid>/consent")
def consent(cid):
    allowed = request.form.get("opted_in") == "1"
    source = request.form.get("consent_source", "").strip()
    with db() as con:
        con.execute("""UPDATE contacts SET opted_in=?, consent_source=?,
            consent_at=?, status=CASE WHEN ?=0 THEN 'pending' ELSE status END
            WHERE id=?""",
            (int(allowed), source, datetime.now(timezone.utc).isoformat() if allowed else None,
             int(allowed), cid))
    flash("Consent setting updated. Only enable it with recorded customer permission.", "success")
    return redirect(url_for("contacts"))


@app.route("/campaigns", methods=["GET", "POST"])
def campaigns():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        template = request.form.get("template_name", "").strip()
        language = request.form.get("language", "en").strip()
        media = request.files.get("media")
        media_path = media_type = None
        if media and media.filename:
            ext = media.filename.rsplit(".", 1)[-1].lower()
            if ext not in ALLOWED:
                flash("Allowed media: PNG, JPG, WEBP, PDF.", "error")
                return redirect(url_for("campaigns"))
            safe = secure_filename(media.filename)
            unique = f"{uuid.uuid4().hex}_{safe}"
            media.save(UPLOAD_DIR / unique)
            media_path = unique
            media_type = "document" if ext == "pdf" else "image"
        if not title or not template:
            flash("Campaign title and approved template name are required.", "error")
            return redirect(url_for("campaigns"))
        with db() as con:
            con.execute("INSERT INTO campaigns(title,template_name,language,media_path,media_type) VALUES(?,?,?,?,?)",
                        (title, template, language, media_path, media_type))
        flash("Campaign created. The template must match the approved Meta template structure.", "success")
        return redirect(url_for("campaigns"))
    with db() as con:
        rows = con.execute("""SELECT c.*,
          SUM(CASE WHEN l.result='sent' THEN 1 ELSE 0 END) sent_count,
          SUM(CASE WHEN l.result='failed' THEN 1 ELSE 0 END) failed_count
          FROM campaigns c LEFT JOIN campaign_logs l ON c.id=l.campaign_id
          GROUP BY c.id ORDER BY c.id DESC""").fetchall()
    return render_template("campaigns.html", rows=rows, eligible=stats()["eligible"])


def upload_media_to_meta(token, phone_id, filename, media_type):
    path = UPLOAD_DIR / filename
    ext = path.suffix.lower()
    mime = "application/pdf" if media_type == "document" else {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"
    }.get(ext, "image/jpeg")
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{phone_id}/media"
    with path.open("rb") as f:
        response = requests.post(url, data={"messaging_product": "whatsapp"},
            files={"file": (path.name, f, mime)},
            headers={"Authorization": f"Bearer {token}"}, timeout=60)
    response.raise_for_status()
    return response.json()["id"]


def send_template(token, phone_id, person, campaign, media_id=None):
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{phone_id}/messages"
    components = []
    if campaign["media_type"] and media_id:
        header_param = {"type": campaign["media_type"], campaign["media_type"]: {"id": media_id}}
        components.append({"type": "header", "parameters": [header_param]})
    # Starter template contract: body contains exactly one text parameter {{1}} for contact name.
    components.append({"type": "body", "parameters": [
        {"type": "text", "text": person["name"] or "Customer"}
    ]})
    payload = {"messaging_product": "whatsapp", "to": person["phone"], "type": "template",
               "template": {"name": campaign["template_name"],
                            "language": {"code": campaign["language"]},
                            "components": components}}
    response = requests.post(url, json=payload,
        headers={"Authorization": f"Bearer {token}"}, timeout=40)
    return response.ok, response.text


@app.post("/campaigns/<int:campaign_id>/run")
def run_campaign(campaign_id):
    mode = request.form.get("mode", "dry")
    with db() as con:
        campaign = con.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        recipients = con.execute("SELECT * FROM contacts WHERE opted_in=1 AND status='pending' ORDER BY id").fetchall()
    if not campaign:
        flash("Campaign not found.", "error")
        return redirect(url_for("campaigns"))
    token, phone_id = os.getenv("WA_TOKEN"), os.getenv("WA_PHONE_ID")
    if mode == "live" and (not token or not phone_id):
        flash("Set WA_TOKEN and WA_PHONE_ID in .env before live sending.", "error")
        return redirect(url_for("campaigns"))
    if mode == "live" and request.form.get("confirm") != "yes":
        flash("Please tick the live-send confirmation.", "error")
        return redirect(url_for("campaigns"))

    media_id = None
    if mode == "live" and campaign["media_path"]:
        try:
            media_id = upload_media_to_meta(token, phone_id, campaign["media_path"], campaign["media_type"])
        except Exception as exc:
            flash(f"Media upload failed: {exc}", "error")
            return redirect(url_for("campaigns"))

    sent = failed = 0
    for person in recipients:
        if mode == "dry":
            result, detail = "dry_run", "Preview only; no message sent."
        else:
            try:
                ok, detail = send_template(token, phone_id, person, campaign, media_id)
                result = "sent" if ok else "failed"
            except Exception as exc:
                result, detail = "failed", str(exc)
        with db() as con:
            con.execute("INSERT INTO campaign_logs(campaign_id,contact_id,result,detail) VALUES(?,?,?,?)",
                        (campaign_id, person["id"], result, detail[:1000]))
            if result == "sent":
                con.execute("UPDATE contacts SET status='sent',sent_at=? WHERE id=?",
                            (datetime.now(timezone.utc).isoformat(), person["id"]))
                sent += 1
            elif result == "failed":
                con.execute("UPDATE contacts SET status='failed',error=? WHERE id=?",
                            (detail[:500], person["id"]))
                failed += 1
        if mode == "live":
            time.sleep(1)
    with db() as con:
        con.execute("UPDATE campaigns SET status=? WHERE id=?",
                    ("previewed" if mode == "dry" else "submitted", campaign_id))
    flash(f"Finished. Sent/API accepted: {sent}; failed: {failed}; previewed: {len(recipients) if mode == 'dry' else 0}.", "success")
    return redirect(url_for("campaigns"))


@app.route("/reports")
def reports():
    with db() as con:
        rows = con.execute("""SELECT l.*,c.title,p.name,p.phone FROM campaign_logs l
          JOIN campaigns c ON c.id=l.campaign_id JOIN contacts p ON p.id=l.contact_id
          ORDER BY l.id DESC LIMIT 500""").fetchall()
    return render_template("reports.html", rows=rows)


@app.route("/health")
def health():
    return jsonify({"ok": True, "app": "Opal Broadcast"})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
