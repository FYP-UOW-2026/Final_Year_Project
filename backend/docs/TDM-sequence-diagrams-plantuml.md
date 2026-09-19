# BioAudit — Sequence Diagrams (PlantUML)

Same six flows as `TDM-sequence-diagrams-text.md`, converted to PlantUML syntax. Solid
arrow (`->`) = call/request/instruction; dashed arrow (`-->`) = the return value/response
for that call, drawn sender-to-receiver (PlantUML convention, not reversed). Paste any
block between `@startuml`/`@enduml` into plantuml.com/plantuml or a local renderer.

Each diagram carries a `skinparam` block approximating the requested draw.io lifeline
style (`shape=umlLifeline`, rounded, `#181818` stroke, black 14pt font) as closely as
PlantUML's styling model allows — PlantUML has no raw mxGraph shape/style import, so
this is a visual approximation via `skinparam`, not a literal translation of that string.

Section 7 at the bottom adds the database design (entity relationship diagram) in the
same PlantUML/styling convention, so this file covers both process and data views.

## 1. Register + Login (UR01 / US01)

```plantuml
@startuml
title BioAudit Sequence Diagram - Register + Login

skinparam roundCorner 5
skinparam ParticipantBorderColor #181818
skinparam ParticipantBorderThickness 0.5
skinparam ParticipantFontColor #000000
skinparam ParticipantFontSize 14
skinparam ActorBorderColor #181818
skinparam ActorFontColor #000000
skinparam ActorFontSize 14
skinparam SequenceLifeLineBorderColor #181818
skinparam SequenceLifeLineBorderThickness 0.5

actor User
participant GUI
participant API
participant "Firebase Auth" as FBA
participant Firestore

User -> GUI: Sign Up, then Create Personal Account
GUI -> API: register(email, password)
API -> FBA: createUser()
FBA --> API: uid
API -> Firestore: create user doc, tier=free, role=member
API -> FBA: setCustomUserClaims(tier, role)
API --> GUI: account created, show disclosure
User -> GUI: acknowledge disclosure
GUI -> API: login(email, password)
API -> FBA: verify credentials
FBA --> API: ID token
API --> GUI: token + profile
GUI -> User: show dashboard
@enduml
```

## 2. Scan APK (US09)

```plantuml
@startuml
title BioAudit Sequence Diagram - Scan APK

skinparam roundCorner 5
skinparam ParticipantBorderColor #181818
skinparam ParticipantBorderThickness 0.5
skinparam ParticipantFontColor #000000
skinparam ParticipantFontSize 14
skinparam ActorBorderColor #181818
skinparam ActorFontColor #000000
skinparam ActorFontSize 14
skinparam SequenceLifeLineBorderColor #181818
skinparam SequenceLifeLineBorderThickness 0.5

actor User
participant GUI
participant "Static Analysis" as SA
participant API
participant Firestore

User -> GUI: Run New Test, then Scan APK
User -> GUI: confirm authorisation
User -> GUI: select APK file
GUI -> SA: parse manifest + DEX identifiers
SA --> GUI: debuggable flag, boolean-only auth, allowBackup
GUI -> GUI: map findings to OWASP Mobile Top 10, or "Uncategorised"
GUI -> User: display findings (severity, confidence, OWASP)
GUI -> API: POST /scans {type: apk, findings[], authorisationConfirmed}
API -> Firestore: scans.add(doc)
API -> Firestore: enforceRetention(userId, tier)
Firestore --> API: saved
API --> GUI: scan id
note over SA, GUI: Detection stays client-side and deterministic
@enduml
```

## 3. Assess Connected Device (US10)

```plantuml
@startuml
title BioAudit Sequence Diagram - Assess Connected Device

skinparam roundCorner 5
skinparam ParticipantBorderColor #181818
skinparam ParticipantBorderThickness 0.5
skinparam ParticipantFontColor #000000
skinparam ParticipantFontSize 14
skinparam ActorBorderColor #181818
skinparam ActorFontColor #000000
skinparam ActorFontSize 14
skinparam SequenceLifeLineBorderColor #181818
skinparam SequenceLifeLineBorderThickness 0.5

actor User
participant GUI
participant ADB
participant Device
participant Server
participant Firestore

User -> GUI: Run New Test, then Assess Device
GUI -> User: show authorisation checkbox
User -> GUI: confirm authorisation, select installed app
GUI -> ADB: read manifest + DEX (static findings)
GUI -> Device: probe exported components + auth-state oracle
Device --> GUI: responses
GUI -> Device: read logcat
Device --> GUI: log lines
GUI -> User: display full assessment results
GUI -> Server: POST /scans {type: device, findings[]}
Server -> Firestore: save scan doc
Server --> GUI: saved to synced history
note over GUI, Device: No root access; black-box over standard ADB only
@enduml
```

