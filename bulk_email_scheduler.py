#!/usr/bin/env python3
"""
file: bulk_email_scheduler.py

Bulk email sender with rate limiting, quiet hours and simple keyboard control.

Intended use case:
Teams that want to distribute AI generated draft content or other long form content
to a list of recipients, without hitting provider limits or sending at night.

Original version contained project specific credentials and text.
This version is cleaned up and prepared for publishing on GitHub.
"""

import os
import ssl
import smtplib
import time
import random
import datetime
import sys
import math
import socket
from email.message import EmailMessage
from email.headerregistry import Address

try:
    import msvcrt
    HAS_MSVCRT = True
except ImportError:
    HAS_MSVCRT = False

# =======================
# SMTP configuration
# =======================

# Use environment variables in production, default values are only placeholders.
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.example.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "your-address@example.com")
SMTP_PASS = os.getenv("SMTP_PASS")  # do not hardcode secrets here

FROM_NAME = os.getenv("FROM_NAME", "Content Automation")
FROM_ADDR = os.getenv("FROM_ADDR", SMTP_USER)
REPLY_TO = os.getenv("REPLY_TO", FROM_ADDR)
BCC_DEFAULT = os.getenv("BCC_DEFAULT", "")  # optional default BCC

# =======================
# Files
# =======================
EMAILS_FILE = os.getenv("EMAILS_FILE", "emails.txt")
SENT_LOG = os.getenv("SENT_LOG", "sent_log.txt")

# =======================
# Time window and limits
# =======================
# Local quiet hours, no sending within this interval
QUIET_START_HOUR = int(os.getenv("QUIET_START_HOUR", "20"))  # 20:00 local
QUIET_END_HOUR = int(os.getenv("QUIET_END_HOUR", "7"))       # 07:00 local

# Limits to stay under provider caps and keep a human looking pattern
MAX_PER_DAY_TOTAL = int(os.getenv("MAX_PER_DAY_TOTAL", "78"))
MAX_PER_HOUR_TOTAL = int(os.getenv("MAX_PER_HOUR_TOTAL", "12"))
MAX_PER_DAY_PER_DOMAIN = int(os.getenv("MAX_PER_DAY_PER_DOMAIN", "5"))
PER_RUN_LIMIT = int(os.getenv("PER_RUN_LIMIT", "999999"))

TEST_ADDRESS = os.getenv("TEST_ADDRESS", FROM_ADDR)

# =======================
# Email content
# =======================
SUBJECT = os.getenv(
    "EMAIL_SUBJECT",
    "AI assisted content update for your team"
)

HTML_BODY = os.getenv(
    "EMAIL_HTML_BODY",
    """\
<!doctype html>
<html lang="en">
  <body style="font-family: Arial, Helvetica, sans-serif; color:#111; line-height:1.6; font-size:16px; background-color:#ffffff; margin:0; padding:0;">
    <div style="max-width:640px; margin:auto; padding:20px;">
      <p>Hello,</p>
      <p>we use AI assisted content workflows and this email is part of a scheduled batch that shares a new draft article or update.</p>
      <p>You can treat this message as a starting point, adjust the content to your own voice and context, and then publish it through your usual channels.</p>
      <p>If you did not expect this message, or if you do not want to receive similar emails in the future, please reply so that we can remove your address from this list.</p>
      <p>Best regards,<br>The content automation team</p>
    </div>
  </body>
</html>
"""
)

PLAIN_FALLBACK = os.getenv(
    "EMAIL_PLAIN_BODY",
    (
        "Hello,\n\n"
        "we use AI assisted content workflows and this email is part of a scheduled batch that shares a new draft article or update.\n\n"
        "You can treat this message as a starting point, adjust the content to your own voice and context, and then publish it through your usual channels.\n\n"
        "If you did not expect this message, or if you do not want to receive similar emails in the future, please reply so that we can remove your address from this list.\n\n"
        "Best regards,\n"
        "The content automation team\n"
    ),
)

