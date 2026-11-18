# Phase 4 dashboard validation

The seeded Compose application served the production bundle through nginx. Edge exercised login, alert selection, asynchronous creation, report generation, logout, reviewer login, investigation selection, approval and checkpoint resumption.

| Check | Result |
|---|---|
| Frontend directory, `npm run build` | TypeScript passed, Vite built in 1.44 s |
| `docker compose run --rm backend uv run --frozen pytest -q` | 41 passed in 35.75 s |
| Load local demo credentials with `. .\scripts\dev-env.ps1`, then run `npx playwright test` in frontend | 1 passed in 12.4 s |

The browser test creates a synthetic investigation and approves recommendations. Switch roles by signing out. The analytics view shows per-investigation request costs and agent timing. Mock usage appears as N/A.
