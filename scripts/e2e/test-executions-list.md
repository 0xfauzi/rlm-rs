# E2E Test: Executions List and Filters (Playwright MCP)

## Goal
Verify the executions list renders, filters update the view and URL, and Open links navigate to the correct execution mode pages.

## Preconditions
- UI dev server running at http://localhost:3000
- Playwright MCP server available
- At least two executions exist (one Answerer COMPLETED, one Runtime)

## Steps

### 1) Open Executions list
- MCP tool: `browser_navigate`
  - url: `http://localhost:3000/executions`
- Assertion (MCP tool: `browser_snapshot`):
  - Table is visible and shows at least two rows.

### 2) Capture initial list screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/executions/list-initial.png`

### 3) Filter by COMPLETED status
- MCP tool: `browser_select_option`
  - element: `Status` dropdown
  - value: `COMPLETED`
- MCP tool: `browser_snapshot`
  - Assert only status pills show `COMPLETED`.
  - Assert the URL contains `status=COMPLETED`.

### 4) Filter by Answerer mode
- MCP tool: `browser_select_option`
  - element: `Mode` dropdown
  - value: `ANSWERER`
- MCP tool: `browser_snapshot`
  - Assert mode pills show `Answerer`.
  - Assert the URL contains `mode=ANSWERER`.

### 5) Filter by Session ID prefix
- MCP tool: `browser_type`
  - element: `Session ID` input
  - text: first 6-8 characters of a known session ID
- MCP tool: `browser_snapshot`
  - Assert all visible session IDs start with the typed prefix.
  - Assert the URL contains `session_id=`.

### 6) Reset filters
- MCP tool: `browser_select_option`
  - element: `Status` dropdown
  - value: `ALL`
- MCP tool: `browser_select_option`
  - element: `Mode` dropdown
  - value: `ALL`
- MCP tool: `browser_type`
  - element: `Session ID` input
  - text: ``
- MCP tool: `browser_snapshot`
  - Assert the URL no longer includes `status=` or `mode=` and `session_id=`.
  - Assert multiple rows are visible again.

### 7) Open an Answerer execution
- MCP tool: `browser_click`
  - element: `Open` button on an Answerer execution row
- MCP tool: `browser_snapshot`
  - Assert the URL matches `/executions/[id]`.

### 8) Open a Runtime execution
- MCP tool: `browser_navigate`
  - url: `http://localhost:3000/executions`
- MCP tool: `browser_click`
  - element: `Open` button on a Runtime execution row
- MCP tool: `browser_snapshot`
  - Assert the URL matches `/executions/[id]/runtime`.

## Pass/Fail Criteria
- Filters update the table and URL as expected.
- Answerer and Runtime Open buttons navigate to the correct execution detail pages.
- Screenshot saved at the specified path.
