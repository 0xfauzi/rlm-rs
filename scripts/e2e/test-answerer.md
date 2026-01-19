# E2E Test: Answerer Execution Flow (Playwright MCP)

## Goal
Verify Answerer execution can be started from a session, completes, renders an answer, and allows citation inspection and verification.

## Preconditions
- UI dev server running at http://localhost:3000
- Playwright MCP server available
- API and LocalStack services running
- A session exists and is in READY status

## Steps

### 1) Start Answerer from session detail
- MCP tool: `browser_click`
  - element: `Start Answerer` button

### 2) Verify Answerer modal appears
- MCP tool: `browser_snapshot`
  - Assert a modal is visible with a question textarea.

### 3) Capture modal screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/answerer/modal-open.png`

### 4) Enter question
- MCP tool: `browser_type`
  - element: question textarea
  - text: `What is the main topic of this document?`

### 5) Start execution
- MCP tool: `browser_click`
  - element: `Start Execution` button

### 6) Verify execution detail URL
- MCP tool: `browser_snapshot`
  - Assert the URL matches `/executions/[id]`.

### 7) Capture execution started screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/answerer/execution-started.png`

### 8) Verify running status
- MCP tool: `browser_snapshot`
  - Assert status shows `RUNNING` or `PENDING`.

### 9) Wait for completion
- MCP tool: `browser_snapshot` (poll up to 60s)
  - Assert status shows `COMPLETED`.

### 10) Verify answer content
- MCP tool: `browser_snapshot`
  - Assert answer text appears and is non-empty.

### 11) Capture execution completed screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/answerer/execution-completed.png`

### 12) Verify citations section
- MCP tool: `browser_snapshot`
  - Assert the Citations section is visible.
  - Assert at least one citation card shows a doc index and char range.

### 13) Capture citations screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/answerer/citations-visible.png`

### 14) Open citation inspector
- MCP tool: `browser_click`
  - element: `Inspect` button on the first citation

### 15) Verify citation viewer
- MCP tool: `browser_snapshot`
  - Assert the URL contains `/citations` with query params.
  - Assert metadata fields are populated.
  - Assert excerpt text includes a yellow highlight.

### 16) Capture citation viewer screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/answerer/citation-viewer.png`

### 17) Verify citation
- MCP tool: `browser_click`
  - element: `Verify citation` button
- MCP tool: `browser_snapshot`
  - Assert the result shows `Valid` or `Invalid`.

## Pass/Fail Criteria
- Answerer execution starts and reaches COMPLETED.
- Answer content renders with non-empty text.
- Citations are visible and inspection view loads.
- Verification result is displayed.
- Screenshots saved at the specified paths.
