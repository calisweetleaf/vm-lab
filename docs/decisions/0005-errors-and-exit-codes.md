# ADR 0005: Machine Errors and Exit Codes

**Status:** accepted  
**Date:** 2026-08-05  
**Decision owner:** Daeron

## Machine envelope

Every future machine-readable failure contains:

- `schema_version`;
- `ok: false`;
- request ID and, when assigned, operation ID;
- VM ID and generation when the request resolved an exact VM;
- a stable error `code` and `category`;
- bounded human `detail`;
- `retryable`;
- bounded `remediation` steps;
- redacted, bounded `evidence`;
- structured bounded `details`.

Unknown fields are rejected where they could hide operator intent. A raw
traceback, secret, bearer header, payload body, or unbounded subprocess output
never reaches the public envelope.

## Public CLI exit codes

| Code | Meaning |
| --- | --- |
| `0` | observed success |
| `1` | requested domain operation or health gate failed |
| `2` | invalid CLI usage, request, or configuration |
| `3` | required daemon, transport, fixture, or environment unavailable |
| `4` | policy, ownership, conflict, or precondition rejection |
| `5` | accepted operation failed and entered inspectable recovery state |

The direct proof harness retains its narrower contract: `0` means all checks
passed, `1` means at least one check failed, and `2` means the harness itself
could not execute. Independent checks continue after a failure, but the final
status remains failed.
