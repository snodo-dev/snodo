---
adr: 001
status: Accepted
---

## 001: PyJWT over custom HMAC

- **Status**: Accepted
- **Context**: The original implementation (`Task 1.3`) used custom SHA-256 HMAC signing with manual signature verification and no standard claims. JWT offers standardised issuance, verification, expiry (standard `iat`/`exp` claims vs ad-hoc timestamps), and built-in tamper detection.
- **Decision**: Replace the custom two-class token system with a single JWT-backed `ValidationToken` wrapping a PyJWT HS256-signed token (`tokens.py:21,128,167`). The `TokenIssuer` issues and verifies via `jwt.encode`/`jwt.decode`, with standard claims (`iat`, `exp`, `task_id`, `validator_signatures`, `consensus`) and a configurable TTL (`token_ttl_seconds`, default 600s). The old convenience surface (`issue_token`, `verify_token`, `decode_token`) is preserved as module-level functions over a default issuer.
- **Consequences**: Token semantics became standard (JWT expiry, signature verification by PyJWT). The TTL is configurable per `TokenIssuer` instance or globally via `snodo config set engine token_ttl_seconds`. The signing secret is randomly generated per process but can be set via `SNODO_TOKEN_SECRET` for cross-restart persistence. LangGraph checkpoint stores the JWT string.
- **Alternatives considered**: Keep custom HMAC — rejected because audit-log evidence (Task 7.7 commit `959a47a9`) shows the custom system was replaced. Continue without TTL — rejected because session-resume needs bounded token lifetime. Use asymmetric (RSA) signing — rejected as overkill for single-process token issuance in the current architecture.
- **Evidence**: Audit log entry 52 (2025-05-27, Task 7.7), commit `959a47a9`; `tokens.py:21,86-141,148-193`.

---
