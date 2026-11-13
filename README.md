# Opal Broadcast — Android-first installable PWA

Responsive Flask web app with PWA manifest/service worker, CSV import, consent management, image/PDF campaign attachments, campaign history, dry-run preview, reports, and an optional official WhatsApp Cloud API sender.

## Run locally
Python 3.10+ required.
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```
Open `http://localhost:5000`. For installation on a real Android phone, deploy behind HTTPS; open the HTTPS URL in Chrome → menu → Install app / Add to Home screen.

## CSV columns
`name,phone,opted_in,consent_source`
Only yes/true/1/y means opted-in. All other values are opted out. Never mark a contact opted-in without actual permission.

## WhatsApp integration
- This app's supported automatic sender is the official WhatsApp Business Platform Cloud API.
- Set `WA_TOKEN` and `WA_PHONE_ID` in `.env`.
- Create a Meta-approved template. The current starter sender expects one body variable `{{1}}` for customer name. If attaching media, the approved template must also have a matching IMAGE or DOCUMENT header.
- Create a campaign and run Dry Run first. Live send requires an explicit confirmation checkbox.
- API accepted is not the same as delivered/read. Add Meta webhooks for final delivery/read/failure receipts before using those labels in production.
- API version is configurable; use the version supported by your Meta app.

## About the "both" request
The project supports automatic sending through Cloud API. It does not copy proprietary code from paid bulk-sender apps, automate the consumer WhatsApp/WhatsApp Business mobile app, reverse engineer private protocols, or bypass WhatsApp limits. For the second/manual path, use WhatsApp's own app share/open-chat features manually; do not use unofficial bulk automation.

## Before public production
This starter is not yet a hardened multi-user production service. Add login/authentication, HTTPS, CSRF protection, role permissions, audit trail, consent evidence and opt-out intake, secure secret storage, media cleanup, webhooks, queue/retries, per-account throttling, backups, privacy policy, and abuse monitoring. Restrict upload size and validate media types at the server/proxy too. Do not expose Flask's built-in server directly to the public internet.
