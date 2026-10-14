# ADR-004: Provider and Worker Abstraction

Status: Accepted, 2026-10-06.

Context: Models, account systems and harnesses are replaceable execution resources.
Decision: ConversationProvider and AgentWorker are separate typed ports; only synthetic mocks are implemented.
Consequence: no real credentials, routing or model calls; API key, OAuth and harness-session references remain extensible without browser cookies.

