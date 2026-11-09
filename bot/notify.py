"""Send the daily messages by text (Twilio), Telegram, or just print them.

Pick the channel with the NOTIFY setting: "twilio", "telegram" or "console".
"""
from __future__ import annotations

import os

import requests


def send(message: str) -> None:
    channel = os.environ.get("NOTIFY", "console").lower()
    if channel == "twilio":
        _twilio(message)
    elif channel == "telegram":
        _telegram(message)
    else:
        print("----- message -----")
        print(message)
        print("-------------------")


def _twilio(message: str) -> None:
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, token),
        data={"From": os.environ["TWILIO_FROM_NUMBER"], "To": os.environ["TEXT_TO_NUMBER"], "Body": message},
        timeout=30,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Twilio error {r.status_code}: {r.text[:300]}")


def _telegram(message: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": message},
        timeout=30,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text[:300]}")
