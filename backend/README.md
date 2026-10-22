# BioAudit Backend

REST API for the BioAudit biometric authentication security tool. Built with **Express.js**,
**Cloud Firestore**, and **Firebase Authentication**.

## What this service does, and what it does not

The backend **does not run the security scans**. The APK file and the USB-connected phone are
both on the user's own machine, so scanning stays in the desktop app. This service handles
everything around it:

| The API does | The desktop app does |
|---|---|
| Accounts, sign-in, sessions | Reading the APK's manifest and code |
| Storing and serving scan history | Talking to the phone over ADB |
| Enforcing student and team limits | Deciding what counts as a finding |
| Generating AI explanations | Converting an exported report to PDF |
| Organisations, invitations, member oversight | |

Two consequences worth noting. The AI provider keys (Gemini by default, Groq as fallback) live on the server, so it is never shipped
inside a downloadable program and can be replaced centrally. And detection stays deterministic:
the rules in the desktop app decide what a problem is, and the AI here only puts a confirmed
finding into readable words.

## Requirements

- Node.js 18.17 or newer
- A Firebase project with **Firestore** and **Email/Password authentication** enabled
- A Gemini API key (default provider) and/or a Groq API key (fallback, free from
  console.groq.com), only if you want AI explanations

## Setup

```bash
cd backend
npm install
cp .env.example .env      # then fill it in
npm run dev
```

Filling in `.env` needs three things from the Firebase console:

1. **Service account key.** Project settings, Service accounts, Generate new private key. Save
   the JSON as `backend/serviceAccountKey.json`. It is gitignored, and it must stay that way:
   the key grants full access to your project.
2. **Web API key.** Project settings, General, Web API Key. The Admin SDK cannot check a
   password, so sign-in calls Google's Identity Toolkit with this key.
3. **Gemini and/or Groq API key**, if you want explanations. Leave it blank and the rest of the API works
   normally, with the explain endpoint reporting that the feature is switched off.

Then deploy the database rules:

```bash
firebase deploy --only firestore:rules
```

## Testing

Two suites, neither of which touches a live Firebase project.

```bash
npm test                 # 13 offline checks, no emulator needed
npm run test:integration # 42 checks against the Firebase emulators
```

**`npm test`** boots the real Express app and exercises the health check, the 404 handler,
authentication rejection, request validation, secret redaction, scan comparison, and report
rendering. It generates a throwaway RSA key at runtime so the Admin SDK accepts the
credential format offline, which means it runs anywhere with no setup.

**`npm run test:integration`** starts the auth and Firestore emulators, runs the full
workflow against them, and shuts them down again. It needs Java 11 or newer, which the
Firestore emulator requires. This covers what the offline suite cannot: real registration
and sign-in, tier limits and history trimming, the team-only features, and the whole
organisation flow including invitations and the flag and review cycle.

This suite has already earned its keep. It caught a response oracle in our own login
endpoint, where an unknown email address produced a different message from a wrong
password, which would have let anyone enumerate accounts. There is now a test that fails
if that difference ever comes back.

To run the Python client's tests you need a server the client can reach:

```bash
npm run dev:emulated     # emulators plus the API on port 4000, leave running
# then, from the repository root:
pytest tests/test_api_client.py -v
```

Those tests skip themselves when no server is listening, so a normal `pytest tests/` run
stays offline.

`.env.emulated` raises the rate limits, because the test suites make far more sign-ins
than a person would and the production defaults would stop a run partway through. The
limits are configurable through `API_RATE_LIMIT_PER_MINUTE` and
`AUTH_RATE_LIMIT_PER_15MIN`; leave them unset in production to get the defaults.

## How authentication works

```
Desktop app                          This API                       Firebase
     |                                   |                              |
     |-- POST /api/auth/login ---------->|                              |
     |                                   |-- verify password ---------->|
     |<-- idToken + refreshToken --------|                              |
     |                                   |                              |
     |-- GET /api/scans ---------------->|                              |
     |   Authorization: Bearer <idToken> |-- verify token ------------->|
     |<-- history ----------------------|                              |
```

