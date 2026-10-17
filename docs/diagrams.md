# Entity relationship diagram

Render these Mermaid diagrams directly on GitHub.

```mermaid
erDiagram
    USERS ||--o{ RESERVATIONS : owns
    USERS ||--o{ IDEMPOTENCY : scopes
    EVENTS ||--|{ SEATS : contains
    SEATS ||--o{ RESERVATIONS : history
    RESERVATIONS ||--o| PAYMENTS : attempts
    USERS {
        uuid id PK
        varchar name
        text token_hash UK
    }
    EVENTS {
        int id PK
        text name
        timestamptz opens_at
    }
    SEATS {
        int id PK
        int event_id FK
        text label
    }
    RESERVATIONS {
        uuid id PK
        uuid user_id FK
        int seat_id FK
        text status
        timestamptz created_at
        timestamptz expires_at
        timestamptz confirmed_at
        timestamptz cancelled_at
    }
    PAYMENTS {
        uuid id PK
        uuid reservation_id FK,UK
        text status
        text outcome
        timestamptz due_at
        timestamptz created_at
    }
    IDEMPOTENCY {
        uuid user_id PK,FK
        varchar key PK
        text fingerprint
        jsonb response
        int code
        timestamptz created_at
    }
```

`seats(event_id,label)` is unique. A partial unique index allows only one `held` or `confirmed` reservation per seat. Idempotency has the composite primary key `(user_id,key)`.

# Booking workflow

```mermaid
flowchart TD
    A[Authenticated request + idempotency key] --> B[Begin transaction; lock user/key]
    B --> C{Recorded request?}
    C -->|Same fingerprint| R[Replay original response]
    C -->|Different fingerprint| X[409 Conflict]
    C -->|New request| D[Lock seat row]
    D --> E[Reconcile expiry / due payment using DB time]
    E --> F{Seat available and booking open?}
    F -->|No| G[Store application error response]
    F -->|Yes| H[Create timed hold]
    H --> I[Store response and commit]
    G --> I
    I --> J[Confirm starts one durable payment]
    J --> K{Reconcile before expiry?}
    K -->|No| L[Expire hold; void pending payment]
    K -->|Yes, payment due| M{Outcome}
    M -->|Success| N[Confirm booking]
    M -->|Failure| O[Cancel hold; release seat]
    N --> P[Cancellation refunds simulated payment]
    L --> Q[Seat can be reserved again]
    O --> Q
    P --> Q
```

# Reservation state machine

```mermaid
stateDiagram-v2
    [*] --> held: reserve available seat
    held --> confirmed: successful payment before deadline
    held --> expired: deadline reached
    held --> cancelled: user cancellation or payment failure
    confirmed --> cancelled: user cancellation + simulated refund
    expired --> [*]
    cancelled --> [*]
```

Reallocation creates a **new** reservation. No terminal reservation is reactivated.
