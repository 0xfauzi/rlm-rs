# E2E Test: Runtime Multi-Step Flow (Playwright MCP)

## Goal
Verify the Runtime execution editor supports multi-step runs, state inspection, and reset behavior.

## Preconditions
- UI dev server running at http://localhost:3000
- Playwright MCP server available
- API and LocalStack services running
- A session exists and is in READY status

## Steps

### 1) Start Runtime from session detail
- MCP tool: `browser_click`
  - element: `Start Runtime` button

### 2) Confirm modal and launch runtime
- MCP tool: `browser_snapshot`
  - Assert the Start Runtime modal appears.
- MCP tool: `browser_click`
  - element: `Start Runtime` button in modal

### 3) Verify runtime URL
- MCP tool: `browser_snapshot`
  - Assert the URL matches `/executions/[id]/runtime`.

### 4) Capture initial editor screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/runtime/editor-initial.png`

### 5) Enter first step and run
- MCP tool: `browser_snapshot`
  - Assert a step textarea is visible.
- MCP tool: `browser_type`
  - element: step 1 textarea
  - text: `print('hello from step 1')`
- MCP tool: `browser_click`
  - element: `Run Step` button

### 6) Verify stdout output
- MCP tool: `browser_snapshot`
  - Assert stdout shows `hello from step 1`.

### 7) Capture step result screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/runtime/step-1-result.png`

### 8) Add steps 2 and 3
- MCP tool: `browser_click`
  - element: `Add Step` button
- MCP tool: `browser_type`
  - element: step 2 textarea
  - text: `state['counter'] = 1`
- MCP tool: `browser_click`
  - element: `Add Step` button
- MCP tool: `browser_type`
  - element: step 3 textarea
  - text: `print(state.get('counter', 0))`

### 9) Run all steps
- MCP tool: `browser_click`
  - element: `Run All` button
- MCP tool: `browser_snapshot`
  - Assert stdout shows `1`.
  - Assert state tab shows JSON with `counter: 1`.

### 10) Capture run-all screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/runtime/run-all-completed.png`

### 11) Reset state
- MCP tool: `browser_click`
  - element: `Reset State` button
- MCP tool: `browser_snapshot`
  - Assert the URL has a new execution ID.
  - Assert output inspector is cleared.
  - Assert step code is preserved.

### 12) Capture reset screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/runtime/after-reset.png`

## Pass/Fail Criteria
- Runtime execution loads and runs individual steps.
- Stdout and state updates are visible after Run Step and Run All.
- Reset State creates a new execution and preserves step code.
- Screenshots saved at the specified paths.
