# Contributing to Open Executive

## Getting Started

1. Fork the repo and clone your fork
2. Set up the development environment: `make install`
3. Copy `.env.example` to `.env` and add your `ANTHROPIC_API_KEY`
4. Start the dev server: `make dev`
5. Run the tests: `make test`

## Branch Naming

- `feat/` — new features
- `fix/` — bug fixes
- `agent/` — new specialist agents
- `eval/` — new eval scenarios
- `docs/` — documentation changes

## PR Requirements

All PRs must:
1. Pass CI (ruff, mypy, unit tests)
2. Include working code — no stubs, no placeholders
3. Include tests for new behavior
4. For new or modified agents: include at least 2 eval scenarios
5. **Architecture docs**: verify `/architecture` reflects your change (see below)

## Adding a New Specialist Agent

See [CLAUDE.md](../CLAUDE.md#adding-a-new-specialist-agent) for the step-by-step guide.

## Improving the Knowledge Base

The `knowledge/` directory contains Markdown files with executive expertise. Contributions here are very welcome.

Requirements:
- Accurate and up-to-date information
- Cite sources for specific claims
- Domain-tagged with the correct folder
- Practical, not academic — this is for practitioners

## Architecture Docs (`/architecture` page)

The `/architecture` page in the UI documents the system for the team. Parts of it are live (auto-pulled from the API) and parts are static diagrams.

**You do NOT need to update anything if you:**
- Add a new specialist agent (the Agent Council section pulls from `GET /agents` automatically)
- Add or change an API endpoint (the API Reference pulls from `GET /openapi.json` automatically)
- Add or change a workflow (the Workflows section pulls from `GET /workflows` automatically)

**You DO need to update the static diagrams in `packages/ui/src/app/architecture/page.tsx` if you:**
- Change the overall request flow (how requests move from client → API → Executive → specialists → response)
- Change the prompt caching strategy (how system blocks are built or what's in the user turn)
- Add a new integration layer (new inbound channel, new external service)
- Change the memory system architecture (new storage backend, new extraction pipeline)
- Change how the knowledge/RAG pipeline works at a structural level

These structural changes are rare and significant — they warrant a diagram update in the same PR.

## Prompt Changes

Prompt changes to `executive_persona.py` or `domain_prompts.py` require:
1. A before/after comparison in the PR description
2. Eval suite run showing no regression (score drop ≤10% on existing scenarios)
3. At least 2 new eval scenarios if adding new behavior

## Reporting Issues

Use GitHub Issues. Include:
- What you asked the Executive
- What you expected
- What you got
- Your company profile context (anonymized)