# =======================
# Utilities
# =======================


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def parse_sent_log(path: str):
    rows: list[tuple[str, str]] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                email, *rest = line.split(";")
                ts = rest[0] if rest else ""
                rows.append((email.lower(), ts))
    return rows


def sent_set(rows):
    return {r[0] for r in rows}


def domain_of(addr: str) -> str:
    return addr.split("@")[-1].lower()


def count_today(rows):
    today = datetime.date.today().isoformat()
    return sum(1 for _, ts in rows if ts.startswith(today))


def count_today_domain(rows, dom: str):
    today = datetime.date.today().isoformat()
    return sum(1 for em, ts in rows if ts.startswith(today) and domain_of(em) == dom)


def count_this_hour(rows):
    now = datetime.datetime.now(datetime.timezone.utc)
    y, m, d, h = now.year, now.month, now.day, now.hour

    def within_hour(ts):
        try:
            t = datetime.datetime.fromisoformat(ts)
            t = t.astimezone(datetime.timezone.utc)
            return (t.year, t.month, t.day, t.hour) == (y, m, d, h)
        except Exception:
            return False

    return sum(1 for _, ts in rows if within_hour(ts))


def append_sent(path: str, email: str):
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{email.lower()};{utc_now_iso()}\n")


def load_emails(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found.")
    emails: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            a = line.strip()
            if a and "@" in a:
                emails.append(a)
    return emails


def build_message(to_addr: str, bcc_on: bool = True) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = SUBJECT
    msg["From"] = Address(FROM_NAME, addr_spec=FROM_ADDR)
    msg["To"] = to_addr
    msg["Reply-To"] = REPLY_TO
    if bcc_on and BCC_DEFAULT:
        msg["Bcc"] = BCC_DEFAULT
    msg["X-Content-Automation"] = "yes"
    msg.set_content(PLAIN_FALLBACK, subtype="plain", charset="utf-8")
    msg.add_alternative(HTML_BODY, subtype="html", charset="utf-8")
    return msg


def smtp_send_one(msg: EmailMessage):
    if not SMTP_PASS:
        raise RuntimeError("SMTP_PASS environment variable is not set.")
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=60) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)


# =======================
# Connectivity
# =======================


def has_connectivity(timeout_sec: float = 5.0) -> bool:
    try:
        with socket.create_connection((SMTP_HOST, SMTP_PORT), timeout=timeout_sec):
            return True
    except Exception:
        pass
    try:
        with socket.create_connection(("8.8.8.8", 53), timeout=timeout_sec):
            return True
    except Exception:
        return False


def _hotkey_loop(prompt: str, during_wait: bool = False) -> bool:
    """
    Windows only:
    - q quits the script
    - t sends a test email
    - o toggles default BCC on or off
    """
    if not HAS_MSVCRT:
        # On non Windows systems we ignore hotkeys and keep running.
        return True
    if during_wait:
        print(prompt, end="\r", flush=True)
    while msvcrt.kbhit():
        ch = msvcrt.getwch().lower()
        if ch == "q":
            print("\n[ABORT]")
            return False
        if ch == "t":
            try:
                if not has_connectivity():
                    print("\n[NET] offline, cannot send test email now.")
                else:
                    smtp_send_one(build_message(TEST_ADDRESS, State.bcc_on))
                    print("\n[OK] test email sent.")
            except Exception as e:
                print(f"\n[ERR] test email: {e}")
        if ch == "o":
            State.bcc_on = not State.bcc_on
            print(f"\n[BCC] {'on' if State.bcc_on else 'off'}")
    return True


def wait_for_connectivity(
    status_hint: str = "[NET] offline, waiting for connectivity. t=test, q=quit, o=BCC toggle",
) -> bool:
    while True:
        if has_connectivity():
            return True
        _hotkey_loop(status_hint, during_wait=True)
        time.sleep(2.0)


# =======================
# Scheduling
# =======================


