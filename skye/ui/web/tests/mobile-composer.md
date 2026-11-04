# Composer viewport regression

Build: `npm ci && npx tsc --noEmit && npm run build` from `skye/ui/web`.
Open the running Skye UI with at least one project and team configured. Use
browser responsive mode at 320, 375, 390, 640, 768 and 1440 CSS pixels wide.

At each width:

- Enter a goal. Verify Project, Team, Solo/Team and Start task remain visible.
- Check `document.documentElement.scrollWidth === document.documentElement.clientWidth`.
- Open Project and Team menus. Neither menu should extend beyond the viewport.
- Press Escape to dismiss each menu.
- Switch to Solo: the team control disappears. Switch to Team: it returns.
- Confirm the Start task button is reachable without horizontal scrolling.
- Use long project/team labels and check they do not push the send button out.

Below 640px the controls wrap, with Start task on a separate right-aligned row
when needed. On desktop they retain their compact layout. This is a layout-only
change; task submission, team selection and status colors are unchanged.
