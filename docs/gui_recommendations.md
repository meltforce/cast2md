# GUI Recommendations

## 1. Dark Mode Support
**Current Status**: Forced Light Mode.
- `base.html` line 2: `<html lang="en" data-theme="light">`

**Fix**:
1.  **Enable Auto-Detection**: Change to `<html lang="en">` (removes the force). Pico CSS will automatically match the user's OS preference.
2.  **Add a Toggle**: Add a robust JS toggle for users who want to override the OS setting.
    ```javascript
    // Simple toggle logic
    const html = document.documentElement;
    const current = html.getAttribute("data-theme");
    html.setAttribute("data-theme", current === "dark" ? "light" : "dark");
    ```
3.  **Fix Hardcoded Colors**:
    - Current: `.status-pending { background: #fef3c7; color: #92400e; }`
    - Problem: These colors look bad/unreadable in dark mode.
    - Solution: Use CSS variables or transparent colors.
    - Example: `background: color-mix(in srgb, var(--pico-primary) 10%, transparent);`

## 2. Mobile Optimizations
**Current Status**: Basic responsive design via Pico CSS.
**Issues**:
- **Navigation Overflow**: The top menu has 7 items. On mobile (<400px), this will wrap uglily or scroll horizontally.
- **Touch Targets**: Some buttons might be too small.

**Fix**:
1.  **Overflow Scroll**: Add `overflow-x: auto` to the `<nav>` container to allow smooth scrolling on small phones.
2.  **Compact Nav**: On mobile, hide less critical links (Status, Settings, API) behind a "More" dropdown or icon.
3.  **Bottom Nav**: For a true "app-like" feel, move primary actions (Feeds, Queue, Search) to a fixed bottom bar on mobile devices.

## 3. General Polish
- **Loading States**: Add HTMX specific indicators (`htmx-indicator`) to show a spinner when performing long actions (like "Search" or "Refresh Feed").
- **Toasts**: Use a toast notification system for "Queued download" messages instead of simple alerts or page reloads.