The app signs in once and sends the ID token on every later request. Tokens expire after an
hour, so the app calls `POST /api/auth/refresh` with its refresh token to get a new one.

**Tier and role live in the token** as Firebase custom claims, which lets the permission check
run without a database read on each request. Because a token already in someone's hands cannot
be edited, any change to tier or role revokes existing sessions, and the user signs in again to
pick up the new permissions. Endpoints say so in their response when that applies.

## Roles and tiers

There is no paid plan. An account's **tier** follows from whether it belongs to an
organisation, and is never stored:

- **`student`**: a personal account with no organisation. It must verify a university email,
  keeps a limited history, and has a monthly cap on AI explanations.
- **`team`**: any account in an organisation, member or admin. Everything is unlocked.

**Role** is separate: the account's position in an organisation, `member` or `admin`. It gates
oversight of other people. Admins oversee rather than scan, so they cannot save scans.

| | Student | Team member | Team admin |
|---|---|---|---|
| Scan an APK, assess a device | Yes, once verified | Yes | No |
| View test history | Newest 10 | Unlimited | Members' runs, from Team |
| View AI explanation | Monthly cap | Unlimited | — |
| Compare scan results | No | Yes | — |
| Export scan report | No | Yes | — |
| Manage organisation members | No | No | Yes |

Leaving or being removed from an organisation makes an account a student again, and its next
saved scan trims its history to the student limit.

## API reference

All paths are prefixed with `/api`. Every endpoint except registration, login, and refresh needs
`Authorization: Bearer <idToken>`.

### Health

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Service status, and whether AI explanations are configured |
| POST | `/webhooks/mailgun` | Mailgun's bounce reports for verification codes (signed; see Student verification) |

### Authentication

| Method | Path | Diagram function |
|---|---|---|
| POST | `/auth/register/send-code` | Unregistered: email the code a student signs up with |
| POST | `/auth/register/code-status` | Unregistered: whether that code bounced |
| POST | `/auth/register` | Unregistered: Register user account (a student with that code, or an invitee with their invitation token) |
| POST | `/auth/register-admin` | Admin: Register admin account (also creates the organisation) |
| POST | `/auth/login` | User: Log in |
| POST | `/auth/refresh` | Keep a session alive |
| POST | `/auth/logout` | User: Log out |

### Own account

| Method | Path | Diagram function |
|---|---|---|
| GET | `/users/me` | User: View profile |
| PATCH | `/users/me` | User: Update account details |
| POST | `/users/me/email` | User: Change email address |
| POST | `/users/me/password` | User: Change password |
| DELETE | `/users/me` | User: Delete account |
| GET | `/users/me/student-verification` | Student: student verification status |
| POST | `/users/me/student-verification/send` | Student: email a new verification code |
| POST | `/users/me/student-verification/verify` | Student: Verify student email |

Changing an email address or password requires the current password as well as a valid session,
since a stolen token alone should not be enough to take an account over.

**Student verification.** Personal accounts are for students, and a student has to verify their
university email **before** the account is created. `.ac.uk`, `.edu`, `.edu.xx` and `.ac.xx` domains
are accepted, plus any listed in `STUDENT_EMAIL_DOMAINS`. Students pick their university from a
searchable list (`GET /universities`, defined in `src/data/universities.js`), and the email must be one
that university issues; "my university isn't listed" falls back to the domain check above. Signing up
is two calls:

