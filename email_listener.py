import imaplib
import email
import base64
import time
import os
import requests

IMAP_HOST = "imap.gmail.com"
IMAP_USER = os.getenv("SMTP_EMAIL")
IMAP_PASS = os.getenv("SMTP_PASSWORD")  # App password
ALLOWED_SENDERS = ['devaang18@gmail.com', 'neildillon10@gmail.com']
FASTAPI_ENDPOINT = "https://compliance-demo.onrender.com"

def process_mail():
    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    mail.login(IMAP_USER, IMAP_PASS)
    mail.select("inbox")

    status, messages = mail.search(None, 'UNSEEN')
    if status != "OK":
        print("No new messages.")
        return

    for num in messages[0].split():
        status, data = mail.fetch(num, '(RFC822)')
        if status != "OK":
            continue

        msg = email.message_from_bytes(data[0][1])
        sender = email.utils.parseaddr(msg['From'])[1]
        if sender not in ALLOWED_SENDERS:
            continue

        for part in msg.walk():
            if part.get_content_type() == "application/pdf":
                filename = part.get_filename()
                file_data = part.get_payload(decode=True)
                file_b64 = base64.b64encode(file_data).decode()

                payload = {
                    "sender": sender,
                    "filename": filename,
                    "file": file_b64
                }

                try:
                    r = requests.post(FASTAPI_ENDPOINT, json=payload)
                    print(f"Sent {filename} to FastAPI: {r.status_code}")
                except Exception as e:
                    print(f"Failed to send {filename}: {str(e)}")

        mail.store(num, '+FLAGS', '\\Seen')  # mark as read

if __name__ == "__main__":
    while True:
        try:
            process_mail()
        except Exception as e:
            print("Error:", e)
        time.sleep(60)  # check every 60 seconds
