# Project Rules

## Goal

Build a reproducible enterprise inventory root-cause Agent using synthetic data only.

## Architecture

- `app/domain`: business entities and rules; no framework imports except Pydantic.
- `app/repositories`: persistence interfaces and implementations.
- `app/services`: deterministic application use cases.
- `app/tools`: typed Agent tools.
- `app/agent`: workflow and LLM integration.
- `app/api`: FastAPI transport layer.
- `tests`: unit, integration, and end-to-end tests.

## Constraints

- Never copy company source code, real table names, customer data, credentials, or internal addresses.
- All sample data must be generated and visibly marked synthetic.
- Business calculations and root-cause scoring must remain deterministic and independently testable.
- LLM output must not overwrite computed values or evidence.
- Distinguish `empty` from `error` in every external contract.
- Never expose chain-of-thought; expose only tool/action summaries and evidence.
- Every resume metric must be reproducible by a test or evaluation command.

## Quality Gate

- Python 3.11+.
- Type hints on public functions.
- Pydantic models reject unknown fields.
- Tests cover normal, boundary, empty, and invalid cases.
- Secrets belong in `.env`; only `.env.example` is committed.