def in_quiet_hours(dt_local: datetime.datetime) -> bool:
    h = dt_local.hour
    return (h >= QUIET_START_HOUR) or (h < QUIET_END_HOUR)


def wait_until(target_local: datetime.datetime) -> bool:
    while True:
        now = datetime.datetime.now()
        if now >= target_local:
            return True
        if not _hotkey_loop(
            "[WAIT] q=quit, t=test, o=BCC toggle",
            during_wait=True,
        ):
            return False
        remaining = (target_local - now).total_seconds()
        time.sleep(min(remaining, 15.0))


def next_start_of_day_at(hour: int, minute: int = 0) -> datetime.datetime:
    now = datetime.datetime.now()
    day = now.date()
    if now.hour >= hour:
        day = day + datetime.timedelta(days=1)
    return datetime.datetime.combine(
        day,
        datetime.time(hour=hour, minute=minute, second=0),
    )


def ensure_business_window():
    now = datetime.datetime.now()
    if in_quiet_hours(now):
        start = next_start_of_day_at(QUIET_END_HOUR, 0)
        print(
            "[INFO] quiet hours active, next start at "
            f"{start.strftime('%Y-%m-%d %H:%M')}. q=quit, t=test, o=BCC toggle"
        )
        ok = wait_until(start)
        if not ok:
            sys.exit(0)


def biased_delay_minutes(now_local: datetime.datetime) -> int:
    """
    Sample a delay in minutes from a log normal distribution, with different
    ranges for typical business hours and other times.

    This is a simple way to avoid very regular sending patterns.
    """
    h = now_local.hour
    if 9 <= h < 12 or 14 <= h < 17:
        lo, hi = 22, 45
        mean = 32
        sigma = 0.40
    else:
        lo, hi = 55, 100
        mean = 75
        sigma = 0.40
    mu = math.log(mean) - (sigma ** 2) / 2
    x = random.lognormvariate(mu, sigma)
    val = int(max(lo, min(hi, round(x))))
    val += random.randint(0, 5)
    return val


def schedule_next(now_local: datetime.datetime, base_delay_min: int) -> datetime.datetime:
    target = now_local + datetime.timedelta(minutes=base_delay_min)
    if in_quiet_hours(target):
        day = target.date()
        if target.hour >= QUIET_START_HOUR:
            day = day + datetime.timedelta(days=1)
        target = datetime.datetime.combine(
            day,
            datetime.time(hour=QUIET_END_HOUR, minute=0, second=0),
        )
    target += datetime.timedelta(seconds=random.randint(5, 45))
    return target


def first_unsent_rotating(
    emails: list[str],
    already_sent: set[str],
    used_domains_today: dict[str, int],
    last_domain: str | None,
    failed_once: set[str],
) -> str | None:
    """
    Choose the next address that has not been sent to, avoids domains that hit
    the per day cap, and tries to rotate domains so that successive sends
    come from different domains when possible.
    """
    candidate_any: str | None = None
    for addr in emails:
        low = addr.lower()
        if low in already_sent or low in failed_once:
            continue
        dom = domain_of(low)
        if used_domains_today.get(dom, 0) >= MAX_PER_DAY_PER_DOMAIN:
            continue
        if last_domain and dom != last_domain:
            return low
        if candidate_any is None:
            candidate_any = low
    return candidate_any


# =======================
# Error classification (optional helper)
# =======================


def is_soft_error(err: Exception) -> bool:
    """
    Classify SMTP errors that might be transient.
    This helper is retained for future use.
    """
    if isinstance(err, smtplib.SMTPResponseException):
        code = err.smtp_code
        if 400 <= code < 500:
            return True
        if code in (421, 450, 451, 452):
            return True
        if code == 535:
            return False
        if 500 <= code < 600:
            return True
    if isinstance(err, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError)):
        return True
    return False


# =======================
# State
# =======================


class State:
    bcc_on = True


# =======================
# Main
# =======================


