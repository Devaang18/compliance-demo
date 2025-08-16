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
from email.mime.application import MIMEApplication
from dotenv import load_dotenv
import os
load_dotenv()

# ================================
# CONFIGURATION
# ================================
ALLOWED_SENDERS = os.getenv("ALLOWED_SENDERS")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SMTP_EMAIL =  os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = os.getenv("SMTP_PORT", 465)

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
    """Extract text from PDF using pdfplumber"""
    text = ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    return text

def format_report_html(report_json):
    """Format compliance report as HTML email"""
    summary = report_json.get("summary", "")
    issues = report_json.get("issues", [])

    html = f"""
    <html>
    <body>
        <p>Dear User,</p>
        <p>Here is your Compliance Review Report:</p>
        <h3>Summary:</h3>
        <p>{summary}</p>
        <h3>Detailed Issues Found:</h3>
        <table border="1" cellpadding="5" cellspacing="0">
            <tr style="background-color:#f2f2f2;">
                <th>ID</th>
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
                <td>{issue.get('id')}</td>
                <td>{issue.get('category')}</td>
                <td>{issue.get('severity')}</td>
                <td>{issue.get('regulation_reference')}</td>
                <td>{issue.get('exact_violation_text')}</td>
                <td>{issue.get('rule_description')}</td>
                <td>{issue.get('recommendation')}</td>
            </tr>
        """
    html += """
        </table>
        <p>Thank you,<br>Compliance AI Bot</p>
    </body>
    </html>
    """
    return html

def send_email(to_email: str, subject: str, html_body: str):
    """Send HTML email via Gmail SMTP"""
    msg = MIMEMultipart("alternative")
    msg['Subject'] = subject
    msg['From'] = SMTP_EMAIL
    msg['To'] = to_email
    part = MIMEText(html_body, "html")
    msg.attach(part)
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)

# ================================
# ENDPOINT: /review
# ================================
@app.post("/review")
async def review_email(payload: EmailPayload):
    sender = payload.sender
    if sender not in ALLOWED_SENDERS:
        raise HTTPException(status_code=403, detail="Sender not allowed")

    # Decode PDF
    try:
        pdf_bytes = base64.b64decode(payload.file)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error decoding PDF: {str(e)}")

    # Extract text
    try:
        chunk_text = extract_text_from_pdf(tmp_path)
        if not chunk_text.strip():
            raise HTTPException(status_code=400, detail="PDF contains no extractable text")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF extraction error: {str(e)}")

    # GPT-5 Compliance Prompt
    prompt = f"""
You are a compliance expert specializing in gambling, marketing, and legal regulations. Review the following document text and identify violations of SPECIFIC regulated policy rules from these industries.

IMPORTANT: Only reference ACTUAL regulated policy rules from:
- Gambling regulations (e.g., UK Gambling Act 2005, US UIGEA, EU gambling directives)
- Marketing regulations (e.g., CAP Code, FTC guidelines, ASA regulations)
- Legal compliance (e.g., GDPR, consumer protection laws, financial regulations)

DO NOT create or reference made-up rules. Only use established regulatory frameworks.

For each violation found, you must:
1. Reference the SPECIFIC regulation/act/section
2. Quote the EXACT line from user's document that violates it
3. Explain how it violates the regulation

Return ONLY a JSON object with keys:
- "issues": list of compliance issues found. Each issue must have:
    - id (short string),
    - category (string: "Gambling", "Marketing", "Legal"),
    - severity ("Low","Medium","High"),
    - regulation_reference (string: specific regulation/act/section),
    - exact_violation_text (string: quote the exact line from user's document),
    - rule_description (string: what the regulation requires),
    - recommendation (string: how to fix the violation)
- "summary": a short 2-3 sentence summary of this chunk

Document text to review:
\"\"\"{chunk_text}
\"\"\"

Return valid JSON only.
"""

    # GPT-5 API Call
    try:
        response = openai.chat.completions.create(
            model="gpt-5-chat-latest",
            messages=[{"role": "user", "content": prompt}]
        )
        gpt_output = response.choices[0].message.content
        report_json = json.loads(gpt_output)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GPT-5 processing error: {str(e)}")

    # Send Email Back
    try:
        html_body = format_report_html(report_json)
        send_email(sender, "Your Compliance Report", html_body)
    except Exception as e:
        print(f"Warning: failed to send email: {str(e)}")

    return JSONResponse(content=report_json)