## 4. View AI Explanation with tier cap (FR01 / PR01)

```plantuml
@startuml
title BioAudit Sequence Diagram - View AI Explanation

skinparam roundCorner 5
skinparam ParticipantBorderColor #181818
skinparam ParticipantBorderThickness 0.5
skinparam ParticipantFontColor #000000
skinparam ParticipantFontSize 14
skinparam ActorBorderColor #181818
skinparam ActorFontColor #000000
skinparam ActorFontSize 14
skinparam SequenceLifeLineBorderColor #181818
skinparam SequenceLifeLineBorderThickness 0.5

actor User
participant GUI
participant API
participant Firestore
participant Redact
participant "Knowledge Base" as KB
participant Gemini

User -> GUI: View AI Explanation
GUI -> API: POST /scans/:id/explain {findingId}
API -> Firestore: read user doc (tier, aiExplanationCount)
note over API, Firestore: If Free and count >= monthly cap, respond 403 and prompt upgrade
API -> Redact: redactFinding(evidence)
Redact --> API: redacted evidence
API -> KB: load guidance for finding.category
KB --> API: reviewed guidance
API -> Gemini: system prompt + redacted evidence + guidance
Gemini --> API: {explanation, mitigation, references}
API -> Firestore: increment aiExplanationCount (Free only)
API --> GUI: explanation + fix
GUI -> User: display explanation alongside finding
@enduml
```

## 5. Join Organisation via Invite (UR02 / US08)

```plantuml
@startuml
title BioAudit Sequence Diagram - Join Organisation via Invite

skinparam roundCorner 5
skinparam ParticipantBorderColor #181818
skinparam ParticipantBorderThickness 0.5
skinparam ParticipantFontColor #000000
skinparam ParticipantFontSize 14
skinparam ActorBorderColor #181818
skinparam ActorFontColor #000000
skinparam ActorFontSize 14
skinparam SequenceLifeLineBorderColor #181818
skinparam SequenceLifeLineBorderThickness 0.5

actor User
participant GUI
participant API
participant Firestore
participant "Firebase Auth" as FBA

User -> GUI: click invite link (token)
GUI -> API: GET /invitations/:token
API -> Firestore: invitations.where(tokenHash==hash(token), status==pending)
Firestore --> API: invitation record (or none)
note over API, Firestore: Expired or invalid, respond with error and request new invite
API --> GUI: invite details (org name, prefilled email)
User -> GUI: register/login, then acknowledge disclosure
GUI -> API: POST /invitations/:token/accept
API -> Firestore: transaction - org.memberIds arrayUnion(uid)
API -> Firestore: transaction - user.organisationId = orgId
API -> Firestore: invitation.status = accepted
API -> FBA: setCustomUserClaims(uid, {organisationId, role})
API -> FBA: revokeRefreshTokens(uid)
API --> GUI: confirmation, forces re-auth on next request
@enduml
```

## 6. Admin Flag + Review (AD13 / AD14)

