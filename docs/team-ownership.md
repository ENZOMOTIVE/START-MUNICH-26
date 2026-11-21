# Team ownership

Split is five GitHub issues. Do not expand into follow-ups listed at the bottom.

| Task | Issue | Owns | Does not own |
| ---- | ----- | ---- | ------------ |
| [T1](tasks/T1.md) | #21 | `backend/app/{core,contracts,db,platform,workers}` | pricing math, workflows, frontend |
| [T2](tasks/T2.md) | #22 | `backend/app/pricing`, `fixtures/rates`, read `starter-kit/` | mailbox, booking, dashboard |
| [T3](tasks/T3.md) | #23 | `backend/app/workflows` | tariff files, CRM UI |
| [T4](tasks/T4.md) | #24 | CRM sessions + `frontend/` | mailbox poll, rate calc |
| [T5](tasks/T5.md) | #25 | `fixtures/`, tests, CI, compose, Makefile, runbook, demo | live adapters |

## Shared files (coordinate before editing)

- `backend/app/contracts/models.py`
- `backend/app/contracts/adapters.py`
- `backend/app/db/models.py`
- `backend/app/core/config.py`
- `.env.example`
- `compose.yaml`
- `Makefile`

## Follow-ups (not this split)

- Complete tariff parsing
- Live carrier negotiation
- Full gateway accounting
- Adaptive-wait
