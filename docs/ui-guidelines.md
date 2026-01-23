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
│  [Secondary Action 1] [Secondary Action 2]          │  ← Action Bar (optional)
├─────────────────────────────────────────────────────┤
│                                                     │
│  Main Content (table, list, form, etc.)            │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### Components

#### Page Header (`.page-header`)

Title and primary action on the same row:
- Title (h1) and subtitle on the left
- Primary action button(s) on the right
- Responsive: wraps on mobile

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

#### Stats Grid (`.stats-grid`)

At-a-glance metrics displayed as cards:
- Informational only, not clickable
- Auto-fits to available width
- Consistent sizing across pages

```html
<div class="stats-grid">
    <div class="stat-card">
        <h3>42</h3>
        <p>Label</p>
    </div>
    <!-- more stat-cards -->
</div>
```

**Important:** Stats are for displaying information, not for navigation. If filtering or navigation is needed, use separate controls (buttons, dropdowns, tabs).

#### Action Bar (`.action-bar`)

Secondary or contextual actions:
- Only shown when relevant actions exist
- Buttons grouped with consistent spacing
- Typically conditional based on state

```html
{% if has_items %}
<div class="action-bar">
    <button onclick="doAction1()">Action 1</button>
    <button class="secondary" onclick="doAction2()">Action 2</button>
</div>
{% endif %}
```

### Button Hierarchy

1. **Primary** (default): Main action for the page
2. **Secondary** (`.secondary`): Alternative actions
3. **Outline** (`.outline`): Lower emphasis, often for table row actions
4. **Outline Secondary** (`.outline.secondary`): Lowest emphasis

### CSS Classes

Defined in `base.html`:

```css
.page-header        /* Flex container for title + actions */
.header-actions     /* Button group in header */
.action-bar         /* Secondary actions toolbar */
.stats-grid         /* Grid of stat cards */
.stat-card          /* Individual stat display */
```

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

## Tooltips

Always use CSS tooltips instead of native browser `title` attributes. Native tooltips have inconsistent behavior across browsers.

Implementation pattern:
```css
.element-with-tooltip {
    position: relative;
    cursor: help;
}
.element-with-tooltip::after {
    content: attr(title);
    /* positioning and styling */
}
```

The `title` attribute holds the text (for accessibility), but CSS `::after` renders it visually.

## Responsive Behavior

- Page header wraps on narrow screens (title above, actions below)
- Stats grid auto-fits columns based on available width
- Action bar wraps buttons as needed
- Tables may require horizontal scroll on mobile

## Color Usage

Use CSS variables for dark mode compatibility:
- `var(--pico-primary)` - Primary accent color
- `var(--pico-muted-color)` - Subdued text
- `var(--pico-card-background-color)` - Card backgrounds

Status colors use `color-mix()` for consistent opacity in both themes.
