# E2E Test: Session Creation Flow (Playwright MCP)

## Goal
Verify a user can upload a document, create a session, and open the session detail view.

## Preconditions
- UI dev server running at http://localhost:3000
- Playwright MCP server available
- API and LocalStack services running
- Test fixture exists at `ui/tests/e2e/fixtures/test-doc.txt`

## Steps

### 1) Open Sessions page
- MCP tool: `browser_navigate`
  - url: `http://localhost:3000/sessions`
- Assertion (MCP tool: `browser_snapshot`):
  - Upload section is visible and heading includes `Upload and Create`.

### 2) Capture initial Sessions screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/sessions/page-initial.png`

### 3) Upload fixture document
- MCP tool: `browser_file_upload`
  - paths: [`/Users/wumpinihussein/Documents/code/rlm-rs/ui/tests/e2e/fixtures/test-doc.txt`]
  - Use the file input within the Upload panel.

### 4) Verify source name auto-populates
- MCP tool: `browser_snapshot`
  - Assert the source name input value is `test-doc.txt`.

### 5) Set TTL to 30
- MCP tool: `browser_type`
  - element: `TTL` input
  - text: `30`

### 6) Submit upload
- MCP tool: `browser_click`
  - element: `Upload and Create` button

### 7) Verify session creation success
- MCP tool: `browser_snapshot`
  - Assert a success toast appears OR a new session row appears.
  - New session row shows status `CREATING` or `READY`.

### 8) Capture created session screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/sessions/session-created.png`

### 9) Open the new session
- MCP tool: `browser_click`
  - element: `Open` button for the newly created session
- MCP tool: `browser_snapshot`
  - Assert the URL matches `/sessions/[uuid]`.
  - Assert `Session ID` is visible on the page.

### 10) Capture session detail screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/sessions/session-detail.png`

### 11) Wait for READY status
- MCP tool: `browser_snapshot` (poll up to 30s)
  - Assert the status pill shows `READY`.
  - Assert `Start Answerer` button is enabled.

## Pass/Fail Criteria
- Upload completes and a session appears in the Sessions table.
- Session detail page renders with a visible Session ID.
- Status eventually reaches READY and Start Answerer is enabled.
- Screenshots saved at the specified paths.