```plantuml
@startuml
title BioAudit Sequence Diagram - Admin Flag + Review

skinparam roundCorner 5
skinparam ParticipantBorderColor #181818
skinparam ParticipantBorderThickness 0.5
skinparam ParticipantFontColor #000000
skinparam ParticipantFontSize 14
skinparam ActorBorderColor #181818
skinparam ActorFontColor #000000
skinparam ActorFontSize 14
skinparam SequenceLifeLineBorderColor #181818
skinparam SequenceLifeLineBorderThickness 0.5

actor Admin
participant GUI
participant API
participant Firestore
participant "Audit Log" as AuditLog

Admin -> GUI: Flag on member's scan record
GUI -> API: POST /organisations/:id/flags {scanId, reason}
API -> Firestore: check no existing open flag for scanId
API -> Firestore: flags.add({status: open, flaggedBy})
API -> Firestore: scans.doc(scanId).update({flagged: true})
API -> AuditLog: record(MEMBER_DATA_FLAGGED)
API --> GUI: confirmation

Admin -> GUI: open Flagged Items
GUI -> API: GET /organisations/:id/flags?status=open
API -> Firestore: flags.where(organisationId, status==open)
Firestore --> API: flag list
API --> GUI: display flags (note, date)
Admin -> GUI: mark reviewed
GUI -> API: PATCH /flags/:id {decision, note}
API -> Firestore: flag.status = reviewed
API -> Firestore: scans.doc(scanId).update({flagged: false})
API -> AuditLog: record(FLAG_REVIEWED)
API --> GUI: confirmation
note over Admin, API: Admin never modifies scan findings themselves (URS 2.3)
@enduml
```

## 7. Database Design (Entity Relationship Diagram)

Grounded in `backend/src/constants/index.js` (`COLLECTIONS`) and the service files
(`users.service.js`, `scans.service.js`, `organisations.service.js`) — same source as
the Mermaid ERD in `TDM-technical-design-manual.md` §5, redrawn here in PlantUML. Firestore
is document-based, so these are logical relationships, not enforced foreign keys; the
array fields (`memberIds`, `adminIds`) implement the many-side of the User–Organisation
relationship.

```plantuml
@startuml
title BioAudit Database Design - Entity Relationship Diagram

skinparam titleFontSize 26
skinparam roundCorner 5
skinparam EntityBorderColor #181818
skinparam EntityBorderThickness 0.5
skinparam EntityFontColor #000000
skinparam EntityFontSize 14
skinparam EntityBackgroundColor #FFFFFF
skinparam ArrowFontSize 14
hide circle

entity USER {
  * uid : string <<PK>>
  --
  email : string
  displayName : string
  tier : string "free | premium"
  role : string "member | admin"
  organisationId : string <<FK>>
  scanCount : int
  disabled : bool
  createdAt : timestamp
}

entity ORGANISATION {
  * id : string <<PK>>
  --
  name : string
  ownerId : string <<FK>>
  adminIds : array
  memberIds : array
  createdAt : timestamp
}

entity INVITATION {
  * id : string <<PK>>
  --
  organisationId : string <<FK>>
  email : string
  role : string
  tokenHash : string
  status : string "pending|accepted|cancelled|expired"
  invitedBy : string <<FK>>
  expiresAt : timestamp
}

entity SCAN {
  * id : string <<PK>>
  --
  userId : string <<FK>>
  organisationId : string <<FK>>
  type : string "apk | device"
  target : object
  authorisationConfirmed : bool
  findings : array "embedded Finding[]"
  flagged : bool
  toolVersion : string
  createdAt : timestamp
}

entity FINDING {
  * id : string <<PK>>
  --
  category : string
  title : string
  severity : string "critical|high|medium|low|info"
  owasp : array
  evidence : string
  source : string
  confidence : string "confirmed|likely"
  explanation : string
  mitigation : string
}

entity REPORT {
  * id : string <<PK>>
  --
  scanId : string <<FK>>
  generatedBy : string <<FK>>
  html : string
  createdAt : timestamp
}

entity FLAG {
  * id : string <<PK>>
  --
  organisationId : string <<FK>>
  scanId : string <<FK>>
  subjectId : string <<FK>>
  reason : string
  flaggedBy : string <<FK>>
  status : string "open|reviewed|dismissed"
}

entity AUDITLOG {
  * id : string <<PK>>
  --
  action : string
  actorId : string <<FK>>
  subjectId : string <<FK>>
  organisationId : string <<FK>>
  metadata : object
  createdAt : timestamp
}

USER ||--o{ SCAN : "owns (userId)"
USER }o--o{ ORGANISATION : "member of"
USER ||--o{ AUDITLOG : "actor / subject"
ORGANISATION ||--o{ INVITATION : "issues"
ORGANISATION ||--o{ FLAG : "scopes"
SCAN ||--o{ REPORT : "exported as"
SCAN ||--o{ FLAG : "flagged by admin"
SCAN ||--|{ FINDING : "contains (embedded array)"

@enduml
```