def main():
    # Direct test mode, send a single test email and exit.
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        if not has_connectivity():
            print("[NET] offline, waiting before sending test email...")
            if not wait_for_connectivity():
                return
        try:
            smtp_send_one(build_message(TEST_ADDRESS, State.bcc_on))
            print("[OK] test email sent.")
        except Exception as e:
            print("[ERR] test email:", e)
        return

    sent_rows = parse_sent_log(SENT_LOG)
    already = sent_set(sent_rows)
    emails = load_emails(EMAILS_FILE)

    today_total = count_today(sent_rows)
    hour_total = count_this_hour(sent_rows)

    used_domains_today: dict[str, int] = {}
    for em, ts in sent_rows:
        if ts.startswith(datetime.date.today().isoformat()):
            d = domain_of(em)
            used_domains_today[d] = used_domains_today.get(d, 0) + 1

    if today_total >= MAX_PER_DAY_TOTAL:
        print(f"[INFO] daily limit reached: {today_total}/{MAX_PER_DAY_TOTAL}.")
        return

    # No night starts
    ensure_business_window()

    if not has_connectivity():
        print("[NET] offline, waiting for connection...")
        if not wait_for_connectivity():
            return

    sent_count = 0
    limit = PER_RUN_LIMIT
    last_domain: str | None = None
    failed_once: set[str] = set()

    while sent_count < limit and today_total < MAX_PER_DAY_TOTAL:
        if hour_total >= MAX_PER_HOUR_TOTAL:
            now = datetime.datetime.now()
            next_hour = (now + datetime.timedelta(hours=1)).replace(
                minute=0, second=0, microsecond=0
            )
            print(
                "[INFO] hourly cap reached: "
                f"{hour_total}/{MAX_PER_HOUR_TOTAL}. "
                f"Next run at {next_hour.strftime('%Y-%m-%d %H:%M')}. "
                "q=quit, t=test, o=BCC toggle"
            )
            if not wait_until(next_hour):
                break
            if not has_connectivity() and not wait_for_connectivity():
                break
            sent_rows = parse_sent_log(SENT_LOG)
            hour_total = count_this_hour(sent_rows)
            continue

        addr = first_unsent_rotating(
            emails,
            already,
            used_domains_today,
            last_domain,
            failed_once,
        )
        if not addr:
            print(
                "[INFO] nothing left to send. "
                "All addresses processed or domain limits reached."
            )
            break

        if not _hotkey_loop("[READY] t=test, q=quit, o=BCC toggle", during_wait=False):
            break

        if not has_connectivity():
            print("[NET] offline, waiting for connection...")
            if not wait_for_connectivity():
                break

        try:
            smtp_send_one(build_message(addr, State.bcc_on))
            print(
                f"[OK] sent: {addr}  "
                f"{'(bcc on)' if State.bcc_on else '(bcc off)'}"
            )
            append_sent(SENT_LOG, addr)  # log on success only
            already.add(addr.lower())
            d = domain_of(addr)
            used_domains_today[d] = used_domains_today.get(d, 0) + 1
            last_domain = d
            sent_count += 1
            today_total += 1
            hour_total += 1
        except Exception as e:
            print(f"[ERR] {addr}: {e}")
            failed_once.add(addr.lower())
            last_domain = domain_of(addr)
            # No waiting here, go straight to the next address.

        if today_total >= MAX_PER_DAY_TOTAL:
            print(
                f"[INFO] daily limit reached: {today_total}/{MAX_PER_DAY_TOTAL}. Stop."
            )
            break

        now_local = datetime.datetime.now()
        base_delay = biased_delay_minutes(now_local)
        target = schedule_next(now_local, base_delay)
        print(
            f"[PLAN] next send at "
            f"{target.strftime('%Y-%m-%d %H:%M')}. "
            "q=quit, t=test, o=BCC toggle"
        )
        if not wait_until(target):
            break
        if not has_connectivity():
            print("[NET] offline, waiting for connection...")
            if not wait_for_connectivity():
                break

    print(f"[DONE] sent: {sent_count} emails.")


if __name__ == "__main__":
    main()
