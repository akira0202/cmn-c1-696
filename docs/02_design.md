# Template Design Specification — CMN-C1-696 EntraIDGroupAssignAgent

> Source of truth: the approved proposal.

## Agent Overview

Receives a natural-language (NL) instruction to add an existing user to a Microsoft Entra ID (Azure AD) security group (e.g. "Add Suzuki Hanako to Finance-Approvers-JP for her new Finance Manager role"). It parses the NL into user identifier (email/UPN), group name, and business justification; resolves both the user and the group via the Microsoft Graph API under a mandatory `allowed_groups` allowlist; performs the membership assignment; and returns a structured confirmation plus an ISMS A.9.2.2-compliant audit record. Primary users: IT/IAM operators and upstream HR onboarding systems (e.g. WorkdayWorkerQueryAgent). Cat 1 / CMN — a single reusable IAM write capability with governance gates.

## Position in AgentCore Architecture

- **Agent Class**: `EntraIDGroupAssignAgentGraph`
- **L1 Base**: **AgentBaseGraph** (L1 direct)
  - Deterministic fixed pipeline (NL → resolve → gate → assign → confirm). NOT an autonomous think-act loop, so `AutonomousBaseGraph` is not used.
  - `ToolCallingAgent` is a **pattern reference only — NOT inherited**: the framework requires L1-direct inheritance. See Design Decision Record for why a Level 2 base is not used.
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: override `execute()` only)
  - Graph: composition — `register_nodes()` for 5 domain nodes + `add_edges()` override for the linear chain

## Architecture Overview

### Node Configuration

5 domain `FunctionNode`s in a linear sequence between the framework-injected `initialize` / `finalize`.

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema_version, correlation setup | user_input | user_input | InitializeNode (default) |
| input_parse | Parse NL → email/UPN, group_name, business_justification (LLM). S-1 trust (INTERNAL); email/justification validation in `execute()` **after** LLM extraction (see S-2 note below) | user_input | parsed_user_email, parsed_group_name, business_justification | FunctionNode |
| user_resolve | Graph `GET /users/{email}` → object_id + displayName; graceful not-found | parsed_user_email | user_object_id, user_display_name | FunctionNode |
| group_resolve | Graph `GET /groups?$filter=displayName eq` → group_object_id; enforce `allowed_groups` allowlist + block privileged admin groups + ambiguity guard | parsed_group_name | group_object_id | FunctionNode |
| member_add | Graph `POST /groups/{id}/members/$ref`; idempotent already-member handling. **required_trust_level = INTERNAL** | user_object_id, group_object_id | membership_result | FunctionNode |
| confirmation_format | Build structured confirmation + ISMS A.9.2.2 audit record; return both to the caller (`formatted_output` = confirmation + `audit_record`) and emit the audit event. S-3 scrub secrets | membership_result | confirmation, audit_record, formatted_output | FunctionNode |
| finalize | response_metadata, node_history | confirmation | response | FinalizeNode (default) |

### Data Flow

```
START → initialize → input_parse → user_resolve → group_resolve → member_add → confirmation_format → finalize → END
```

> Non-standard linear chain (5 domain nodes) → `add_edges()` is overridden (the Graph Builder Contract permits this for a non-standard node order). `register_nodes()` calls `super().register_nodes()` first (injects initialize + finalize), then registers the 5 domain nodes.
>
> **Slot mapping:** `AgentBaseGraph.compile()` requires the `pre_process` / `main` / `post_process` slots to be non-None, so the three stage-aligned nodes fill those slot keys — `input_parse`=`pre_process` (input handling), `member_add`=`main` (the core write), `confirmation_format`=`post_process` (output formatting) — and the two resolve steps use their own keys (`user_resolve`, `group_resolve`). `add_edges()` wires them in domain order. Audit events and `node_history` use the node **class names**, so the slot keys do not affect observability.

> **Marketplace write-mode scope.** Every domain node requires `INTERNAL`, and the group
> membership write in `member_add` is the operation that level protects. The Marketplace facade
> fixes caller trust at `VERIFIED_EXTERNAL` with no elevation path, so this write is **not
> reachable through Marketplace**: an invocation from there is refused at the S-1 gate of the
> first domain node, before any Graph call. This is the intended outcome, not a gap to work
> around — the trust requirement is not lowered to make the write reachable. Run the agent from
> an entry point that establishes an `INTERNAL` caller (see `docs/07_operation_guide.md`).

> **Response to the caller.** A successful run returns the confirmation (`result`, `user` with
> `email` / `display_name` / `object_id`, `group`, `effective_at`, `business_justification`,
> optional `app_access_summary`) together with `audit_record`, so a calling system can keep the
> provisioning evidence with its own records. The returned copy does not replace the audit event
> `iam.entra.audit_record.emitted`, which is still emitted for every run. A refused or failed run
> returns no confirmation and no audit record.

