# E2E Test: App Shell and Navigation (Playwright MCP)

## Goal
Verify the UI shell loads, the top bar health badges appear, and primary navigation works.

## Preconditions
- UI dev server running at http://localhost:3000
- Playwright MCP server available

## Steps

### 1) Load the app shell
- MCP tool: `browser_navigate`
  - url: `http://localhost:3000`
- Assertion (MCP tool: `browser_snapshot`):
  - Page renders without error.

### 2) Capture home screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/app-shell-home.png`

### 3) Verify health badges in top bar
- MCP tool: `browser_snapshot`
  - Assert the top bar contains text `API` and `LocalStack`.
  - Assert the badge text shows either `Online` or `Offline` for each.

### 4) Navigate to Sessions
- MCP tool: `browser_click`
  - element: `Sessions` (sidebar link)
- MCP tool: `browser_snapshot`
  - Assert the snapshot URL contains `/sessions`.

### 5) Navigate to Executions
- MCP tool: `browser_click`
  - element: `Executions` (sidebar link)
- MCP tool: `browser_snapshot`
  - Assert the snapshot URL contains `/executions`.

### 6) Navigate to Debug
- MCP tool: `browser_click`
  - element: `Debug` (sidebar link)
- MCP tool: `browser_snapshot`
  - Assert the snapshot URL contains `/debug`.

### 7) Verify active Sessions styling
- MCP tool: `browser_navigate`
  - url: `http://localhost:3000/sessions`
- MCP tool: `browser_snapshot`
  - Assert the Sessions link has active styling.
  - Active styling is indicated by the link class containing `bg-slate-900` and `text-white`.

### 8) Clicking API badge opens Debug
- MCP tool: `browser_click`
  - element: `API` health badge in the top bar
- MCP tool: `browser_snapshot`
  - Assert the snapshot URL contains `/debug`.

## Pass/Fail Criteria
- All navigations complete without errors.
- Snapshots confirm expected text and URL changes.
- Screenshot file is saved at the specified path.