1. `POST /auth/register/send-code` with `{ email, universityId? }` emails a six-digit code. It is
   refused for a non-university address, an address outside the chosen university, one that
   already has an account, or one whose domain does not receive mail according to DNS (MX
   records, or the domain's own address if it has none). That last check catches typos such as
   `ox.ac.ku`. It cannot tell whether the mailbox itself exists, which is why the code is still
   what proves the address, and why the success message repeats the address for the student to
   check. A DNS outage on the server's side skips the check rather than blocking sign-ups, and it
   only runs when email is actually being sent. The reply carries a `sendId` for the next call.
2. While the student waits, `POST /auth/register/code-status` with `{ email, sendId }` says whether
   the code bounced: `{ status: "sent" }`, or `{ status: "bounced", message }` once the university's
   mail server has refused the address, which is how a mailbox that does not exist shows up.
   Clients ask every 5 seconds for 2 minutes, and only after a send that reported
   `delivered: true`. It answers only to the `sendId` holder, so it cannot be used to check which
   mailboxes exist.
3. `POST /auth/register` with `{ email, password, displayName?, universityId?, code }` creates the
   account already verified and returns `{ user, session }`. Without a right code nothing is created.

**Bounces** are reported by Mailgun to `POST /webhooks/mailgun`. Each code email carries its code
document and a random `sendId` as Mailgun variables, and a report only marks that exact send, and
only if its signature checks out against `MAILGUN_WEBHOOK_SIGNING_KEY`. To turn it on, add a
**Permanent failure** webhook in the Mailgun dashboard (Sending → Webhooks) pointing at
`https://<api>/api/webhooks/mailgun`, and copy the dashboard's HTTP webhook signing key into the
server's environment. Without that, every code reads as `sent`. Mailgun also stops sending to an
address once it has bounced; if a real student's address bounced by mistake (say, before their
university mailbox was set up), remove it under Suppressions → Bounces.

The other way to get a personal account is an invitation: `POST /auth/register` with
`{ email, password, displayName?, invitationToken }` creates the account and joins the team in one
step, at any address, since the team sponsors it. A bad token creates nothing.

Changing a student's email lapses the verification. Until the new address is verified (a code is
emailed to it straight away; `/users/me/student-verification/*` re-sends and checks it), the account
cannot save a scan or request an AI explanation, though viewing and deleting existing history stays
open. Every code expires after 15 minutes, allows 5 attempts, and can be re-sent once a minute.

**The website signs students up through the API too.** `firestore.rules` no longer lets a client
create a `users` document with `requestedPlan: 'student'`; only an organisation sign-up may still be
written directly. The website's student form should call the endpoints above (its origin must be
listed in `CORS_ORIGINS`), then sign in with the Firebase client SDK using the same email and
password if it needs a browser session. Like the desktop app, it should ask for the email address
twice and refuse to send a code until both match, since a typo sends the code to someone else's
inbox, or to none. Deploy the rules change with
`firebase deploy --only firestore:rules`.

