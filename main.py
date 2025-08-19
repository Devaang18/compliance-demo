from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import openai
import pdfplumber
import tempfile
import json
import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import getaddresses
import os
import imaplib
import email
import threading
import time
from dotenv import load_dotenv

load_dotenv()

# ================================
# CONFIGURATION
# ================================
ALLOWED_SENDERS = ['devaang18@gmail.com', 'neildillon10@gmail.com']
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_EMAIL = SMTP_EMAIL
IMAP_PASSWORD = SMTP_PASSWORD

openai.api_key = OPENAI_API_KEY

# ================================
# FASTAPI APP
# ================================
app = FastAPI(title="Automated AI Compliance Review")

# ================================
# REQUEST MODEL
# ================================
class EmailPayload(BaseModel):
    sender: str
    filename: str
    file: str  # base64-encoded PDF

# ================================
# UTILITIES
# ================================
def extract_text_from_pdf(file_path: str) -> str:
    text = ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text


def format_report_html(report_json):
    summary = report_json.get("summary", "")
    issues = report_json.get("issues", [])

    html = f"""
    <html>
    <body>
        <p>Dear User,</p>
        <p>Here is your Compliance Review Report:</p>
        <h3>Summary:</h3>
        <p>{summary}</p>
    """

    if issues:
        html += """
        <h3>Detailed Issues Found:</h3>
        <table border="1" cellpadding="5" cellspacing="0">
            <tr style="background-color:#f2f2f2;">
                <th>Category</th>
                <th>Severity</th>
                <th>Regulation Reference</th>
                <th>Violation Text</th>
                <th>Rule Description</th>
                <th>Recommendation</th>
            </tr>
        """
        for issue in issues:
            severity_color = {"Low":"#d4edda","Medium":"#fff3cd","High":"#f8d7da"}.get(issue.get("severity","Low"), "#ffffff")
            html += f"""
                <tr style="background-color:{severity_color};">
                    <td>{issue.get('category')}</td>
                    <td>{issue.get('severity')}</td>
                    <td>{issue.get('regulation_reference')}</td>
                    <td>{issue.get('exact_violation_text')}</td>
                    <td>{issue.get('rule_description')}</td>
                    <td>{issue.get('recommendation')}</td>
                </tr>
            """
        html += "</table>"
    else:
        html += """
        <p style="padding:10px; background-color:#d4edda; border:1px solid #c3e6cb; border-radius:5px;">
            ✅ No compliance issues were found in the submitted document. The document appears fully compliant.
        </p>
        """

    html += """
        <p>Thank you for using Solas Compliance.</p>
    </body>
    </html>
    """
    return html


def send_email(to_email: str, cc_emails: list, subject: str, html_body: str, original_msg=None):
    if cc_emails is None:
        cc_emails = []

    msg = MIMEMultipart("alternative")
    msg['Subject'] = "Re: " + subject if not subject.startswith("Re:") else subject
    msg['From'] = SMTP_EMAIL
    msg['To'] = to_email
    if cc_emails:
        msg['Cc'] = ", ".join(cc_emails)

    # Preserve threading
    if original_msg:
        if original_msg.get("Message-ID"):
            msg['In-Reply-To'] = original_msg.get("Message-ID")
            msg['References'] = original_msg.get("Message-ID")

    part = MIMEText(html_body, "html")
    msg.attach(part)

    all_recipients = [to_email] + cc_emails

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, all_recipients, msg.as_string())


def clean_gpt_json(raw_output: str) -> str:
    cleaned = raw_output.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json"):].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    return cleaned

# ================================
# REVIEW FUNCTION
# ================================
def review_pdf(pdf_bytes: bytes, sender_email: str, cc_emails: list = None, original_msg=None):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    text = extract_text_from_pdf(tmp_path)
    if not text.strip():
        raise ValueError("PDF contains no extractable text")

    prompt = f"""
You are a compliance expert specializing in gambling, marketing, and legal regulations. Review the following document text and identify violations of SPECIFIC regulated policy rules from these industries.

IMPORTANT: Only reference ACTUAL regulated policy rules from:
- Gambling regulations (e.g., UK Gambling Act 2005, US UIGEA, EU gambling directives)
- Marketing regulations (e.g., CAP Code, FTC guidelines, ASA regulations)
- Legal compliance (e.g., GDPR, consumer protection laws, financial regulations)

DO NOT create or reference made-up rules. Only use established regulatory frameworks.
If no violations exist, explicitly return an empty issues list and state in the summary that the document is fully compliant.

For each violation found, you must:
1. Reference the SPECIFIC regulation/act/section
2. Quote the EXACT line from user's document that violates it
3. Explain how it violates the regulation

Return ONLY a JSON object with keys:
- "issues": list of compliance issues found. Each issue must have:
    - id
    - category
    - severity
    - regulation_reference
    - exact_violation_text
    - rule_description
    - recommendation
- "summary": a short 2-3 sentence summary
Document text to review:
\"\"\"{text}\"\"\"

Return valid JSON only.
"""

    response = openai.chat.completions.create(
        model="gpt-5-chat-latest",
        messages=[{"role": "user", "content": prompt}]
    )
    gpt_output = response.choices[0].message.content
    cleaned = clean_gpt_json(gpt_output)
    report_json = json.loads(cleaned)

    html_body = format_report_html(report_json)
    send_email(sender_email, cc_emails, "Your Compliance Report", html_body, original_msg=original_msg)
    return report_json

# ================================
# FASTAPI ENDPOINT
# ================================
@app.post("/review")
async def review_endpoint(payload: EmailPayload):
    if payload.sender not in ALLOWED_SENDERS:
        raise HTTPException(status_code=403, detail="Sender not allowed")
    try:
        pdf_bytes = base64.b64decode(payload.file)
        report = review_pdf(pdf_bytes, payload.sender)
        return JSONResponse(content=report)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ================================
# IMAP EMAIL LISTENER
# ================================
def email_listener_loop():
    while True:
        try:
            mail = imaplib.IMAP4_SSL(IMAP_HOST)
            mail.login(IMAP_EMAIL, IMAP_PASSWORD)
            mail.select("inbox")
            status, messages = mail.search(None, '(UNSEEN)')
            if status != "OK":
                time.sleep(30)
                continue

            for num in messages[0].split():
                status, msg_data = mail.fetch(num, '(RFC822)')
                if status != "OK":
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                sender_email = email.utils.parseaddr(msg.get("From"))[1]

                # ✅ Extract CC recipients robustly
                cc_emails = [addr for name, addr in getaddresses(msg.get_all("Cc", []))]

                if sender_email not in ALLOWED_SENDERS:
                    # not allowed to trigger review, skip
                    mail.store(num, '+FLAGS', '\\Seen')
                    continue

                # Process PDF attachments
                for part in msg.walk():
                    if part.get_content_type() == "application/pdf":
                        pdf_bytes = part.get_payload(decode=True)
                        try:
                            review_pdf(pdf_bytes, sender_email, cc_emails, original_msg=msg)
                        except Exception as e:
                            print(f"Failed to review email from {sender_email}: {e}")

                # Mark email as read
                mail.store(num, '+FLAGS', '\\Seen')

            mail.logout()
        except Exception as e:
            print(f"Email listener error: {e}")
        time.sleep(60)  # poll every 60s

# Run listener thread
threading.Thread(target=email_listener_loop, daemon=True).start()
