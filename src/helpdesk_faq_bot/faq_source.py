"""Where FAQs come from, and where unanswered questions go.

* ``CsvFaqSource``          - local CSV, great for demos and tests.
* ``GoogleSheetFaqSource``  - live Google Sheet read with a service account, so non-technical
                              staff can edit answers without a redeploy.

Both expose ``load() -> list[FAQEntry]``. ``CachedFaqSource`` wraps either one with a short TTL
so the bot picks up sheet edits quickly without hitting the Sheets API on every message.

Expected columns (header row, case-insensitive):
``Question | Answer | Alternate Phrases | Keyword Rules | Category``
"""

from __future__ import annotations

import csv
import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .models import FAQEntry

log = logging.getLogger(__name__)


class FaqSource(Protocol):
    name: str

    def load(self) -> list[FAQEntry]: ...


def _rows_to_entries(rows) -> list[FAQEntry]:
    entries = []
    for row in rows:
        entry = FAQEntry.from_row(row)
        if entry.question and entry.answer:
            entries.append(entry)
    return entries


class CsvFaqSource:
    name = "csv"

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> list[FAQEntry]:
        with self.path.open(newline="", encoding="utf-8-sig") as fh:
            return _rows_to_entries(csv.DictReader(fh))


class GoogleSheetFaqSource:
    """Reads the FAQ worksheet with a Google service account (share the sheet with its email)."""

    name = "sheets"

    SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)

    def __init__(self, sheet_id: str, credentials_file: str = "", worksheet: str = "FAQ"):
        import gspread  # imported lazily so CSV-only demos don't need Google libs

        if credentials_file:
            # Local/dev: a downloaded service-account JSON key
            self._client = gspread.service_account(filename=credentials_file)
        else:
            # Cloud Run / GKE: keyless Application Default Credentials of the attached service account
            import google.auth

            creds, _ = google.auth.default(scopes=list(self.SCOPES))
            self._client = gspread.authorize(creds)
        self._sheet_id = sheet_id
        self._worksheet = worksheet

    def _ws(self, title: str):
        return self._client.open_by_key(self._sheet_id).worksheet(title)

    def load(self) -> list[FAQEntry]:
        return _rows_to_entries(self._ws(self._worksheet).get_all_records())

    def append_unanswered(self, worksheet: str, question: str, user: str = "") -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._ws(worksheet).append_row([stamp, question, "New", "", user], value_input_option="USER_ENTERED")


class CachedFaqSource:
    """TTL cache in front of another source. Falls back to the last good copy on errors."""

    def __init__(self, inner: FaqSource, ttl_seconds: int = 60):
        self.inner = inner
        self.name = inner.name
        self.ttl = ttl_seconds
        self._entries: list[FAQEntry] | None = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def load(self) -> list[FAQEntry]:
        with self._lock:
            fresh = self._entries is not None and time.monotonic() - self._loaded_at < self.ttl
            if fresh:
                return self._entries  # type: ignore[return-value]
            try:
                self._entries = self.inner.load()
                self._loaded_at = time.monotonic()
            except Exception:  # noqa: BLE001 - keep answering from the last good copy
                if self._entries is None:
                    raise
                log.exception("FAQ reload failed; serving cached copy")
            return self._entries


class UnansweredLog:
    """Append-only JSONL log of questions the bot could not answer (review these to grow the FAQ)."""

    def __init__(self, path: Path | str, sheet: GoogleSheetFaqSource | None = None, sheet_name: str = ""):
        self.path = Path(path)
        self.sheet = sheet
        self.sheet_name = sheet_name

    def record(self, question: str, user: str = "", channel: str = "", reason: str = "no_match") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "question": question,
            "user": user,
            "channel": channel,
            "reason": reason,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        if self.sheet is not None:
            try:
                self.sheet.append_unanswered(self.sheet_name, question, user)
            except Exception:  # noqa: BLE001 - the local log is the source of truth
                log.exception("Could not append unanswered question to Google Sheet")


def build_faq_source(settings) -> tuple[FaqSource, GoogleSheetFaqSource | None]:
    """Pick Sheets when configured (or forced), otherwise the local CSV."""
    mode = settings.faq_source
    use_sheets = mode == "sheets" or (mode == "auto" and settings.sheets_configured)
    if use_sheets and not settings.google_sheet_id:
        raise ValueError("FAQ_SOURCE=sheets needs GOOGLE_SHEET_ID")
    if use_sheets:
        sheet = GoogleSheetFaqSource(settings.google_sheet_id, settings.google_credentials, settings.faq_sheet_name)
        log.info("FAQ source: Google Sheet %s / %s", settings.google_sheet_id, settings.faq_sheet_name)
        return CachedFaqSource(sheet, settings.faq_cache_seconds), sheet
    log.info("FAQ source: CSV %s", settings.faq_csv_path)
    return CachedFaqSource(CsvFaqSource(settings.faq_csv_path), settings.faq_cache_seconds), None
