# Bulk Email Scheduler For AI Content Workflows

Python script that sends emails from a local address list with:

- daily and hourly caps
- per domain caps
- local quiet hours
- randomised delays
- optional BCC and Windows hotkeys

The focus is simple automation, not a marketing platform. You keep full control of the recipient list and SMTP credentials.

## Use case

Typical use case:

- You generate draft content with an LLM for a content team.
- You want to distribute these drafts by email in a controlled way.
- You want to avoid sending at night or hitting provider limits.
- You prefer a script that runs on your own machine instead of another SaaS tool.

This script is one building block in such a content automation pipeline.

## Features

- Reads recipients from a plain text file (`emails.txt` by default).
- Logs successful sends with UTC timestamps in `sent_log.txt`.
- Respects:
  - total per day cap (`MAX_PER_DAY_TOTAL`)
  - total per hour cap (`MAX_PER_HOUR_TOTAL`)
  - per domain cap (`MAX_PER_DAY_PER_DOMAIN`)
- Avoids night time sends using local quiet hours.
- Uses a biased random delay between sends to avoid very regular patterns.
- Windows hotkeys (if `msvcrt` is available):
  - `q` quit
  - `t` send a test email
  - `o` toggle default BCC on or off
- Test mode with `--test` flag to send one test email and exit.

## Limitations

- No tracking, no open rate statistics.
- No unsubscribe logic, you must handle that yourself.
- No retries for soft errors at the moment, errors are printed and the script moves on.
- No template engine, content is a simple text and HTML body.

For many teams this is fine, especially if you mainly need a safe and boring way to send occasional batches from a small list.

## Requirements

- Python 3.10 or newer.
- SMTP account that supports SSL on port 465 or similar.
- Basic command line usage.

No external Python packages are required, only the standard library.

## Installation

Clone the repository and switch into it:

```bash
git clone https://github.com/your-account/bulk-email-scheduler.git
cd bulk-email-scheduler
