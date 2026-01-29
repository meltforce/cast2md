# UI Guidelines

Design principles and patterns for the cast2md web interface.

## Page Layout Pattern

All pages follow a consistent layout structure:

```
┌─────────────────────────────────────────────────────┐
│  Page Title                        [Primary Action] │  ← Page Header
│  Subtitle                                          │
├─────────────────────────────────────────────────────┤
│  ┌────┐ ┌────┐ ┌────┐ ┌────┐                       │  ← Stats Grid
│  │ N1 │ │ N2 │ │ N3 │ │ N4 │                       │
│  └────┘ └────┘ └────┘ └────┘                       │
├─────────────────────────────────────────────────────┤
│  [Secondary Action 1] [Secondary Action 2]          │  ← Action Bar
├─────────────────────────────────────────────────────┤
│                                                     │
│  Main Content (table, list, form, etc.)            │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

## Components

### Page Header (`.page-header`)

Title and primary action on the same row:

```html
<div class="page-header">
    <hgroup>
        <h1>Page Title</h1>
        <p>Brief description of the page</p>
    </hgroup>
    <div class="header-actions">
        <button>Primary Action</button>
    </div>
</div>
```

- Title (h1) and subtitle on the left
- Primary action button(s) on the right
- Responsive: wraps on mobile

### Stats Grid (`.stats-grid`)

At-a-glance metrics displayed as cards:

```html
<div class="stats-grid">
    <div class="stat-card">
        <h3>42</h3>
        <p>Label</p>
    </div>
</div>
```

- Informational only, **not clickable**
- Auto-fits to available width
- Consistent sizing across pages

### Action Bar (`.action-bar`)

Secondary or contextual actions:

```html
{% if has_items %}
<div class="action-bar">
    <button onclick="doAction1()">Action 1</button>
    <button class="secondary" onclick="doAction2()">Action 2</button>
</div>
{% endif %}
```

- Only shown when relevant actions exist
- Buttons grouped with consistent spacing

---

## Button Hierarchy

| Level | Class | Usage |
|-------|-------|-------|
| Primary | (default) | Main action for the page |
| Secondary | `.secondary` | Alternative actions |
| Outline | `.outline` | Lower emphasis, table row actions |
| Outline Secondary | `.outline.secondary` | Lowest emphasis |

---

## Information vs. Navigation

A key design principle: **separate display from interaction**.

| Element | Purpose | Clickable? |
|---------|---------|------------|
| Stat cards | Show counts/metrics | No |
| Buttons | Trigger actions | Yes |
| Links | Navigate to pages | Yes |
| Dropdowns | Filter/select options | Yes |

When you need both counts AND filtering (like the queue page):

- Use stat cards to show counts
- Use a separate control (dropdown, tabs) for filtering

This avoids the confusion of "is this a number or a button?"

---

## Tooltips

Always use **CSS tooltips** instead of native browser `title` attributes. Native tooltips have inconsistent behavior across browsers.

```css
.element-with-tooltip {
    position: relative;
    cursor: help;
}
.element-with-tooltip::after {
    content: attr(title);
    position: absolute;
    bottom: 100%;
    left: 50%;
    transform: translateX(-50%);
    padding: 0.4rem 0.6rem;
    background: var(--pico-card-background-color);
    color: var(--pico-color);
    font-size: 0.75rem;
    border-radius: 4px;
    white-space: nowrap;
    opacity: 0;
    visibility: hidden;
    transition: opacity 0.15s, visibility 0.15s;
    z-index: 1000;
    pointer-events: none;
}
.element-with-tooltip:hover::after {
    opacity: 1;
    visibility: visible;
}
```

The `title` attribute holds the text (for accessibility), but CSS `::after` renders it visually.

---

## Responsive Behavior

- Page header wraps on narrow screens (title above, actions below)
- Stats grid auto-fits columns based on available width
- Action bar wraps buttons as needed
- Tables may require horizontal scroll on mobile

---

## Color Usage

Use CSS variables for dark mode compatibility:

| Variable | Usage |
|----------|-------|
| `var(--pico-primary)` | Primary accent color |
| `var(--pico-muted-color)` | Subdued text |
| `var(--pico-card-background-color)` | Card backgrounds |

Status colors use `color-mix()` for consistent opacity in both light and dark themes.
