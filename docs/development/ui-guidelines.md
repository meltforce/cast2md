# UI guidelines

cast2md uses the shared Modernist homelab design language. The authoritative tokens and reusable components live in `src/cast2md/static/homelab.css`; app-specific additions live in `app.css`.

## Page pattern

Pages use the same structural sequence:

1. `.page-head` with a `.kick`, `h1`, and optional `.page-head-actions`
2. `.hero` for up to four informational metrics
3. `.filters` for visible filters or `.actions` for commands
4. `.table` on desktop and `.row` records on mobile
5. `.footer` aligned to `--page-x`

Hero metrics display information only. They are never links or filter controls. When a count also needs filtering, keep the count in the hero and provide separate `.chip` controls in `.filters`.

## Buttons

| Class | Use |
| --- | --- |
| `.btn .btn-primary` | The page's main action |
| `.btn .btn-secondary` | A meaningful alternative |
| `.btn .btn-ghost` | Navigation and compact row actions |
| `.btn .btn-danger` | Destructive actions after confirmation |

Links navigate and buttons act. Do not add `role="button"` to links.

## Status

Eight episode states use four treatments while keeping the API state name as the visible label:

| Treatment | States |
| --- | --- |
| `.status .status-done` | `completed` |
| `.status .status-running` | `downloading`, `transcribing` |
| `.status .status-queued` | `new`, `awaiting_transcript`, `needs_audio`, `audio_ready` |
| `.status .status-failed` | `failed` |

Do not assign a separate hue to each state. Weight, outline, and the written label carry the distinction.

## Tooltips

Use one tooltip pattern: `.tip` plus a `title` attribute. The attribute provides the accessible text; the square CSS bubble provides the visual treatment. Do not add a second tooltip implementation.

## Responsive behavior

The only breakpoint is 768 px. The desktop navigation and tables apply from 768 px; below it the fixed four-item tab bar and `.row` lists apply. Controls must remain keyboard accessible, show the shared accent focus ring, and have at least a 44 px touch target where they are the row's primary action.

## Visual rules

- Use Archivo and the design tokens; do not introduce another font or hard-coded app color.
- Use strong 2 px rules between sections and 1 px rules between records.
- Use no radius, decorative shadow, or decorative motion. The toast shadow is the sole elevation exception.
- Keep content flush to `--page-x`; cap only reading measures such as `.reader`.
- Use Lucide-style inline icons only when a word would be less clear. Do not use emoji as interface symbols.
