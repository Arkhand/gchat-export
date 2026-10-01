# Setup

One-time steps. Do all of them signed in with **the Workspace account**, not a
personal Gmail: the Chat API does not work with `@gmail.com` accounts.

> The concrete values of this installation (account, project) are at the end, in
> [This installation](#this-installation).

> **Joining a teammate's installation in the same organization?** Skip steps 1 to 4:
> get `credentials.json` from the project owner and follow
> [Using it with another account](README.md#using-it-with-another-account).

## 1. Google Cloud project
https://console.cloud.google.com/projectcreate

Name it `gchat-export` (or anything). If the organization picker offers your
company's domain, choose it.

> If project creation is blocked, your Workspace admin restricted it: ask them to
> allow it, or to create the project and grant you the Editor role.

## 2. Enable both APIs
With the new project selected, click **Enable** on each:

- **Chat API**: https://console.cloud.google.com/apis/library/chat.googleapis.com
- **People API**: https://console.cloud.google.com/apis/library/people.googleapis.com

The People API is not optional. The Chat API never returns people's names, only
ids like `users/104692848...`. Without the People API every sender shows as an id
and direct messages cannot be told apart.

## 3. OAuth consent screen
APIs & Services → OAuth consent screen
- User type: **Internal**. This matters: an internal app needs no Google
  verification, and its refresh token does not expire after 7 days. An External
  app stays in Testing mode and forces a new login every week.
- App name `gchat-export`; your email as support and developer contact. Save.

> If you are sent to "Configure the Chat API" and asked for an app name and avatar:
> that is for building a *bot*. Reading as yourself does not need it; if it insists,
> enter any name and save.

## 4. OAuth client
APIs & Services → Credentials → **Create credentials** → **OAuth client ID**
- Application type: **Desktop app**
- Name: `gchat-export-desktop`
→ Create → **Download JSON**

Save the file next to `gchat_export.py` as exactly **`credentials.json`**.

## 5. Dependencies
```
pip install --user -r requirements.txt
```

## 6. First run
```
python -u gchat_export.py --list-spaces
```
The first run prints a URL to authorize. **Open it with the Workspace account**: the
app is Internal, so a personal account is rejected. The token is then saved in
`token.json` and you are not asked again.

`--list-spaces` downloads no messages: it lists the conversations and starts by
printing `[i] Account: ...`, so you can confirm the account before downloading.

See the [README](README.md) for daily use and all options.

## Permissions requested
All read-only, and only for spaces you are already a member of:

| Scope | Used for |
|---|---|
| `chat.spaces.readonly` | listing conversations |
| `chat.messages.readonly` | reading messages |
| `chat.memberships.readonly` | knowing who is in each conversation |
| `directory.readonly` | turning `users/NNN` into real names |
| `userinfo.email` + `openid` | knowing which account is running |

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Google Chat API is only available to Google Workspace users` | signed in with a personal account, not the Workspace one |
| `403 PERMISSION_DENIED` when listing | the admin enabled app access control (Admin console → Security → API controls); the app must be trusted |
| `access_denied` in the browser | the consent screen is still in Testing and you are not a tester; switch it to Internal (step 3) |
| `People API has not been used in project` | step 2 is missing: enable the People API |
| Senders show as `users/NNN` | People API missing, or an old token: run with `--reauth` |
| Exit code 3 with `--non-interactive` | the token is gone or revoked: run once interactively to authorize |

---

## This installation

| Item | Value |
|---|---|
| Account | `dmusial@blueboot.com` (Workspace), NOT the personal Gmail |
| GCP project | `gchat-export-508011`, in the `blueboot.com` organization |
| OAuth client | `gchat-export-desktop`, Desktop app |
| Consent screen | Internal |

Note: Chrome opens with the personal account by default. When authorizing or using
the GCP console, check the account in the top-right corner first.
