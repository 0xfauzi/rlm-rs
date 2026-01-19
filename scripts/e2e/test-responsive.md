# E2E Test: Responsive Layout (Playwright MCP)

## Goal
Verify the UI adapts to mobile and desktop viewports and key layouts remain usable.

## Preconditions
- UI dev server running at http://localhost:3000
- Playwright MCP server available

## Steps

### 1) Set viewport to mobile
- MCP tool: `browser_resize`
  - width: `375`
  - height: `667`

### 2) Load Sessions page on mobile
- MCP tool: `browser_navigate`
  - url: `http://localhost:3000/sessions`
- MCP tool: `browser_snapshot`
  - Assert the page renders without error.
  - Assert the sidebar is collapsed or a hamburger menu button is visible.

### 3) Capture mobile Sessions screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/responsive/sessions-mobile.png`

### 4) Set viewport to desktop
- MCP tool: `browser_resize`
  - width: `1280`
  - height: `800`

### 5) Reload Sessions page on desktop
- MCP tool: `browser_navigate`
  - url: `http://localhost:3000/sessions`
- MCP tool: `browser_snapshot`
  - Assert the sidebar is visible.
  - Assert the sessions table shows all columns.

### 6) Capture desktop Sessions screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/responsive/sessions-desktop.png`

### 7) Switch to mobile and open execution detail
- MCP tool: `browser_resize`
  - width: `375`
  - height: `667`
- MCP tool: `browser_navigate`
  - url: `http://localhost:3000/executions`
- MCP tool: `browser_click`
  - element: `Open` button for a recent execution
- MCP tool: `browser_snapshot`
  - Assert the execution detail page loads.
  - Assert the left/right panels stack vertically or are scrollable.

### 8) Capture mobile execution screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/responsive/execution-mobile.png`

### 9) Open Debug page on mobile
- MCP tool: `browser_navigate`
  - url: `http://localhost:3000/debug`
- MCP tool: `browser_snapshot`
  - Assert the panels stack vertically.

### 10) Capture mobile Debug screenshot
- MCP tool: `browser_screenshot`
  - path: `ui/tests/e2e/screenshots/responsive/debug-mobile.png`

### 11) Toggle sidebar on mobile
- MCP tool: `browser_click`
  - element: hamburger menu button
- MCP tool: `browser_snapshot`
  - Assert the sidebar slides in or becomes visible.
- MCP tool: `browser_click`
  - element: overlay or close button
- MCP tool: `browser_snapshot`
  - Assert the sidebar closes.

## Pass/Fail Criteria
- Layout adjusts correctly between mobile and desktop viewports.
- Mobile screenshots are saved to the specified paths.
- Sidebar toggle works on mobile.