Codes are emailed through Mailgun (`MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, `MAILGUN_FROM`), or any SMTP
server as a fallback; see `.env.example`, and check the settings with `npm run email:test -- you@x.com`.
Production sends from the verified domain `bioaudit.me` as `BioAudit <no-reply@bioaudit.me>`. A Mailgun
sandbox domain only delivers to its authorized recipients and tends to land in spam, so use it for
trying things out only. If a send fails, a student's existing code is kept and they are told why (a
sandbox refusal gets its own message). With no mail provider configured outside production, the code is
printed to the server log instead; in production, sending is refused with a 503. Email is always off
against the Firebase emulators, so tests never send real mail.

### Scans and history

| Method | Path | Diagram function |
|---|---|---|
| POST | `/scans` | User: Scan APK file, Assess connected device |
| GET | `/scans` | Student and Team: View test history |
| GET | `/scans/:scanId` | One scan with all findings |
| POST | `/scans/:scanId/findings/:index/explain` | Student and Team: View AI explanation |
| GET | `/scans/compare/results` | Team: Compare scan results |
| GET | `/scans/:scanId/report` | Team: Export scan report |
| DELETE | `/scans/:scanId` | User: Delete test history (one) |
| DELETE | `/scans` | User: Delete test history (all) |

### Organisations

| Method | Path | Diagram function |
|---|---|---|
| POST | `/organisations/join` | Join organisation, Join organisation via invite |
| GET | `/organisations/mine` | The caller's own organisation |
| GET | `/organisations/:orgId/members` | Admin: View member data |
| GET | `/organisations/:orgId/members/:uid/scans` | Admin: View member data (their scans) |
| POST | `/organisations/:orgId/invitations` | Admin: Invite team member |
| GET | `/organisations/:orgId/invitations` | Admin: see pending invitations |
| DELETE | `/organisations/:orgId/invitations/:id` | Admin: Cancel pending invitation |
| POST | `/organisations/:orgId/scans/:scanId/flag` | Admin: Flag member data |
| GET | `/organisations/:orgId/flags` | Admin: Review flagged items (list) |
| POST | `/organisations/:orgId/flags/:flagId/review` | Admin: Review flagged items (decide) |
| POST | `/organisations/:orgId/admins` | Admin: Add additional admin |
| DELETE | `/organisations/:orgId/members/:uid` | Admin: Remove organisation member |
| DELETE | `/organisations/:orgId/members/:uid/account` | Admin: Delete member account |
| GET | `/organisations/:orgId/audit` | The record of admin actions |

## Example: saving a scan

```bash
curl -X POST http://localhost:4000/api/scans \
  -H "Authorization: Bearer $ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "device",
    "authorisationConfirmed": true,
    "target": { "packageName": "com.example.app", "deviceSerial": "1b541277" },
    "findings": [
      {
        "category": "exported-auth-bypass",
        "title": "Exported activity reachable without authentication",
        "severity": "critical",
        "owasp": ["M3", "M1"],
        "evidence": "`am start -n com.example.app/.SecretActivity` -> Starting: Intent{...}",
        "source": "ipc_oracle",
        "confidence": "confirmed",
        "component": "com.example.app.SecretActivity"
      }
    ]
  }'
```

A device assessment is rejected unless `authorisationConfirmed` is `true`, so a stored record can
never show an unconfirmed run against a live app.

## Data model

```
users/{uid}                 email, displayName, role, organisationId,
                            studentVerification, aiUsage, scanCount, disabled
organisations/{orgId}       name, ownerId, adminIds[], memberIds[]
invitations/{id}            organisationId, email, role, tokenHash, status, expiresAt
scans/{id}                  userId, organisationId, type, target, findings[],
                            counts, flagged, authorisationConfirmed
flags/{id}                  scanId, organisationId, subjectId, reason, status, review
auditLog/{id}               action, actorId, subjectId, organisationId, metadata, at
studentVerifications/{uid}  email, codeHash, attempts, sentAt, expiresAt
signupVerifications/{hash}  the same, keyed by a SHA-256 of the address (no account yet)
```

Invitation tokens are stored **hashed**, so a copy of the database does not hand out working
invitations. The token itself is returned once when the invitation is created and cannot be
retrieved again.

## Security decisions worth knowing

**Admins cannot read their members' findings.** The APK and raw device data stay on the machine
that scanned them; only redacted findings are uploaded to the account. An admin can see that a member ran a scan and how many
problems it found, which is what oversight needs, but not the evidence pulled out of the
member's app. That read is also written to the audit log.

**The audit log is append-only.** Nothing in the API updates or deletes an entry, and the
Firestore rules deny client writes outright. Reading a member's data, flagging it, removing a
member, and deleting an account are all recorded.

**Login does not say whether an account exists.** A wrong password and an unknown email address
produce the same message. Telling them apart would be a response oracle, which is exactly the
weakness this project detects in other apps, so the API does not have one of its own.

**The Admin SDK bypasses Firestore rules.** Because this service runs with full privileges,
every permission check is enforced in Express. The rules in `firestore.rules` deny almost
everything by default and exist as a second line of defence.

**Rate limits.** 120 requests a minute across the API, and 20 attempts per 15 minutes on
registration and login, since those are the two endpoints worth guessing at.

## Project layout

```
backend/
  src/
    server.js              entry point, graceful shutdown
    app.js                 middleware and route mounting
    config/
      env.js               loads and validates configuration at startup
      firebase.js          Admin SDK setup
    middleware/
      auth.js              token verification, tier and role gates
      validate.js          request validation with zod
      errorHandler.js      one response shape for every failure
    routes/                one file per group, mapped to the diagram
    services/
      firebaseAuth.service.js   password sign-in and token refresh
      users.service.js          accounts and custom claims
      organisations.service.js  membership, invitations, flag and review
      scans.service.js          history, retention, comparison
      aiGraph.service.js        AI explanations, with redaction (the live path; see below)
      groq.service.js           unused, left in place as reference for aiGraph.service.js
      report.service.js         HTML report rendering
      audit.service.js          append-only record of admin actions
    utils/
      redact.js            strips secrets before anything reaches the AI
      ApiError.js          errors that carry an HTTP status
      asyncHandler.js      forwards async errors to the error handler
  firestore.rules          deny-by-default database rules
