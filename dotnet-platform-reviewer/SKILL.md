---
name: dotnet-platform-reviewer
description: "C# .NET 10 code review enforcing DDD, event sourcing, CQRS, hexagonal architecture, modern C# features, and TDD compliance. Use when reviewing C# code, pull requests, or any .NET backend design."
version: 1.0.0
---

# .NET Platform Code Review

Expert code review for C# .NET 10 services following DDD, event sourcing, CQRS, and hexagonal architecture.

---

## Review Dimensions

### 1. Layer Dependency Rules

Dependencies MUST point inward: Presentation → Application → Domain. Never outward.

- **Domain layer**: No infrastructure, no application references. Business logic only.
- **Application layer**: Depends on Domain only. Command/query handlers and application services live here.
- **Adapters layer**: Cloud SDKs, repository implementations, external clients. NOT anywhere else.
- **Presentation/API layer**: Delegates to application handlers. No business logic, no direct domain access.

Flag any import of a cloud SDK (Azure, AWS, etc.) outside the Adapters layer as a **critical violation**.

### 2. DDD and Aggregate Design

- Aggregates inherit from `AggregateRoot`. No public setters. No direct state mutations.
- State changes happen exclusively via domain events applied through `When()` methods.
- Aggregate IDs are dedicated value objects (e.g. `TravelRuleMessageId`), distinct from Correlation IDs.
- Repositories return aggregates, not DTOs.
- Business logic lives in the domain layer, not in handlers or controllers.

Flag any direct property assignment to aggregate state as a **critical violation**.

### 3. Event Sourcing

- Every state change MUST be a domain event. No mutations without events.
- All domain events MUST include: `EventVersion` and `Sequence Number`.
- When a new event is added to an aggregate's `Apply()`, the corresponding rebuilder MUST be updated.
  A rebuilder that silently skips an unknown event type will cause state loss — catch this.
- The event store is the single source of truth. Never derive state from anywhere else.

### 4. CQRS

- **Commands**: Use `IAggregateRepository` to load and save aggregates. Never use `IEventStore` directly unless justified.
- **Queries**: Use `IAggregateRepository` to load aggregates. No state mutation. No business logic.
- Controllers delegate to command or query handlers. Zero business logic in controllers.
- Background services delegate to command handlers. Same rules apply.

Flag any business logic in a controller or query handler as a violation.

### 5. Async 202 Pattern

Write operations involving external systems or expected to exceed 2s MUST:
1. Return `202 Accepted` immediately after enqueuing.
2. Store pending operation on the aggregate via a domain event (event-sourcing compliant).
3. Process asynchronously via a background worker.
4. Both enqueue and process handlers MUST be idempotent.

Flag synchronous external calls in request handlers as a violation.

### 6. Security

- All PII MUST be encrypted at rest (AES-256 or equivalent).
- No cloud SDK calls in domain or application layers.
- No hardcoded secrets or connection strings anywhere.
- API endpoints MUST be authenticated and authorized.
- PII access MUST be logged (timestamp, service, operation, correlation ID).

Flag any hardcoded secret or unencrypted PII storage as a **critical violation**.

### 7. Modern C# Features (.NET 10 / C# 14)

Enforce these patterns:

**Primary Constructors (C# 12)** — use parameters directly, no redundant backing fields.
Exception: backing field OK when transforming the value (e.g. `options.Value`).

**Collection Expressions (C# 12)** — use `[]` not `new List<T> { }` or `new[] { }`.

**File-Scoped Namespaces (C# 10)** — always `namespace Foo.Bar;` not wrapped block.

**Records for DTOs** — use `record` for immutable data objects. Use `required` + `init` for mutable records.

**Required Members (C# 11)** — use `required` modifier for mandatory properties.

**Pattern Matching** — use property patterns and switch expressions over long if-chains.

**Target-Typed New** — use `new()` when type is clear from context.

**`field` keyword (C# 14)** — use for property validation without explicit backing fields.

**Global Usings** — do not re-import `System`, `System.Linq`, `System.Threading.Tasks`, `Microsoft.Extensions.Logging`.

### 8. TDD Compliance

- Tests MUST exist at the application/command handler level, not isolated unit tests of domain objects.
- Mock ONLY external boundaries: `IAggregateRootRepository`, `IEventStore`, HTTP clients, message brokers.
- Do NOT mock domain logic or protocol handlers.
- Tests MUST verify domain events raised (inspect uncommitted events, event sequence, content, state transitions).
- Test placement: highest appropriate level first (component > integration > unit).

Flag tests that mock domain logic as a violation of hexagonal testing principles.

### 9. Observability

- All logs MUST be structured (via OTEL): ISO 8601 timestamp, correlation ID, service name, log level.
- Metrics: request count, latency (p50/p95/p99), queue depth, success rates, retry count.
- Correlation ID MUST propagate through all distributed traces.

### 10. API Contract

- Error responses for 400-level: RFC 7807 Problem Details (`type`, `title`, `status`, `detail`, optional `invalid_fields`).
- URL paths use Aggregate IDs. Responses expose both Aggregate ID and Correlation ID.
- All endpoints versioned. No unannounced breaking changes.

---

## Severity Classification

| Severity | Examples |
|----------|----------|
| **CRITICAL** | Cloud SDK outside Adapters, hardcoded secrets, direct state mutation on aggregate, missing encryption for PII |
| **HIGH** | Business logic in controller/handler, CQRS violation, missing domain event for state change, rebuilder not updated |
| **MEDIUM** | Old-style C# patterns replaceable by modern equivalents, missing required members, redundant backing fields |
| **LOW** | Style inconsistencies, missing structured log fields, minor naming issues |

---

## Review Output Format

For each file reviewed:

```
📋 REVIEW: <filename>

CRITICAL:
1. [RULE] file.cs:42
   Issue: <specific violation>
   Fix: <concrete action>

HIGH:
1. [RULE] file.cs:89
   Issue: <specific violation>
   Fix: <concrete action>

MEDIUM / LOW:
... (grouped, concise)

VERDICT: ✅ PASS | ⚠️ PASS WITH CONCERNS | ❌ FAIL
```

If no violations: `✅ PASS — no violations found.`

---

## Mandatory Review Checklist

Before marking a review complete:

1. [ ] Verify no cloud SDK imports exist outside the Adapters layer
2. [ ] Verify all state changes route through domain events (no direct mutation)
3. [ ] Verify all domain events include EventVersion and Sequence Number
4. [ ] Verify rebuilder is updated when new events are added to Apply()
5. [ ] Verify command handlers use IAggregateRepository, not IEventStore directly
6. [ ] Verify query handlers contain no business logic and no state mutation
7. [ ] Verify controllers contain no business logic and delegate to handlers only
8. [ ] Verify external calls or long operations use the 202 async pattern
9. [ ] Verify PII is encrypted at rest
10. [ ] Verify no hardcoded secrets or connection strings
11. [ ] Verify modern C# features used where applicable (primary constructors, records, collection expressions, file-scoped namespaces)
12. [ ] Verify tests mock only external boundaries, not domain logic
13. [ ] Verify tests verify domain events raised on aggregates
14. [ ] Verify RFC 7807 Problem Details used for 400 errors
15. [ ] Verify correlation ID propagated through logs and traces

Do not mark review complete until all 15 checks are explicitly confirmed.
