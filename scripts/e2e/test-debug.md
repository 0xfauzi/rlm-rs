# E2E Test: Debug Console (Playwright MCP)

## Goal
Verify the Debug Console panels, request log controls, configuration panel, and reseed workflow.

## Preconditions
- UI dev server running at http://localhost:3000
- Playwright MCP server available
- LocalStack and API available for online status checks

## Steps

### 1) Open Debug Console
- MCP tool: `browser_navigate`
  - url: `http://localhost:3000/debug`
- Assertion (MCP tool: `browser_snapshot`):
  - Page renders without error.
  - Panels visible: `API Health`, `LocalStack Health`, `Recent Requests`.

### 2) Capture initial screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/debug/initial.png`

### 3) Verify health status badges
- MCP tool: `browser_snapshot`
  - Assert API Health badge shows `Online`.
  - Assert LocalStack Health badge shows `Online`.

### 4) Refresh health and logs
- MCP tool: `browser_click`
  - element: `Refresh` button
- MCP tool: `browser_snapshot`
  - Assert `Last check` timestamps update or a loading skeleton appears then clears.

### 5) Verify request log entries
- MCP tool: `browser_snapshot`
  - Assert at least one request row contains Method, URL, and Status.

### 6) Clear request log
- MCP tool: `browser_click`
  - element: `Clear request log`
- MCP tool: `browser_snapshot`
  - Assert the request list shows `No requests logged yet.`

### 7) Open configuration panel
- MCP tool: `browser_click`
  - element: `Settings` button
- MCP tool: `browser_snapshot`
  - Assert the `Configuration` panel is visible.
  - Assert input fields for `API base URL` and `LocalStack endpoint` are present.

### 8) Capture configuration screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/debug/config-expanded.png`

### 9) Reseed dev key
- MCP tool: `browser_click`
  - element: `Re-seed dev key`
- MCP tool: `browser_snapshot`
  - Assert toast appears with `Dev key re-seeded.` or `Dev key reseed failed.`

### 10) Capture reseed toast screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/debug/reseed-toast.png`

## Pass/Fail Criteria
- All panels render without error.
- Snapshots confirm health badges, request log behavior, and configuration panel.
- Screenshots saved at the specified paths.