```

## AI orchestration (LangGraph)

`src/services/aiGraph.service.js` is a LangGraph rebuild of the explanation layer, wired in
at `scans.routes.js` and `reports.routes.js` in place of the old single-shot
`groq.service.js` call, left in the tree unused (`gemini.service.js` has been removed). It routes each
call through `aiModelRouter.js`'s fallback chain (Gemini, then Groq, then any configured
Ollama models, then OpenAI once enabled), trying the next provider when one fails outright.

What it adds over a single-shot call: tool calls instead of one fixed prompt (the model
asks for a finding's evidence, a scan's summary, guidance for a category, or a diff between
two scans, rather than having everything handed to it up front), an output `responseSchema`
per mode instead of prose-described JSON, and a one-shot repair loop for a malformed reply
instead of a hard failure.

```js
import { createSession } from "./services/aiGraph.service.js";

const session = createSession({
  user: req.user,        // bound from the request, never model-supplied
  scanIds: [scan.id],    // allowlist: a tool call naming any other scan is refused
});

const { result } = await session.explain(`Explain finding ${index} in scan ${scan.id}.`);
// result: { explanation, mitigation, references } -- same shape the old single-shot services returned
```

Four modes: `explain` (one finding, matches today's endpoint), `synthesize` (a whole scan),
`compare` (the diff between two scans, narrated), `report` (feeds a future export path).

Every tool's return value passes through one egress guard before it can reach the AI provider:
redacted, capped (800 characters for evidence, 200 for labels), stripped to an explicit
allowlist of keys. `compare_scans` drops evidence from both scans entirely — a diff needs
categories, not two scans' worth of provider rows. This matters because a tool call's return
is appended to the conversation and sent back to the provider on the next turn regardless of which
tool ran first, so the guard has to sit on every tool's output, not just the first one
called.

## Connecting the desktop app

A Python client is already written: `bioaudit/api/client.py`. It uses only the standard
library, so it does not add a dependency to a project whose core deliberately runs without
any.

```python
from bioaudit.api import ApiClient, ApiClientError

client = ApiClient("http://127.0.0.1:4000/api")

try:
    account = client.login("dev@example.com", "password")
    print(account.tier, account.is_team)

    # run_ is a TestRun the local scanning engine produced
    result = client.upload_run(run_, authorised=True)

    explanation = client.explain_finding(result["scan"]["id"], finding_index=0)
    print(explanation["explanation"])
    print(explanation["mitigation"])

    for scan in client.list_history()["scans"]:
        print(scan["createdAt"], scan["counts"])

except ApiClientError as exc:
    # exc.message is written for a person, so a dialog can show it directly
    show_error(exc.message)
```

The client refreshes an expired token and retries the request once, automatically. That
matters because the server revokes sessions whenever a role or organisation changes, so
without it joining a team would break the next call the user made.

CORS allows requests with no `Origin` header, which is what a desktop client sends. Browser
front ends have to be listed in `CORS_ORIGINS`.
