# Test Specification — CMN-C1-696 EntraIDGroupAssignAgent

## Test Strategy
- Coverage target: a managed review-process item (line coverage on `src/nodes/` + `src/services/`; the threshold is set by that process, not fixed here).
- Test types:
  - **Unit** — per-node business logic (`tests/unit/`), Graph API + LLM mocked.
  - **Proof-of-Boundary (PB)** — framework boundaries (`tests/proof_of_boundary/`).
  - **Framework Compliance (TC)** — a mix of unit tests + CI gates.
  - **Integration / E2E** — **out of scope for v1.** No gate or review criterion requires it; `tests/integration/` is kept as an empty package (`__init__.py` only) — it is listed in the central CI `REQUIRED_STG_COMPLETE` but not in `REQUIRED_STG_DIRS`. Full-pipeline invoke ordering is covered structurally by PB-6.
- Mocking: `GraphApiClient` is replaced with a fake in unit tests (no live Microsoft Graph calls); the LLM in `InputParseNode` is a fake returning canned JSON.

## Framework Compliance Tests (Mandatory)

> TC definitions below are the mandated compliance items (unchanged). The **Result** column records how each is covered.

| TC-ID | Test | Expected Result | Result (coverage) |
|-------|------|----------------|-------------------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass | `tests/proof_of_boundary/test_state_safety.py` (PB-2) — Pass |
| TC-02 | SecurityViolationError fires on invalid input | Error raised | `test_input_parse_node.py` (invalid email), `test_group_resolve_node.py` (non-allowlisted / privileged) — Pass |
| TC-03 | No JWT/Credential in State | CI `gate-credential-scan`: 0 violations (S-5 enforcement is done in CI) | CI gate (already green) |
| TC-04 | InvocationContext via configurable only | Direct access raises error | See **Note TC-04** — reconstructed via `from_state`; no ctx object in State (PB-2) — Pass |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start` / `node_complete` / `node_error` absent from `execute()` body | `test_no_duplicate_lifecycle_events.py` (source assert over `src/nodes/`) — Pass |
| TC-06 | S-2: `_security_gate_input()` not overridden (`FunctionNode` subclass) | `TypeError` raised at class definition if overridden (`@final` enforced by framework) | framework-enforced; asserted in `test_gate_finality.py` — Pass |
| TC-07 | S-3: `_security_gate_output()` not overridden (`FunctionNode` subclass) | `TypeError` raised at class definition if overridden (`@final` enforced by framework) | framework-enforced; `ConfirmationFormatNode` extends `_extra_security_gate_output` only — `test_gate_finality.py` — Pass |
| TC-08 | `required_trust_level` enforced | Insufficient trust → refused before `execute()` | `tests/proof_of_boundary/test_s1_trust_gate.py` — every domain node called through `node(state)` as `ANONYMOUS` and `VERIFIED_EXTERNAL` never reaches `execute()` (spy asserted not called; `INTERNAL` control case does) + CI `gate-trust-level-check` — Pass |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial when domain checks needed | Domain-specific input checks execute correctly (e.g. PII scan on additional fields, consent validation, business rules) | See **Note TC-09** — S-2 validation done in `execute()` (docs/02 H-1) — Pass |
| TC-10 | S-3: `_extra_security_gate_output()` non-trivial when domain checks needed | Domain-specific output checks execute correctly (e.g. nested credential scan, PII re-check, content filtering, preservation verification) | `ConfirmationFormatNode._extra_security_gate_output` scrubs secret-keyed fields — `test_confirmation_format_node.py` — Pass |
| TC-11 | S-4: at least one domain `emit_trace_event()` inside each `execute()` | Domain event emitted on every invocation path | per-node unit tests assert `emit_trace_event` called on each branch — Pass |

> **Note TC-04:** In this framework version, `BaseGraph.invoke()` does **not** place an `InvocationContext` in `config["configurable"]` (it carries `thread_id` only). Nodes reconstruct ctx via `InvocationContext.from_state(state)` and read secrets from the ContextVar (`ctx.secrets.require(...)`). The compliance intent — **no `InvocationContext` object persisted in State** — holds and is verified by TC-01 / PB-2. Our nodes never store ctx in State.
>
> **Note TC-09:** `InputParseNode` performs its S-2 domain validation (email format, justification presence) inside `execute()` **after** the LLM extraction, because at `_extra_security_gate_input()` time (pre-`execute()`) the parsed email does not yet exist in State (docs/02 H-1). The `_extra_security_gate_input()` hook is therefore left as the default no-op for these nodes; domain input validation is still present and tested (BL-02/03/04). TC-09's "non-trivial hook" requirement applies only where a node needs *pre-execute* input checks.

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result (coverage) |
|-------|----------|------|----------------|-------------------|
| PB-1 | BaseNode → EventEmitter | `emit_trace_event()` fires on every invocation path | No silent failures | per-node unit tests — Pass |
| PB-2 | State serialization | Post-invoke State is primitives only | No Pydantic/dataclass | `test_state_safety.py` |
| PB-3 | Level 2 → External service | Real external service connection | Data retrieved | `test_user_resolve_node.py` / `test_group_resolve_node.py` (mock client); **live Graph connection = integration, out of scope v1** — Pass |
| PB-4 | Import isolation | No Level 0 imports | AST scan: 0 violations | `test_import_isolation.py` |
| PB-5 | Checkpoint safety | No JWT/Pydantic in checkpoint | Inspection pass | `test_state_safety.py` |
| PB-6 | Invoke execution order | `__call__()`: S-1 trust gate → S-4 `node_start` → S-2 `_security_gate_input` → `execute()` → S-3 `_security_gate_output` → S-4 `node_complete` | Order verified | `test_pb_invoke_order.py` |

> PB-7 (HITL interrupt propagation) is **N/A** — this template does not set `hitl.enabled`.

## Security Boundary Tests (S-1 / S-3)

| ID | Layer | Test | Expected Result |
|----|-------|------|----------------|
| SEC-S1-a | S-1 | Group not in `allowed_groups` → `GroupResolveNode` refuses | `SecurityViolationError`; no Graph call |
| SEC-S1-b | S-1 | Privileged/admin group (e.g. "Global Administrator") blocked even if present in `allowed_groups` | `SecurityViolationError`; hard-coded deny precedes allowlist |
| SEC-S1-c | S-1 | Compiled graph invoked by a caller below `INTERNAL` (`ANONYMOUS`, `VERIFIED_EXTERNAL`) — the level a Marketplace invocation carries | `status: error`; no LLM call, no Graph call, no membership write (`test_s1_trust_gate.py`) |
| SEC-S1-d | S-1 | Standalone entry-point caller credentials (`src/api/server.py`) | `INVOKE_AUTH_TOKEN` → `VERIFIED_EXTERNAL` only; `INTERNAL` only with `STG_INTERNAL_RUNNER_TOKEN`, and only in a process started with the bounded-fake profile — otherwise the runner token is ignored (no write; 401 when the invoke token is configured), fixed at startup rather than re-read per request; wrong token → 401; middleware-set trust unchanged (`test_s1_trust_gate.py`) |
| SEC-S3-a | S-3 | `ConfirmationFormatNode` output contains no `client_secret` / Graph tokens | secret-keyed fields absent/redacted in `confirmation` & `audit_record` |
| SEC-S3-b | S-3 | Graph token never written to State | State inspection: no token fields |

## Business Logic Tests

Mapped to docs/02 §Error & Edge Cases (Error #1 – Error #11). Graph API and LLM are mocked.

| BL-ID | Case | Node | Input | Expected Result |
|-------|------|------|-------|----------------|
| BL-01 | Happy path | full chain | valid NL: user email + allowlisted group + justification; user & group resolve; not yet a member | `parsed_*` → `user_object_id` → `group_object_id` → `membership_result="added"` → `confirmation` + `audit_record`; `status=SUCCESS` |
| BL-02 | Invalid/absent email (Error#1) | input_parse | LLM returns email=null / malformed | `raise SecurityViolationError` (S-2) |
| BL-03 | Missing justification when required (Error#2) | input_parse | `require_justification=true`, justification=null | `status=ERROR`, guidance in `error_log` |
| BL-04 | Ambiguous/unparseable group (Error#3) | input_parse | LLM returns group_name=null | `status=ERROR`, ask for exact group name (no guess) |
| BL-05 | User not found (Error#4) | user_resolve | Graph `GET /users/{email}` → 404 → None | `status=ERROR`; attempt audited; no assignment |
| BL-06 | Group not found (Error#5) | group_resolve | Graph filter returns `[]` | `status=ERROR` with guidance |
| BL-07 | Group not in allowlist (Error#6) | group_resolve | parsed group ∉ `allowed_groups` | `raise SecurityViolationError` (before any Graph call) |
| BL-08 | Privileged group (Error#7) | group_resolve | parsed group ∈ privileged set (any casing) | `raise SecurityViolationError`; blocked even if allowlisted |
| BL-09 | Ambiguous — multiple matches (Error#8) | group_resolve | Graph filter returns >1 group | `status=ERROR` (ambiguous, no auto-select) |
| BL-10 | Already a member (Error#9) | member_add | `POST members/$ref` → HTTP 400 "…already exist…" | `membership_result="already_member"`, `status=SUCCESS` (idempotent) |
| BL-11 | Graph 429 (Error#10) | graph_api_client | `_send` receives 429 then 200 | retried via `retry_with_backoff`; eventual success |
| BL-12 | Graph 5xx / auth failure (Error#11) | member_add / resolve | Graph raises after retries | `status=ERROR` + audited; no partial write |
| BL-13 | Missing resolved ids | member_add | `user_object_id` / `group_object_id` absent | `status=ERROR` (no Graph call) |
| BL-14 | S-3 output sanitization | confirmation_format | a secret-keyed field injected into result | field redacted; no `client_secret`/token in `confirmation`/`audit_record` |
| BL-15 | User display name | user_resolve → confirmation_format | Graph user object with / without a usable `displayName` | `confirmation.user.display_name` carries the trimmed name; absent, non-string, empty, over 256 characters or control characters → `None` with `status=SUCCESS`; not written on a failed resolve |
| BL-16 | Audit record returned to the caller | full chain (`invoke()`) | successful run / refused run / failed write | success: `output` carries the confirmation and `audit_record` (identifiers and authorization basis only — no email, display name or justification text), and the audit event is still emitted; refused or failed: `output` is `None` |
| BL-17 | Production extraction LLM | input_parse (default adapter) | no `config["llm"]`; Azure OpenAI connection values present / one missing | the graph uses `AzureOpenAIExtractionLLM`; values resolved and a client built on every call; a missing value → `status=ERROR`, no output, no Graph call, no write |

## Unit Test Files (`tests/unit/`)

| File | Node / module under test | BL / TC covered |
|------|--------------------------|-----------------|
| `test_input_parse_node.py` | InputParseNode | BL-02/03/04, TC-02/09/11 |
| `test_user_resolve_node.py` | UserResolveNode | BL-01(part)/05/12/13/15, PB-3 |
| `test_group_resolve_node.py` | GroupResolveNode | BL-06/07/08/09, SEC-S1-a/b, TC-02 |
| `test_member_add_node.py` | MemberAddNode | BL-10/12/13, TC-11 |
| `test_confirmation_format_node.py` | ConfirmationFormatNode | BL-14/15/16, SEC-S3-a, TC-10 |
| `test_invoke_output_contract.py` | caller-facing output of a compiled `invoke()` | BL-15/16 |
| `test_default_llm_adapter.py` | production extraction LLM (`src/services/llm.py`, default in `register_nodes()`) | BL-17 |
| `test_graph_api_client.py` | GraphApiClient (`src/services/`) | BL-10/11, OData/URL escaping |
| `test_gate_finality.py` | all `src/nodes/` classes | TC-06/07 (`@final` gates not overridden) |
| `test_no_duplicate_lifecycle_events.py` | all `src/nodes/` classes | TC-05 |
| `test_stage5_bounded_fake.py` | provisional Stage ⑤ bounded-fake profile (`src/api/startup.py`, `src/services/stage5_fakes.py`) | see below |

### Provisional Stage ⑤ bounded-fake profile (`test_stage5_bounded_fake.py`)

The provisional Stage ⑤ first invoke runs against bounded fakes rather than a live Entra tenant
(docs/02 *Provisional Stage ⑤ bounded-fake profile*, docs/07). These tests pin what that profile
must prove and what it must never do. The fakes are injected through constructor parameters; the
CI environment flag is exercised only where profile selection itself is asserted.

| Assertion | Why it matters |
|---|---|
| `add_member()` is called exactly once, with the resolved synthetic user and group object ids | the fake must not merely report success — the node has to reach the write with the ids the graph resolved |
| no write is attempted when resolution is incomplete | a missing id must fail before the write, not after |
| `added` and `already_member` both produce the audit record and a confirmation | `already_member` is the idempotency contract, not a way to avoid a side effect |
| a raising fake yields `status: error` with no `membership_result` and no executed-event | a failure must never be disguised as a successful write |
| the profile is off without the flag, and off for any value other than `1` | the fakes are unreachable from a normal deployment |
| a build with no flag and no allowlist raises `ConfigError` | the shipped config cannot silently fall back to a fake |
| the synthetic address uses the reserved `.invalid` TLD and the ids are labelled `stage5-fake-*` | CI values cannot be mistaken for production identifiers |
| the audit payload carries exactly the documented keys | no secret or raw credential reaches the trace |

> The template-baseline sample node tests and the superseded sample nodes have been removed.
> `tests/unit/` is a `REQUIRED_STG_DIRS` package, so at least one real unit test must exist
> there — the per-node suites above satisfy that.

## Additional Regression Coverage

Guards added alongside the changes in this revision. Each entry names the test and the
condition it accepts on; they supplement the mandatory TC / PB items above and replace none
of them.

| Concern | Test | Acceptance condition |
|---|---|---|
| Deployment startup contract | `tests/unit/test_entry_point_import.py` | `src/api/server.py` — the module `uvicorn src.api.server:app` loads — imports successfully under the CI profile and exposes `app` and a compiled `agent` with a non-empty runtime config. With the shipped config alone it refuses to start rather than running with an empty group allowlist. |
| Error → terminal routing | `tests/unit/test_inner_error_short_circuit.py` | Every non-terminal domain hop declares a conditional branch; an errored State selects the terminal branch (`finalize`, matching where `AgentBaseGraph.route()` sends a terminal status), a healthy State advances to the next node, and no unconditional edge remains between two domain nodes. A failed resolve therefore never reaches the membership-write node, and a failure does not amplify the `error_log` reducer channel. |


## Test Execution Summary
- Execution date: _(filled when the suite is run for the record)_
- Total tests: _TBD_
- Pass: _/_ Fail: _/_ Skip: _
- Coverage: _TBD_ (target managed as a review-process item)
