# PnlSummaryCard — manual test scenarios

Kanban #1329 (M6 FE). Repo has no Jest harness for the FE; these are the
scenarios an operator walks through end-to-end before declaring the slice
DONE. Each row lists the input shape, the surface to check, and the
expected output.

## Scenarios

1. **Revenue only, $0 cost** (the seeded `agent-teams` project today —
   1 manual transaction, $500 revenue, USD)
   - URL: `/p/agent-teams` (period selector default = Last 30 days)
   - Expected:
     - Revenue tile: `$500.00`
     - Expenses tile: `$0.00`
     - Net tile: `$500.00` (emerald green) with `(+100.0%)` margin
     - "1 transaction" footer
     - Currency label: `USD`
     - No `(mixed)` badge

2. **Zero transactions in window**
   - URL: any project with no `transactions` rows in the window
     (e.g. `/p/secretary` today)
   - Expected:
     - Empty-state copy: "No transactions yet in this window."
     - CTA link to `/p/<name>/settings` for webhook config

3. **Mixed-currency** (manual setup: POST two transactions to the same
   project with different `currency` codes, e.g. USD + EUR, both inside the
   selected window)
   - Expected:
     - Header currency label shows `(mixed)` badge next to the first-observed
       currency code
     - Tile totals are first-currency-only (the BE returns first observed in
       `summary.currency`)

4. **Period swap re-fetches**
   - Action: change the dropdown from "Last 30 days" → "All time"
   - Expected:
     - Tiles flash to "Loading P&L…" momentarily
     - New totals render
     - localStorage key `pnl_period_default` updated to `all_time`
     - Next project page load (or dashboard) opens with `all_time` selected

5. **Network error → graceful state**
   - Action: stop the API container (`docker compose stop api`), then mount
     a project page
   - Expected:
     - Card renders header + selector
     - Body shows red "P&L unavailable — <status> <message>" text
     - No console exception, no UI freeze

6. **Negative net** (manual setup: cost > revenue in window)
   - Expected:
     - Net tile red
     - Margin chip shows `-NN.N%`
     - Cross-project dashboard row: Net column red

## Dashboard cross-project scenarios

7. **All projects same currency** → grand-total chip visible next to the
   "N projects" count.

8. **Multi-currency portfolio** (manual setup: at least 2 projects with
   different `currency_default` values both having transactions) →
   `grand_total_net_first_currency_only` is null on the BE, FE hides the
   chip entirely and relies on the per-row table.

9. **Zero-transaction rows** → row body dimmed (opacity-60), tooltip says
   "no transactions in window".
