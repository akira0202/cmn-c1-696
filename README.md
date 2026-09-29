# CMN-C1-696 — EntraIDGroupAssignAgent

> **Category**: Cat 1 (single technical capability — a reusable Entra ID group-assignment capability)
> **Industry**: CMN

## Overview

This agent adds an existing user to a Microsoft Entra ID (Azure AD) security group from a
plain-language instruction — for example "add Hanako Suzuki to Finance-Approvers-JP for her new
Finance Manager role". It pulls the user, the group, and the stated business reason out of the
sentence, looks both up through the Microsoft Graph API, and makes the assignment; repeating the
same request does not add the user twice. It returns a structured confirmation (the result, the
user and group, when it took effect, and the stated business reason) together with an audit
record that carries the user and group identifiers, the authorization basis, whether a business
reason was given, and a timestamp. It is meant for IT and identity operators,
and for onboarding systems that call it automatically.

Which groups it may touch is restricted to an allowlist you configure, and privileged or
administrative groups are refused outright. It only adds a user to a group — it does not create or
delete accounts, remove memberships, grant roles or permissions directly, or change the group
itself.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent raises
`PlatformRequired` during graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and test specifications
```

See `docs/` for the design specification and the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.

