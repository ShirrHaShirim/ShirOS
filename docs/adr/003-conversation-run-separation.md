# ADR-003: Conversation and Run Separation

Status: Accepted, 2026-10-06.

Context: Human dialogue is history; autonomous maintenance must run with bounded context.
Decision: independent Conversation/Message and Job/Run/Checkpoint ownership and identities.
Consequence: a Run cannot silently append to a human conversation or become long-term memory. Provider/model changes require a new conversation.

