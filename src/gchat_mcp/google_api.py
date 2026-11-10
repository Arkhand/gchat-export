"""The only module that talks to Google: OAuth and the Chat and People APIs.

The MCP server never imports it; it runs the CLI as a subprocess instead.
"""
import datetime as dt
import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    # The Chat API never returns people's names, neither in sender.displayName
    # nor in memberships: only users/NNN ids. Names come from the Workspace
    # directory through the People API.
    "https://www.googleapis.com/auth/chat.memberships.readonly",
    "https://www.googleapis.com/auth/directory.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


RETRIES = 3


class AuthRequired(Exception):
    """Raised instead of opening a browser when running non-interactively."""


def get_creds(auth_dir, reauth=False, interactive=True, log=print):
    """Credentials for the Workspace account; files live in auth_dir."""
    creds_file = os.path.join(auth_dir, "credentials.json")
    token_file = os.path.join(auth_dir, "token.json")
    if reauth and os.path.exists(token_file):
        os.remove(token_file)
        log("[i] token.json removed; authorization required.")

    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            # Google rejected the token (revoked, expired grant): re-authorize. A network
            # failure is not this; it propagates as an error instead of asking for --reauth,
            # which would delete a token that still works.
            log(f"[!] Token refresh rejected ({e}).")
            creds = None
    if not creds or not creds.valid:
        # A caller running unattended must never hang waiting for a browser.
        if not interactive:
            raise AuthRequired("authorization required: run once without --non-interactive")
        if not os.path.exists(creds_file):
            raise SystemExit(f"[X] Missing {creds_file}. See SETUP.md, step 4.")
        log("[i] Open the URL below with the Workspace account (not a personal Gmail).")
        flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
        creds = flow.run_local_server(port=0)
    os.makedirs(auth_dir, exist_ok=True)
    with open(token_file, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    return creds


def whoami(creds):
    """Email of the authorized account, so the wrong account is noticed early."""
    try:
        svc = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        return svc.userinfo().get().execute(num_retries=RETRIES).get("email") or "?"
    except Exception:
        return "?"


def _pages(make_request):
    token = None
    while True:
        resp = make_request(token).execute(num_retries=RETRIES)
        yield resp
        token = resp.get("nextPageToken")
        if not token:
            return


def list_spaces(svc):
    spaces = []
    for resp in _pages(lambda t: svc.spaces().list(pageSize=1000, pageToken=t)):
        spaces.extend(resp.get("spaces", []))
    return spaces


def list_members(svc, space_name):
    """Human member ids of a space. Chat never returns their names."""
    ids = set()
    for resp in _pages(lambda t: svc.spaces().members().list(
            parent=space_name, pageSize=1000, pageToken=t)):
        for ms in resp.get("memberships", []):
            user = ms.get("member") or {}
            if user.get("name") and user.get("type") == "HUMAN":
                ids.add(user["name"])
    return sorted(ids)


def list_messages(svc, space_name, since, until):
    """Messages created in [since, until), both dates in UTC."""
    # The filter only supports strict < and >, so start a hair before midnight.
    start = dt.datetime.combine(since, dt.time()) - dt.timedelta(microseconds=1)
    flt = (f'createTime > "{start:%Y-%m-%dT%H:%M:%S.%f}Z" '
           f'AND createTime < "{until.isoformat()}T00:00:00Z"')
    msgs = []
    for resp in _pages(lambda t: svc.spaces().messages().list(
            parent=space_name, filter=flt, pageSize=1000, pageToken=t)):
        msgs.extend(resp.get("messages", []))
    return msgs


def people_lookup(creds, log=print):
    """Returns lookup(user_ids) -> {user_id: (name, email)} backed by People API."""
    try:
        svc = build("people", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        log(f"[!] People API unavailable ({e}); people will show as users/NNN.")
        return lambda ids: {}

    def lookup(user_ids):
        found = {}
        for i in range(0, len(user_ids), 50):  # getBatchGet takes up to 50
            batch = user_ids[i:i + 50]
            try:
                resp = svc.people().getBatchGet(
                    resourceNames=[u.replace("users/", "people/") for u in batch],
                    personFields="names,emailAddresses",
                ).execute(num_retries=RETRIES)
            except HttpError as e:
                log(f"[!] People API failed: HTTP {e.resp.status}")
                continue
            for r in resp.get("responses", []):
                person = r.get("person") or {}
                rn = person.get("resourceName") or r.get("requestedResourceName") or ""
                email = next((e.get("value", "").strip()
                              for e in person.get("emailAddresses") or [] if e.get("value")), "")
                name = next((n.get("displayName", "").strip()
                             for n in person.get("names") or [] if n.get("displayName")), "")
                name = name or email.split("@")[0]
                if rn and name:
                    found[rn.replace("people/", "users/")] = (name, email)
        return found

    return lookup
