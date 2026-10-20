# Accessibility

The [web UI](web-ui.md) aims to meet the [Web Content Accessibility Guidelines (WCAG) 2.1](https://www.w3.org/TR/WCAG21/)
at level AA, so that people who use a keyboard, a screen reader, zoom or other assistive technology can
set up and run the playbooks too. This page says what is checked, how, and what is known not to be covered
yet. Problems are bugs: please report them in
[GitHub Issues](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/issues).

The command-line interface (the playbooks and `make`) is plain text in a terminal and inherits the
terminal's accessibility.

## What is checked

- **Every pull request** checks the structure of every page, signed out and signed in, empty and filled,
  and with validation errors (`webui/tests/test_accessibility.py`): the page language is set, there is one
  `<h1>`, the header, main and footer landmarks and the skip link are there, every form control has a
  label (`<label for>` or, in the servers table, an `aria-label` naming the column and row), IDs are
  unique, and every `for`, `aria-describedby`, `aria-labelledby` and in-page link points at an element
  that exists. The pages with errors must have the error summary, focusable, with links to the fields.
- **[axe-core](https://github.com/dequelabs/axe-core)** (4.10, WCAG 2.0, 2.1 and 2.2 A and AA rules plus
  best practices) was run by hand in Google Chrome on every page in both the light and the dark theme when
  the UI was built, with no violations. It runs again by hand when the UI changes, because it needs a real
  browser and the project has no browser in CI.
- **Colour contrast**: the palette in `webui/usas_web/static/app.css` was chosen for at least 4.5:1 for
  text and 3:1 for borders and focus rings in both themes, and checked with axe. Statuses, errors and the
  steps on the overview are written out in words ("Failed", "Error:", "To do") next to their colour.
- **Keyboard use** was checked in the same browser: controls are reached with Tab in reading order and
  show a visible 3-pixel focus ring; the skip link moves focus to the content; "More settings" sections
  open with Enter or Space; a form sent with errors puts focus on the error summary, whose links move focus
  to each field; no control captures Tab.
- **Small screens**: at 375 pixels wide no page scrolls sideways (this found and fixed one bug in the
  servers table); the servers table and the run history scroll inside their own focusable, labelled
  region. Buttons, text fields and selects are at least 40 pixels tall; checkboxes and radio buttons are
  20 pixels with their labels clickable next to them, and on touch screens the navigation links grow to
  40 pixels (WCAG 2.2, 2.5.8).

## How the UI is built for it

- Server-rendered HTML with native controls only (inputs, selects, checkboxes, radio buttons,
  `<details>`): no custom widgets that would need ARIA patterns. Every page works without JavaScript; the
  script only streams run output and shows times in your time zone.
- Every field has a visible label tied to it, and its help text (and error, if any) is announced with it
  through `aria-describedby`. Invalid fields have `aria-invalid` and a thicker border, not just a colour.
- A form with errors comes back with an error summary at the top that takes focus, says how many problems
  there are and links to each field, and with what you typed still in the fields (except passwords).
- Each page has one `<h1>`, sections with headings, and grouped fields in `<fieldset>`s with legends.
- Run output is in a labelled, focusable region that scrolls with the keyboard. It isn't a live region,
  so a screen reader doesn't read every line of Ansible's output; the run's status is, and is announced
  when the run ends.
- Light and dark themes follow the system setting (`prefers-color-scheme`).

## Known gaps

- **No automated contrast or keyboard check in CI.** Contrast and keyboard use were checked by hand in one
  browser; a regression would show only at the next manual check.
- **No screen-reader walk-through yet.** Nobody has yet done the full setup with NVDA, JAWS or VoiceOver.
  Reports from people who do are very welcome.
- **Run output is Ansible's text**, with long lines and many symbols; it reads poorly with a screen reader.
  Downloading the log and reading it in an editor helps.
- **English only.** The UI isn't translated, and its text isn't yet extracted for translation.
