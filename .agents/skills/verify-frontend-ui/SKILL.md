---
name: verify-frontend-ui
description: Verify this repository's React chat, Stepper, Developer Tools surfaces, modals, responsive layout, and accessibility in a real browser. Use after frontend, CSS, interaction, or design-system changes.
---

```toml
id = "89495166b5c61c185efad198401b6957"
```

# Verify frontend UI changes

Read [site/frontend/DESIGN.md](../../../site/frontend/DESIGN.md) before acting.
The live application is chat-first: transcript, Composer, result cards, and the
registry-derived ten-node Stepper. Do not use retired top-panel or agent-card
layouts as the verification target.

## Start locally

```bash
node deploy/preview.mjs --dev
```

Preview binds locally by default. Use `--lan` only when the user explicitly
needs trusted-LAN testing. Record the actual ports reported by the launcher.
Avoid paid workflow execution unless the task requires it; use saved fixtures
or mocked API responses for UI states.

## Drive and measure

Use the available browser-control skill. Exercise desktop, tablet, and narrow
mobile sizes around documented breakpoints. Inspect, rather than infer:

- transcript and Composer scrolling, wrapping, focus, labels, keyboard send;
- all ten Stepper nodes, active-node centering, status text, and keyboard access;
- User, Teacher, and Dev surfaces after satisfying the shared access gate,
  without treating a visual mode as authorization;
- loading, empty, blocked, error, stopped, and completed states;
- modal focus trap, Escape/close behavior, focus restoration, stacking, and
  overflow;
- optional donation link visibility and external-link safety when configured.

Use DOM snapshots and computed styles for dimensions, overflow, focus rings,
contrast evidence, and hit-target sizes. A screenshot alone is not proof.

## Finish

From `site/`, run frontend tests, lint, and build. Remove temporary screenshots,
stop preview processes, and update
[site/frontend/DESIGN.md](../../../site/frontend/DESIGN.md) when the verified
design contract changed. Report URLs, viewport sizes, interactions exercised,
measurements, and any states not verified.
