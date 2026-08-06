# ADR 0005: Machine Errors and Exit Codes

**Status:** accepted
**Date:** 2026-08-05
**Decision owner:** Daeron

## Decision

The public failure surface is a closed machine projection, not a bag of caller
text.

`ERROR_CODE_REGISTRY` is the sole owner of each supported error code's:

- category;
- public exit class;
- fixed detail;
- retryability;
- fixed remediation steps.

`ERROR_EVIDENCE_REGISTRY` is the sole owner of each supported evidence kind's
fixed public summary. Construction and decoding both verify those relationships
exactly. A caller cannot supply alternate prose, retry behavior, remediation,
category, exit class, or evidence summary while retaining the same code/kind.
Unknown fields and unregistered values are rejected rather than ignored.

The public `ErrorEnvelope` contains:

- `schema_version` and `protocol_version`;
- `ok: false`;
- request and correlation IDs;
- an operation ID when one exists;
- VM ID, generation, and boot ID when the failure is bound to that identity;
- a stable error `code`, registry-bound `category`, and registry-bound
  `exit_code`;
- registry-derived `detail`, `retryable`, and `remediation`;
- at most 16 closed-kind evidence entries;
- `details`, which is required to be an empty object for every P1 error code.

The rigid evidence reference is exactly `diag:<non-nil UUID>`. It is an opaque
identifier for a future private diagnostic record—not a filesystem path, URL,
secret, bearer capability, proof that a diagnostic exists, or proof that the
holder may read it. A future diagnostic-store owner must provide lookup,
authorization, retention, and redaction. Until that owner exists, the reference
remains intentionally unresolved.

A raw traceback, secret, authorization header, request payload, success result,
command body, filesystem path, URL, log body, or unbounded subprocess output
never enters the public error envelope.

## Generic machine data is a different boundary

`ControlRequest.payload` and `ControlResponse.result` are authenticated,
bounded, immutable, canonical machine-keyed data. Structural bounds prevent
wire abuse and ambiguous keys; they do **not** establish that arbitrary values
are public, redacted, semantically valid for an operation, or safe to log.

The concrete operation schema owns semantic validation and any public/logging
projection. Transport and logging layers must never dump either generic bag
wholesale. These bags are not a workaround for the closed error contract, and
they must not be copied into `ErrorEnvelope.details`.

## Closed registry

P1 defines these stable error codes:

| Error code | Category | Exit class |
| --- | --- | --- |
| `INVALID_REQUEST` | validation | `2` invalid input |
| `VALIDATION_ERROR` | validation | `2` invalid input |
| `PROTOCOL_ERROR` | protocol | `2` invalid input |
| `AGENT_UNAVAILABLE` | unavailable | `3` unavailable |
| `UNAVAILABLE` | unavailable | `3` unavailable |
| `POLICY_REJECTED` | policy | `4` policy rejection |
| `OWNERSHIP_REJECTED` | ownership | `4` policy rejection |
| `CONFLICT` | conflict | `4` policy rejection |
| `PRECONDITION_FAILED` | precondition | `4` policy rejection |
| `EXECUTION_FAILED` | execution | `1` domain failure |
| `RECOVERY_REQUIRED` | recovery | `5` recovery required |
| `INTERNAL_ERROR` | internal | `1` domain failure |

Adding or changing a code is a protocol change: update the registry, exact
decoder/constructor behavior, direct negative proof, this ADR, and every public
adapter together. An adapter cannot create a local synonym or free-form detail.

## Public CLI exit codes

| Code | Meaning |
| --- | --- |
| `0` | observed success |
| `1` | requested domain operation or health gate failed |
| `2` | invalid CLI usage, request, protocol message, or configuration |
| `3` | required daemon, transport, fixture, agent, or environment unavailable |
| `4` | policy, ownership, conflict, or precondition rejection |
| `5` | accepted operation failed and entered inspectable recovery state |

The enum in the shared protocol classifies a future CLI adapter's response to a
machine error. Merely constructing or decoding an envelope does not establish a
process exit status or observed operation outcome. A public adapter must still
return the matching code at its actual process boundary and preserve the
underlying diagnostic reference for authorized inspection.

The direct proof harness retains its narrower contract: `0` means all checks
passed, `1` means at least one check failed, and `2` means the harness itself
could not execute. Independent checks continue after a failure, but the final
status remains failed.

## Direct evidence

- `src/somnus_protocol/control.py` owns both closed registries,
  `DiagnosticReference`, `ErrorEvidence`, and `ErrorEnvelope`.
- `test/vm_lab/test_protocol_control.py` proves every public phrase is
  registry-derived; rejects category/exit mismatch, arbitrary prose, nonempty
  details, paths/URLs in diagnostic references, unknown fields/codes/kinds, and
  oversized evidence; and distinguishes generic authenticated machine data from
  public/log-safe output.
- No P1 public lifecycle/daemon adapter exists, so the exit mapping is a shared
  contract for that future consumed boundary rather than a claim that those
  commands currently run.
