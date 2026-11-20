"""Gmail（SMTP）でメール送信 — SMTP_USER / SMTP_PASS / MAIL_TO が設定されているときだけ"""
import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def send_mail(subject, html_body, attachment=None):
    user, pw, to = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS"), os.environ.get("MAIL_TO")
    if not (user and pw and to):
        log.info("メール設定なし（SMTP_USER / SMTP_PASS / MAIL_TO）のため送信しません")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content("HTMLメールを表示できる環境でご覧ください。")
    msg.add_alternative(html_body, subtype="html")
    if attachment and os.path.exists(attachment):
        with open(attachment, "rb") as f:
            msg.add_attachment(f.read(), maintype="application",
                               subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               filename=os.path.basename(attachment))
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port) as s:
        s.login(user, pw)
        s.send_message(msg)
    log.info("メール送信：%s", to)
    return True