### State Definition

Flat TypedDict (`State(AgentState)`), primitives / JSON-serializable only.

| Field | Type | Purpose | Set by |
|-------|------|---------|--------|
| user_input | str | Raw NL instruction | caller |
| parsed_user_email | str \| None | Resolved user email/UPN | input_parse |
| parsed_group_name | str \| None | Requested group display name | input_parse |
| business_justification | str \| None | Audit justification (required if `require_justification`) | input_parse |
| user_object_id | str \| None | Entra user object id | user_resolve |
| user_display_name | str \| None | Entra user `displayName`, returned in the confirmation. Informational only: absent, non-string, empty, longer than 256 characters or containing control characters → `None`, and the resolve still succeeds | user_resolve |
| group_object_id | str \| None | Entra group id | group_resolve |
| membership_result | str \| None | `added` / `already_member` | member_add |
| confirmation | dict \| None | Structured confirmation payload | confirmation_format |
| audit_record | dict \| None | ISMS A.9.2.2 audit record: `action`, `result`, `user_object_id`, `group_object_id`, `authorization_basis`, `justification_present`, `effective_at`, `correlation_id`. Identifiers and the authorization basis only — no email, display name or justification text | confirmation_format |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (Graph API `client_secret` NEVER in state — accessed via `ctx.secrets.require()`)
- InvocationContext via `config["configurable"]` only (not in State)
- No Pydantic models, dataclass, arbitrary Python objects

## Config Surface (agent.yaml / config)

| Key | Type | Purpose |
|-----|------|---------|
| `generation_mode` (`agent.yaml`) | `"llm"` | Static generation contract of the deployed agent: `input_parse` extracts the structured fields from the NL instruction with an LLM |
| `entra_tenant_id` / `entra_client_id` | str | Graph API app-registration identifiers — **non-secret config**; passed to the Graph client (not resolved via `ctx.secrets`) |
| `entra_client_secret` | secret ref | Graph API client secret — resolved by the node via `ctx.secrets.require("ENTRA_CLIENT_SECRET")` and passed to the client; never in State |
| `allowed_groups` | list[str] | **MANDATORY allowlist** — only these groups may be assigned; startup fails if unset |
| `require_justification` | bool | Require `business_justification` for the audit trail |
| `group_app_mapping_path` | str \| None | Optional group → downstream-app access summary. **Failure = non-fatal:** if the file is missing or fails to parse, the agent logs a warning, omits the app-access summary from the confirmation, and still completes the assignment (this mapping is cosmetic, not a security control). |
| `appi_audit_logging` | bool | Emit APPI 2026 / ISMS A.9.2.2 audit record |

## Microsoft Graph API Contract

| Operation | Endpoint | Graph scope (min) |
|-----------|----------|-------------------|
| Resolve user | `GET /v1.0/users/{email}` | `User.Read.All` |
| Resolve group | `GET /v1.0/groups?$filter=displayName eq '{name}'` | `Group.Read.All` |
| Add member | `POST /v1.0/groups/{group-id}/members/$ref` | `GroupMember.ReadWrite.All` |

