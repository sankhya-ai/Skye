---
name: mac-apps
description: Operate the owner's Mac apps with the mac_* tools (root-driver) — open an app, read a window, click, type, fill a form, check the result — in the background, without taking their cursor or focus.
---

# Mac apps (root-driver)

The `mac_*` tools work on real windows through the accessibility tree, with screenshots
and OCR when the tree is not enough. They act in the background: the owner's cursor and
focus stay where they are.

## The loop

1. **Find the app.** `mac_list_apps` / `mac_list_windows`, or `mac_launch_app` (`bundle_id` is surest).
2. **Look.** `mac_get_window_state` with the app's `pid` or `bundle_id`. It returns a `snapshot_id`
   and elements, each with an `element_token`, role, label and value.
3. **Act** on what you saw, passing the `snapshot_id` and the element's `element_token`:
   `mac_set_value` for text fields, `mac_click` for buttons, `mac_press_key` / `mac_hotkey` with a
   `chord` like `cmd+s` (or `menu_path` like `["File", "Save"]`), `mac_scroll`, `mac_drag`.
4. **Check.** Look again, or `mac_verify_action` with both snapshot ids. Read each result's `ok`,
   `performed` and `verification` — never assume an action landed.

A stale snapshot or a moved target: look again. Nothing in the tree: `mac_capture_window`
(with `include_ocr`), `mac_zoom_region`, `mac_vision_ground` with a `query`.

Only windows on the current Space can be read; an app on another Space or minimized reads
empty — say so rather than guessing.

## Forms

Look once, fill each field with `mac_set_value`, check the values in the next look. Values come
from the owner, their files, or memory — never invent one; ask for what is missing. Stop before
submit and say what you filled.

## Never without being asked

Submitting, sending, paying, deleting, `mac_close_window`, `mac_kill_app`, and typing passwords.
If a result says Accessibility is missing, ask the owner to allow **Root Driver** in System
Settings → Privacy & Security.
