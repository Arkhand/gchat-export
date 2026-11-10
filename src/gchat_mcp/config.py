"""Limits that protect the model's context, timeouts, and where the store lives."""

import os
from datetime import datetime, tzinfo

from .store import DB_NAME, data_dir

ROW_TEXT_CHARS = 300           # text in list/search/changes rows
QUOTE_CHARS = 200              # quoted or forwarded text in rows
THREAD_TEXT_CHARS = 4000       # per message in get_thread
MESSAGES_DEFAULT, MESSAGES_MAX = 100, 300
CHANGES_DEFAULT, CHANGES_MAX = 100, 300
SEARCH_DEFAULT, SEARCH_MAX = 20, 50
THREAD_MAX = 200
SYNC_TIMEOUT = 120             # s, incremental download (normally 3-5 s)
SYNC_MONTH_TIMEOUT = 600       # s, backfill of a whole month


def local_tz() -> tzinfo:
    """This machine's time zone; tool dates are read and shown in it."""
    return datetime.now().astimezone().tzinfo


def db_path() -> str:
    """The store: $GCHAT_DATA_DIR or ~/gchat-export, then gchat.db."""
    return os.path.join(data_dir(), DB_NAME)