- **Auth**: OAuth2 client-credentials (app registration). Token acquired from secrets; never logged, never in State/output (S-3).
- **API version**: `v1.0` (GA). `members/$ref` is stable GA.
- **Rate limiting**: HTTP 429 / 5xx → exponential backoff via `shared.utils.retry_policy.retry_with_backoff` (the framework provides no ConnectionPolicy).

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, permissions, credential handle) — Graph creds via `ctx.secrets.require("ENTRA_CLIENT_SECRET")` etc.
- [x] Retry/backoff — `shared.utils.retry_policy.retry_with_backoff` for HTTP 429/5xx (the framework has **no ConnectionPolicy**; this is the retry mechanism)
- [x] SecurityViolationError — raised on invalid email / non-allowlisted or privileged group / missing justification
- [x] S-2: `_extra_security_gate_input()` — domain input checks after the default PII scan. **MUST NOT override `_security_gate_input()`.**
  - **InputParseNode exception (H-1):** S-2 email-format + `business_justification` validation runs inside `execute()` **after** the LLM extraction. At `_extra_security_gate_input()` time (pre-`execute()`) the parsed email does not yet exist in State, so that hook is not applicable to this node. Invalid/absent email → `SecurityViolationError` (Error#1); the other resolve nodes still use `_extra_security_gate_input()` normally.
- [x] S-3: `_extra_security_gate_output()` — scrub `entra_client_secret` / Graph tokens from output on confirmation_format. **MUST NOT override `_security_gate_output()`.**
- [x] S-4: `emit_trace_event()` — mandatory domain event on member_add (membership write side effect) and confirmation_format (audit record emit). Do NOT emit node_start/complete/error. Event names + payload keys:
  - **member_add** → event `iam.entra.member_add.executed`; payload keys: `user_object_id`, `group_id`, `add_result` (`added`/`already_member`), `correlation_id`, `ts`
  - **confirmation_format** → event `iam.entra.audit_record.emitted`; payload keys: `group_id`, `authorization_basis` (ISMS A.9.2.2), `justification_present`, `correlation_id`, `ts`
  - **InputParseNode (M-2)** → diagnostic trace events `input_parsed` / `input_parse_rejected` (payload: `correlation_id`, `reason` or `group`, `justification_present`). These are beyond the two mandatory audit-of-record events above, but are still **required by the S-4 audit rule** (every node emits ≥1 domain event regardless of side effect). Diagnostic scope, not audit-of-record.
  - Never include `entra_client_secret`, Graph tokens, or raw user PII in payloads.

> **S-2/S-3 gate behaviour (ADR-017):** all 5 domain nodes are `FunctionNode` subclasses → framework `@final` gate always runs; extend via `_extra_*` hooks only.

### Composition Pattern
- **Pattern**: Standalone (no GraphNode / RemoteAgentNode — single agent, direct Graph API calls)
- **Composition target**: n/a (may be *called by* WorkdayWorkerQueryAgent upstream, but this template is standalone)

### Startup Boundary and Dependency Injection

`src/api/startup.py` is the single place that turns a deployment into a running agent: it loads
`config/config.yaml`, selects the deployment profile, injects the extraction LLM and the
Microsoft Graph client factory, and compiles. `src/api/server.py` calls it and holds no logic.

The three nodes that talk to Microsoft Graph no longer construct a client each: the graph builds
**one** factory and injects it into `user_resolve`, `group_resolve` and `main`. The factory is
called per invocation with the node's State, because the client secret is resolved from the
request-bound SecretProvider — never at construction, never from State, never from the process
environment. A node built with no factory falls back to the live client, which keeps every node
no-arg constructible for PB-6.

The extraction LLM follows the same shape. `config["llm"]` is absent on the deployed path —
`config.yaml` carries runtime parameters only, and an authenticated client cannot exist while the
graph is being built, since the deployment provisions secrets after construction. The graph
therefore falls back to `src/services/llm.AzureOpenAIExtractionLLM`, which resolves
`AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_DEPLOYMENT` from the
request-bound SecretProvider inside `complete(prompt, state)` — the same per-invocation rule the
Graph client factory follows. A test supplies its own object through `config["llm"]`.

**Injection travels through `config`, not through constructor keywords.** The outer graph keeps
the only constructor the framework defines — `__init__(self, config=None)` — and `register_nodes()`
reads `config["llm"]` and `config["graph_client_factory"]`, falling back to the live adapter or
client factory when either is absent. A bespoke `llm=` or similarly-named keyword would be a silent
deviation: AgentRegistry constructs the agent through the framework contract and would never
populate it, leaving the branch permanently unwired.

The graph and its nodes read only `self.config` and what is injected into them. Profile selection
— the one decision that legitimately depends on where the process runs — is owned by the startup
boundary, so business and authorization logic never reads `os.environ`.

### Provisional Stage ⑤ bounded-fake profile (CI only)

The core step of this agent is a privileged Entra ID membership write, so the provisional
Stage ⑤ first invoke would change real directory permissions as a side effect of a pipeline run.
`AGENT_STAGE5_BOUNDED_FAKE=1`, set only on the `deploy-stg` job, makes the startup boundary
inject **both** bounded fakes together — a fixed-JSON extraction LLM and an in-process Graph API
client — plus the synthetic tenant/client identifiers and the single allowlisted synthetic group.
One flag selects both, so a half-faked run is not reachable.

Everything between the two boundaries executes for real: parsing and validation, user and group
resolution, the allowlist and privileged-group guards, the write call itself, the confirmation and
the audit record. The shipped `config/config.yaml` carries no tenant, client id or allowlist, so no
runtime configuration can reach the fakes; unit tests inject them through constructor parameters
and never set the flag.

**Scope of the resulting evidence:** it demonstrates the deploy → invoke → membership-write
workflow contract. It does **not** demonstrate that a write against a live Entra tenant succeeds.
Live-write integration evidence is obtained separately, against an approved non-production tenant
with a non-privileged test group and a rollback/audit procedure — see docs/07.

### Production LLM contract

`input_parse` extracts the structured fields with an LLM, so the deployed agent declares
`generation_mode: "llm"` in `config/agent.yaml`.

- **Default adapter.** On the deployed path nothing supplies `config["llm"]`: `config/config.yaml`
  carries runtime parameters only, and a YAML file cannot carry a client object. `register_nodes()`
  therefore builds `src/services/llm.AzureOpenAIExtractionLLM`, which calls the shared
  `AzureOpenAIClient`. A caller that does supply `config["llm"]` (a test, the bounded-fake profile)
  replaces it.
- **Required secrets.** `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` (the bare resource
  endpoint) and `AZURE_OPENAI_DEPLOYMENT`, declared in `requires.secrets` together with
  `ENTRA_CLIENT_SECRET`. The adapter resolves them from the request-bound SecretProvider inside
  every `complete(prompt, state)` call and builds the client per call; no client is cached on the
  adapter or the node, so one caller's credentials never serve another.
- **Fail-closed when unset.** If a connection value is missing, `input_parse` fails on the secret
  lookup and the run ends with `status: error` and no output — before user resolution, so no Graph
  call and no membership write take place. A registry-served deployment is additionally gated at
  compile time on every key in `requires.secrets`.

The CI suite exercises this contract with the provider call replaced
(`tests/unit/test_default_llm_adapter.py`); extraction quality against a live Azure OpenAI
deployment is not covered by CI or by the provisional Stage ⑤ evidence, which runs the
bounded-fake profile.

## Error & Edge Cases

| # | Case | Node | Behaviour |
|---|------|------|-----------|
| 1 | Invalid email/UPN format | input_parse | Reject — `SecurityViolationError` (S-2) |
| 2 | `business_justification` missing when required | input_parse | Reject with guidance |
| 3 | NL group name ambiguous / unparseable | input_parse | Error return, ask for exact group name (no guess) |
| 4 | User not found | user_resolve | Error return (no assignment); audit the attempt |
| 5 | Group not found | group_resolve | Reject with guidance |
| 6 | Group not in `allowed_groups` | group_resolve | Reject — `SecurityViolationError` |
| 7 | Privileged/admin group requested | group_resolve | Block — not overridable via config |
| 8 | `displayName eq` returns multiple groups | group_resolve | Reject as ambiguous (no guess) |
| 9 | User already a member | member_add | Idempotent — `already_member`, success |
| 10 | Graph API HTTP 429 | any Graph call | Exponential backoff + retry |
| 11 | Graph API 5xx / auth failure | any Graph call | Error return + audit record; no partial write |

## Guardrails & Idempotency

- **Allowlist is fail-safe**: `allowed_groups` MANDATORY; if unset the agent fails at startup (no default-allow).
- **Exact match only**: group resolved by `displayName eq` (no fuzzy/partial) — prevents unintended group match.
- **No-guess on ambiguity**: ambiguous user/group → reject; never auto-select.
- **Idempotent add**: `POST members/$ref` on an existing member returns success (`already_member`), safe to retry.
- **Privilege-escalation guard (built-in, not config)**: well-known privileged/admin roles are denied by a **hard-coded** rule in the agent — NOT a config key. Representative denied set (non-exhaustive; matched by role template): `Global Administrator`, `Privileged Role Administrator`, `Privileged Authentication Administrator`, `Security Administrator`, `User Administrator`, `Application Administrator`, `Conditional Access Administrator`. The deny takes precedence over `allowed_groups` (even if a privileged group is mistakenly listed there, it is still refused), and cannot be disabled via configuration. This guarantees no configuration can ever make the agent grant admin-level access.

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: framework/ and shared/ only (no agents/base/ required)

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | Fixed deterministic 5-node pipeline; no self-directed loop |
| Node count / flow | 5 nodes (approved design) | 6 nodes (alternative reading) | **5 nodes** | The approved design defines 5; the alternative's GroupAllowlistCheck is folded into group_resolve and AuditLogGenerate into confirmation_format |
| Inheritance | L1-direct AgentBaseGraph | L2 `class(ToolCallingAgent)` | **L1-direct** | The framework forbids an L2 base; ToolCallingAgent = pattern ref only |
| Class name | `EntraIDGroupAssignAgent` (released convention) | `EntraIDGroupAssignAgentGraph` (as proposed) | **`EntraIDGroupAssignAgentGraph`** | Faithful to the approved proposal (no unilateral rename); class name is not gate-checked |
| Group name match | exact (`displayName eq`) | fuzzy/partial | **exact only** | Security-first — prevents unintended group match |
| Composition | Standalone | GraphNode subgraph | **Standalone** | Single capability, 2–3 Graph API calls, no subgraph needed |
| HITL on privileged grant | none | `hitl.enabled` on high-privilege groups | **none (v1)** | Privileged groups are hard-blocked, not approved; allowlist covers the risk. Revisit if a customer needs approve-not-block. |

> **✅ Resolved — L1-direct / 5 nodes is authoritative:** the framework's L1-direct inheritance rule takes precedence over any L2 wording, and the approved design defines the authoritative node count (5). A "Level 2 base" reading (a `ToolCallingAgent` base with a 6-node flow) is treated as **advisory only** — `ToolCallingAgent` is a pattern reference, not an inheritance target. No open question remains for implementation.
