# Technical Design Manual — BioAudit

## 1. Introduction

Purpose: describe BioAudit's system design through Functional Hierarchy Diagram, Data Flow Diagrams, Entity Diagram, Sequence Diagrams. Wireframes and full field-level DB design out of scope this pass — diagrams only, sourced from URS (FYP-26-S3-14) and PRD, cross-checked against actual codebase (`bioaudit/models.py`, `backend/src/constants/index.js`, `backend/src/services/*.js`).

## 2. System Architecture Overview

Two independent codebases:
- **`bioaudit/`** (Python) — CLI, PySide6 GUI, Streamlit dashboard, scanning engine. Runs on tester's machine. Talks to Android device over ADB or reads APK file directly. Detection logic lives entirely here, deterministic, rule-based.
- **`backend/`** (Node/Express) — REST API. No scanning logic. Owns accounts, tier status, scan history, org oversight, AI explanation proxy. Deployed Cloud Run.

External dependents: **Firebase Authentication** (credentials, sessions, custom claims for tier/role/organisationId), **Cloud Firestore** (scan history, org data, invitations, flags, audit log), **Gemini API** (explanation generation only, never detection), **ADB/target Android device** (runtime probing, black-box, no root).

Key boundary: client does the scanning locally (device/APK are on tester's machine), backend only stores results and serves AI explanations. This is why `scans.service.js` says "the scanning itself happens in the desktop app."

## 3. Functional Hierarchy Diagram

```mermaid
flowchart TD
    ROOT[BioAudit]

    ROOT --> UNREG[Unregistered User]
    ROOT --> USR[User — Free/Premium shared]
    ROOT --> FREE[Free User]
    ROOT --> PREM[Premium User]
    ROOT --> ADM[Admin]

    UNREG --> UR1[Register User Account]
    UNREG --> UR2[Join Organisation via Invite]

    USR --> US1[Log In]
    USR --> US2[Log Out]
    USR --> US3[View Profile]
    US3 --> US3a[Update Account Details]
    US3 --> US3b[Change Email]
    US3 --> US3c[Change Password]
    USR --> US4[Delete Account]
    USR --> US5[Join Organisation]
    USR --> US6[Scan APK]
    USR --> US7[Assess Connected Device]
    USR --> US8[Delete Test History]

    FREE --> FR1[View AI Explanation — capped monthly]
    FREE --> FR2[View Test History — capped retention]
    FREE --> FR3[Upgrade to Premium]

    PREM --> PR1[View AI Explanation — uncapped]
    PREM --> PR2[View Test History — uncapped]
    PREM --> PR3[Compare Scan Results]
    PREM --> PR4[Export Scan Report]
    PREM --> PR5[Cancel Subscription]

    ADM --> AD1[Register Admin Account]
    ADM --> AD2[Log In / Log Out / Profile Mgmt]
    ADM --> AD3[Invite Member]
    ADM --> AD4[Cancel Pending Invitation]
    ADM --> AD5[View Member Data]
    AD5 --> AD5a[Remove Member from Organisation]
    AD5 --> AD5b[Delete Member Account]
    AD5 --> AD5c[Flag Data]
    ADM --> AD6[Review Flagged Data]
    ADM --> AD7[Add Admin]
```

Note: Admin branch holds no personal scanning capability — mirrors URS §2.3 ("Admin... has no personal detection or scanning capability").

## 4. Data Flow Diagrams

### 4.1 Context Diagram (Level 0)

```mermaid
flowchart LR
    Tester((Tester / User))
    Admin((Admin))
    Device[/Target Android App — APK or ADB device/]
    Gemini[[Gemini API]]
    FB[(Firebase Auth + Firestore)]

    Tester -->|credentials, APK, run-test commands| SYS
    Admin -->|org mgmt commands| SYS
    SYS[0.0 BioAudit System] -->|scan results, history, explanations| Tester
    SYS -->|member data, flags| Admin
    SYS <-->|probe / read manifest, logcat, allowBackup| Device
    SYS <-->|redacted evidence -> explanation JSON| Gemini
    SYS <-->|auth tokens, profile, scans, org docs| FB
```

### 4.2 Level 1 DFD

```mermaid
flowchart TD
    subgraph Client["bioaudit/ (desktop client)"]
        P1[1.0 Account & Session Mgmt]
        P2[2.0 Assessment Pipeline<br/>static scan / device assess]
        P4[4.0 History & Reporting]
    end

    subgraph Server["backend/ (Node API)"]
        P3[3.0 AI Explanation]
        P5[5.0 Organisation Oversight]
    end

    D1[(Firebase Auth)]
    D2[(Firestore: users)]
    D3[(Firestore: scans / reports)]
    D4[(Firestore: organisations / invitations / flags)]
    D5[(Firestore: auditLog)]
    EXT1[/Target APK / ADB device/]
    EXT2[[Gemini API]]

    Tester((Tester)) --> P1
    P1 <--> D1
    P1 <--> D2

    Tester --> P2
    P2 <--> EXT1
    P2 --> D3

    Tester --> P4
    P4 <--> D3
    P4 --> P3
    P3 <--> EXT2
    P3 --> D2

    Admin((Admin)) --> P5
    P5 <--> D4
    P5 <--> D2
    P5 --> D5
```

Process notes:
- 2.0 never writes to Firestore directly for the *finding content* logic — detection stays client-side and deterministic; only the finished `TestRun`/`Finding` payload is uploaded (US09/US10 post-conditions: "saved to the user's synced assessment history").
- 3.0 enforces tier cap (Free monthly limit) reading/writing `users` doc before calling Gemini — matches FR01 alt flow "Monthly cap reached."
- 5.0 writes every oversight action to `auditLog` (per `audit.service.js` — append-only, no client writes).

## 5. Entity Relationship Diagram

Grounded in `backend/src/constants/index.js` `COLLECTIONS` and the service files (`users.service.js`, `scans.service.js`, `organisations.service.js`). Firestore is document-based — relationships below are logical, not FK-enforced; array fields (`memberIds`, `adminIds`) implement the many-side.

```mermaid
erDiagram
    USER ||--o{ SCAN : "owns (userId)"
    USER }o--o{ ORGANISATION : "member of (organisationId / memberIds[])"
    USER ||--o{ AUDITLOG : "actor / subject"
    ORGANISATION ||--o{ INVITATION : "issues"
    ORGANISATION ||--o{ FLAG : "scopes"
    SCAN ||--o{ REPORT : "exported as"
    SCAN ||--o{ FLAG : "flagged by admin"
    SCAN ||--|{ FINDING : "contains (embedded array)"

    USER {
        string uid PK
        string email
        string displayName
        string tier "free | premium"
        string role "member | admin"
        string organisationId FK
        int scanCount
        bool disabled
        timestamp createdAt
    }

    ORGANISATION {
        string id PK
        string name
        string ownerId FK
        array adminIds
        array memberIds
        timestamp createdAt
    }

    INVITATION {
        string id PK
        string organisationId FK
        string email
        string role
        string tokenHash
        string status "pending|accepted|cancelled|expired"
        string invitedBy FK
        timestamp expiresAt
    }

    SCAN {
        string id PK
        string userId FK
        string organisationId FK
        string type "apk | device"
        object target "packageName, apkFileName, deviceSerial"
        bool authorisationConfirmed
        array findings "embedded Finding[]"
        bool flagged
        string toolVersion
        timestamp createdAt
    }

    FINDING {
        string id PK
        string category
        string title
        string severity "critical|high|medium|low|info"
        array owasp
        string evidence
        string source
        string confidence "confirmed|likely"
        string explanation "AI-filled"
        string mitigation "AI-filled"
    }

    REPORT {
        string id PK
        string scanId FK
        string generatedBy FK
        string html
        timestamp createdAt
    }

    FLAG {
        string id PK
        string organisationId FK
        string scanId FK
        string subjectId FK
        string reason
        string flaggedBy FK
        string status "open|reviewed|dismissed"
    }

    AUDITLOG {
        string id PK
        string action
        string actorId FK
        string subjectId FK
        string organisationId FK
        object metadata
        timestamp createdAt
    }
```

## 6. Sequence Diagrams

Six flows chosen for architectural signal (auth, core detection pipeline, tier-gated AI, org oversight) — not all 30+ use cases.

### 6.1 Register + Login (UR01 / US01)

```mermaid
sequenceDiagram
    actor U as Unregistered User
    participant GUI as BioAudit Client
    participant API as Backend API
    participant FBA as Firebase Auth
    participant FS as Firestore

    U->>GUI: Sign Up -> Create Personal Account
    GUI->>API: POST /auth/register {email, password}
    API->>FBA: createUser()
    FBA-->>API: uid
    API->>FS: users.doc(uid).set({tier: free, role: member})
    API->>FBA: setCustomUserClaims(uid, {tier, role})
    API-->>GUI: account created + disclosure notice
    U->>GUI: acknowledge disclosure
    GUI->>API: POST /auth/login {email, password}
    API->>FBA: verify credentials -> ID token
    FBA-->>API: ID token (claims: tier/role/organisationId)
    API-->>GUI: token + profile
    GUI-->>U: dashboard displayed
```

### 6.2 Scan APK (US09)

```mermaid
sequenceDiagram
    actor U as User
    participant GUI as BioAudit Client
    participant SA as static_analysis/
    participant API as Backend API
    participant FS as Firestore

    U->>GUI: Run New Test -> Scan APK
    U->>GUI: confirm authorisation
    U->>GUI: select APK file
    GUI->>SA: parse manifest + DEX identifiers
    SA-->>GUI: debuggable flag, boolean-only auth, allowBackup
    GUI->>GUI: map findings -> OWASP Mobile Top 10 (or "Uncategorised")
    GUI-->>U: display findings (severity, confidence, OWASP)
    GUI->>API: POST /scans {type: apk, findings[], authorisationConfirmed}
    API->>FS: scans.add(doc); enforceRetention(userId, tier)
    FS-->>API: saved
    API-->>GUI: scan id
```

### 6.3 Assess Connected Device (US10)

```mermaid
sequenceDiagram
    actor U as User
    participant GUI as BioAudit Client
    participant ADB as adb.py
    participant RT as runtime/ (ipc_oracle, response_oracle, observers)
    participant DEV as Target Android Device
    participant API as Backend API

    U->>GUI: Run New Test -> Assess Device
    GUI-->>U: authorisation checkbox
    U->>GUI: tick "I confirm I own or am authorised..."
    U->>GUI: select installed app
    GUI->>ADB: read manifest + DEX (static findings)
    GUI->>RT: probe exported components (ipc_oracle)
    RT->>DEV: am start / broadcast exported components
    DEV-->>RT: response / reachability result
    RT-->>GUI: unauthenticated access findings
    GUI->>RT: response_oracle: valid vs invalid identifier
    RT->>DEV: send both, compare responses
    DEV-->>RT: two responses
    RT-->>GUI: auth-state side-channel finding (if diverges)
    GUI->>RT: observers: scan logcat for leaks
    RT->>DEV: logcat read
    DEV-->>RT: log lines
    RT-->>GUI: leaked token/credential findings
    GUI->>GUI: map all findings -> OWASP / severity / confidence
    GUI-->>U: display full assessment results
    GUI->>API: POST /scans {type: device, findings[]}
    API-->>GUI: saved to synced history
```

### 6.4 View AI Explanation with tier cap (FR01 / PR01)

```mermaid
sequenceDiagram
    actor U as Free/Premium User
    participant GUI as BioAudit Client
    participant API as Backend API
    participant RED as utils/redact.js
    participant FS as Firestore (users)
    participant KB as knowledgeBase.js
    participant GEM as Gemini API

    U->>GUI: View AI Explanation
    GUI->>API: POST /scans/:id/explain {findingId}
    API->>FS: read user doc (tier, aiExplanationCount)
    alt tier == free AND count >= monthly cap
        API-->>GUI: 403 cap reached -> prompt upgrade
    else within cap or Premium
        API->>RED: redactFinding(evidence)
        RED-->>API: redacted evidence
        API->>KB: load guidance for finding.category
        API->>GEM: system prompt + redacted evidence + guidance
        GEM-->>API: {explanation, mitigation, references}
        API->>FS: increment aiExplanationCount (Free only)
        API-->>GUI: explanation + fix
    end
    GUI-->>U: display explanation alongside finding
```

### 6.5 Join Organisation via Invite (UR02 / US08)

```mermaid
sequenceDiagram
    actor U as User (new or existing)
    participant GUI as BioAudit Client
    participant API as Backend API
    participant FS as Firestore
    participant FBA as Firebase Auth

    U->>GUI: click invite link (token)
    GUI->>API: GET /invitations/:token
    API->>FS: invitations.where(tokenHash==hash(token), status==pending)
    alt expired or invalid
        API-->>GUI: error -> request new invite
    else valid
        API-->>GUI: invite details (org name, prefilled email)
        U->>GUI: (register if new) / log in / acknowledge disclosure
        GUI->>API: POST /invitations/:token/accept
        API->>FS: transaction: org.memberIds arrayUnion(uid), user.organisationId = orgId
        API->>FS: invitation.status = accepted
        API->>FBA: setCustomUserClaims(uid, {organisationId, role})
        API->>FBA: revokeRefreshTokens(uid)
        API-->>GUI: confirmation, forces re-auth on next request
    end
```

### 6.6 Admin Flag + Review (AD13 / AD14)

```mermaid
sequenceDiagram
    actor A as Admin
    participant GUI as BioAudit Client
    participant API as Backend API
    participant FS as Firestore (flags/scans)
    participant AUD as audit.service.js

    A->>GUI: Flag on member's scan record
    GUI->>API: POST /organisations/:id/flags {scanId, reason}
    API->>FS: check no existing open flag for scanId
    API->>FS: flags.add({status: open, flaggedBy})
    API->>FS: scans.doc(scanId).update({flagged: true})
    API->>AUD: record(MEMBER_DATA_FLAGGED)
    API-->>GUI: confirmation

    A->>GUI: Flagged Items
    GUI->>API: GET /organisations/:id/flags?status=open
    API->>FS: flags.where(organisationId, status==open)
    FS-->>API: flag list
    API-->>GUI: display flags (note, date)
    A->>GUI: mark reviewed
    GUI->>API: PATCH /flags/:id {decision, note}
    API->>FS: flag.status = reviewed
    API->>FS: scans.doc(scanId).update({flagged: false})
    API->>AUD: record(FLAG_REVIEWED)
    API-->>GUI: confirmation
```

## 7. Traceability

| Diagram | URS use cases covered |
|---|---|
| Functional Hierarchy | FEATURE-1..12 across all actor tiers (URS §2.2) |
| DFD Context / Level 1 | All — pipeline stages map to US06/US09/US10, FR01/PR01, AD-series |
| ERD | US03, US09, US10, US11, PR03, PR04, AD08–AD15 |
| Seq: Register + Login | UR01, US01 |
| Seq: Scan APK | US09 |
| Seq: Assess Device | US10 |
| Seq: AI Explanation | FR01, PR01 |
| Seq: Join Org | UR02, US08 |
| Seq: Flag + Review | AD13, AD14 |

Wireframes and full field-level DB schema deferred, not covered in this pass.
