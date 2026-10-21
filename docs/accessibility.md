# Accessibility

The [web UI](web-ui.md) aims to meet the [Web Content Accessibility Guidelines (WCAG) 2.1](https://www.w3.org/TR/WCAG21/)
at level AA, so that people who use a keyboard, a screen reader, zoom or other assistive technology can
set up and run the playbooks too. This page says what is checked, how, and what is known not to be covered
yet. Problems are bugs: please report them in
[GitHub Issues](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/issues).

The command-line interface (the playbooks and `make`) is plain text in a terminal and inherits the
terminal's accessibility.

## What is checked

On **every pull request** (the required "Web UI tests" check):

- **Every page the app draws** (`webui/frontend/test/a11y.test.tsx`): the overview, Proxmox, SSH key,
  defaults, servers, hardening, run, history, a running and a finished run, the account page, sign-in,
  first-run setup and the page for an unknown address, and the Proxmox, servers and sign-in pages with
  validation errors, and the phone menu open. Each is checked with
  **[axe-core](https://github.com/dequelabs/axe-core)** 4.14 (the WCAG 2.0 and 2.1 A and AA rules and best
  practices), and with the project's own structural checks: one `<h1>`, the header, main and footer
  landmarks and the skip link, a label for every control (`<label for>` or, in the servers table, an
  `aria-label` naming the column and row), unique IDs, every `for`, `aria-describedby`, `aria-labelledby`
  and `aria-controls` and every in-page link pointing at an element that exists, and a name for every
  button. A form sent with errors must put focus on the error summary.
- **Colour contrast of every colour pair** the UI uses (`webui/frontend/test/contrast.test.ts`), computed
  as WCAG defines it from the colour tokens in `webui/frontend/src/styles/tokens.css`, in the dark and the
  light theme: text, muted text, links, button labels on the accent, the selected navigation item, the
  status colours on every surface and on their own badge tints, and the log's coloured lines need 4.5:1;
  form borders, the focus ring, icons and the error summary's border need 3:1 (WCAG 1.4.3 and 1.4.11). The
  test also checks that both themes define the same tokens and that "System" uses the light values when the
  browser prefers light. axe can't measure contrast in the test's simulated browser, which doesn't lay pages
  out; this test covers it instead.
- **The HTML the server sends** for every page (`webui/tests/test_accessibility.py`): the language, a
  title, one `<h1>`, the landmarks, the skip link, unique IDs and a message for browsers without
  JavaScript; and nothing inline that the Content Security Policy would block.
- **The app in a real browser** (`webui/frontend/e2e/`, [Playwright](https://playwright.dev/) in Chromium,
  run by `tests/webui-browser.sh` against the server and the mock Proxmox API; see
  [testing.md](testing.md#the-browser-tests)):
  - **By keyboard only**, the whole setup: the first-run page, the Proxmox form (typing every field, the
    connection test that fails on the certificate, saving the CA, the test again), picking the node from
    its dropdown, starting a provisioning run and following it to the end, then signing in again,
    including a wrong password.
  - **On every page** (signed in, and the first-run and sign-in pages), at desktop width, and the hardening
    page at 375 pixels: Tab reaches every control
    in the order of the page (no positive `tabindex`), each with a focus outline at least 2 pixels wide
    that is in view and not covered by anything (such as the sticky top bar); Tab leaves the page
    instead of going round in part of it (no keyboard trap); Shift+Tab goes back through the same
    stops.
  - **Focus and announcements**: the skip link is first and moves focus to the content; after a change of
    page, focus is on the new page's heading; a form sent with errors or a wrong password puts focus on
    the message; the phone layout's **Menu** opens with Enter, Escape closes it and puts focus back on
    the button, and moving focus out of it closes it; the node dropdown keeps focus on Escape and lets
    Tab go on; the theme radio buttons change the theme with the arrow keys; adding or removing a row of
    the servers table moves focus to a field of the table. While a run streams its output, focus stays
    where it is, the output isn't a live region, and the run's status changes inside a polite live region
    that was on the page from the start, so "Succeeded" is announced once.
  - **axe-core 4.14** (the WCAG 2.0 and 2.1 A and AA rules) with real layout, so **with contrast**, on
    every page (overview, Proxmox, SSH key, defaults, servers, hardening, run, history, a finished run,
    account, sign-in), on the Proxmox page with an error, and at 375 pixels with the menu open: in the
    dark (default) and light themes and in "System" with the browser preferring each, with every accent
    palette the UI offers (one, Ember). Any violation fails.

Breaking either on purpose fails the check: a style without the focus outline on text fields failed the
keyboard test, and a lighter grey for muted text in the light theme failed axe-core's contrast rule there.

**By hand**, on 2026-10-21, with the Ember palette, in Google Chrome 155:

- **axe-core 4.14 in the browser**, with real layout and so with contrast (WCAG 2.0, 2.1 and 2.2 A and AA
  rules and best practices), on every signed-in page in the dark, light and system themes (30 page
  checks), on the sign-in page in both themes, and at 375 pixels wide with the menu closed and open:
  **no violations**. axe could not decide the contrast of a few elements that sit inside scrolling areas
  (the last column of the servers table and the log's lines), because other content overlaps them while
  scrolled; those colour pairs are among the ones the contrast test checks.
  The run found one real problem, fixed before release: the theme switch faded colours for 160 ms, and
  axe measured the navigation's old dark-theme grey on the new white background. Theme changes are now
  instant.
- **Keyboard use**: Tab reaches the skip link first, with a visible 3-pixel focus ring; Enter on it moves
  focus to the page; Tab then reaches the controls in reading order. "More settings" sections open with
  Enter or Space; a form sent with errors puts focus on the error summary, whose links move focus to each
  field; after a change of page, focus moves to the new page's heading and the browser tab's title changes,
  so screen readers announce the new page. No control captures Tab.
- **Small screens**: at 375 pixels wide no page scrolls sideways; the sidebar folds into a top bar with a
  **Menu** button (`aria-expanded`, `aria-controls`); the servers table and the run history scroll inside
  their own focusable, labelled region. Buttons, text fields and selects are at least 40 pixels tall;
  checkboxes and radio buttons are 20 pixels with their labels clickable next to them, and the switches
  for the log are 36 by 22 pixels with their label (WCAG 2.2, 2.5.8).

## The colours

Dark is the default theme; light and "System" (follows `prefers-color-scheme`) are a click away and
remembered for the browser. The accent is "Ember", a Proxmox-like orange: `#ff9447` on the dark theme and
`#b2470a` on the light one. Lowest contrast of each against the page, a card and a raised surface:

| Colour | Dark theme | Light theme | Needs |
| --- | --- | --- | --- |
| Body text | 13.1:1 | 15.5:1 | 4.5:1 |
| Muted text (help, labels) | 6.3:1 | 5.9:1 | 4.5:1 |
| Accent as a link | 7.2:1 | 4.8:1 | 4.5:1 |
| Button label on the accent | 8.8:1 | 5.5:1 | 4.5:1 |
| Focus ring | 9.0:1 | 4.8:1 | 3:1 |
| Form control border | 3.2:1 | 3.3:1 | 3:1 |
| Success, warning, error, info text | 8.2, 8.8, 6.3, 7.4:1 | 4.7, 5.1, 5.0, 5.0:1 | 4.5:1 |

The test computes every pair from the token file, so these numbers can't drift from what ships. Statuses,
errors and the steps on the overview are written out in words ("Failed", "Error:", "To do", and in the
navigation "(done)" or "(to do)" for screen readers) next to their colour; the log's coloured lines start
with Ansible's own words (`ok:`, `changed:`, `fatal:`). The accent and the warning colour are both orange:
nothing uses the accent to mean a warning.

## How the UI is built for it

- Native controls only (inputs, selects, checkboxes, radio buttons, `<details>`), styled but not replaced:
  no custom widgets that would need ARIA patterns. The theme choice is three radio buttons; the log's
  switches are checkboxes.
- Every field has a visible label tied to it, and its help text (and error, if any) is announced with it
  through `aria-describedby`. Invalid fields have `aria-invalid` and a thicker border, not just a colour.
- A form with errors shows an error summary at the top that takes focus, says how many problems there are
  and links to each field, and keeps what you typed in the fields; an error in a "More settings" field
  opens that section.
- Each page has one `<h1>`, sections with headings, and grouped fields in `<fieldset>`s with legends.
- Messages after an action (saved, connected, a failed test) are in live regions: errors as alerts, the
  rest announced politely.
- On narrow screens the navigation is behind a **Menu** button (`aria-expanded`, `aria-controls`).
  Escape closes it and returns focus to the button; moving focus out of it closes it too, so it never
  covers the part of the page that has focus.
- Run output is in a labelled, focusable region that scrolls with the keyboard. It isn't a live region, so
  a screen reader doesn't read every line of Ansible's output; the run's status is, and is announced when
  the run ends. Line numbers are drawn by CSS, so they aren't copied or read out with the text.
- Motion is limited to a pulsing dot on a running run and a short fade of buttons on hover; with
  `prefers-reduced-motion` there is none.
- The fonts are Atkinson Hyperlegible Next and Atkinson Hyperlegible Mono (SIL Open Font License 1.1),
  designed by the Braille Institute for low-vision readers: letters that are easy to confuse, such as I, l
  and 1 or 0 and O, look different. Text is 16 pixels by default and follows the browser's zoom and font
  size.

## Screen-reader walk-through

The automated tests check what screen readers rely on (names, roles, states, live regions, where focus
goes), but not what a screen reader says. This walk-through checks that, with **VoiceOver on macOS** and
**NVDA on Windows**. The maintainer runs it before each release as part of the
[pre-release checklist](release-checklist.md#web-ui), and anyone is welcome to run it and report what they
find. It takes about half an hour per screen reader.

**Not run yet.** No release has recorded a result; the table at the end is where the first one goes.

### Before you start

Use an instance with an empty data volume, so the first step is the real first-run page. The release
candidate from the pre-release checklist is one (run the walk-through before its "First run" step);
any test instance is another:

```bash
docker run -d --name usas-sr -p 127.0.0.1:8080:8080 -v usas-sr-data:/data honeybeartech/ubuntu-server-auto-setup
docker logs usas-sr     # the setup code
```

A Proxmox VE API that the connection test can reach is needed for steps 5 and 6 (a test node or the
cluster of the pre-release checklist). Afterwards remove the container and its volume
(`docker rm -f usas-sr && docker volume rm usas-sr-data`).

| | VoiceOver (macOS) | NVDA (Windows) |
| --- | --- | --- |
| Browser | Safari. In Safari's settings, under Advanced, turn on "Press Tab to highlight each item on a webpage", or Tab skips links and buttons | Firefox or Chrome |
| Start and stop | Command+F5 | Control+Alt+N to start, NVDA+Q to quit (NVDA is Insert or Caps Lock) |
| See what it says | VoiceOver Utility, Visuals: "Show caption panel" | NVDA menu, Tools, Speech viewer |
| Next item, activate | VO+Right arrow, VO+Space (VO is Control+Option) | Down arrow in browse mode; Enter or Space |
| Headings, landmarks, form fields | VO+U (the rotor), then Left or Right arrow to pick the list | H, D, F; NVDA+F7 for lists |
| Forms | Tab, and type | Tab; NVDA switches to focus mode in a field by itself (a short sound) |

The wording differs between screen readers and browsers ("edit text" or "edit", "pop-up button" or "combo
box"); what matters is that the information in quotes below is there, in that order, and that nothing
important is missing or read twice.

### Steps and what you should hear

1. **The first-run page.** Open `http://localhost:8080`. Expected: the page title "Set up · USAS" and
   the heading "Set the password", level 1. The first Tab: "Skip to the content, link"; Enter on it
   moves to the content.
2. **Set the password.** Tab through the form: "Setup code, edit text"; "Password, secure edit text",
   then its help, "At least 12 characters. A passphrase of several words works well."; "Password again,
   secure edit text"; "Set the password, button". First type a wrong setup code and press Enter:
   expected: "That setup code is wrong. It is in the container's log: docker logs usas", read at once,
   with focus on the message. Then the right code: the page changes, and you hear the heading "Proxmox
   API", level 1, and then "Password set. Start with the Proxmox API."
3. **Find your way.** List the landmarks: banner, navigation "Main", main and content information (the
   footer). List the headings: one level 1 heading per page, sections at level 2. In the navigation,
   Proxmox, SSH key, Defaults and Servers end with "(to do)" or "(done)", and the current page is
   "current page".
4. **The Proxmox form.** Each group says its name ("API", "Placement"); each field its label, "(required)"
   where it is required, and its help with the variable it sets, for example "API host (required), edit
   text, Host name or IP address of a Proxmox VE node (the API listens on it). Variable pve_api_host."
   The token secret says whether a value is saved. Clear the API host and choose **Save**: expected:
   "There is a problem" (or "There are 2 problems") read at once, with focus on the list; its link
   moves to the field, which now says "invalid" and "Error: API host is required."
5. **Test the connection.** Fill in the API fields and choose **Test the connection**. Expected, without
   focus moving: "Connected to Proxmox VE …, cluster … Nodes: … Nothing was saved yet." If the
   certificate isn't trusted, the error is read at once instead; save the CA in the "Cluster CA
   certificate" section ("Saved the CA certificate …") and test again.
6. **Pick a node.** Tab to the node: "Node name (required), pop-up button" (or "combo box"), then the
   chosen node and its state, for example "pve1 (online)", and the help "The nodes of cluster …". Open
   the list (VoiceOver: VO+Space; NVDA: Alt+Down arrow), move through the nodes, close it with Escape:
   focus stays on the dropdown. Choose **Save**: "Saved the Proxmox settings."
7. **The inventory form.** On **Defaults**, fill in and save ("Saved the defaults."). On **Servers**:
   the table "Servers (1 row)"; each field says its column and row, for example "Name, row 1, edit
   text"; Tab to "Add a row, button" and press it: focus moves to "Name, row 2". "Remove row 2, button":
   "Row removed. Save to keep the change.", and focus on a field of row 1. Fill in one server and
   **Save**: "Saved 1 server(s)."
8. **Start a run.** On **Run**: the group "What to run" with radio buttons, for example "provision,
   radio button, 2 of 3", each with its description; "Check mode, checkbox"; "Limit, edit text" with its
   help. Choose check mode and your test server's name as the limit, then **Start the run**. Expected:
   the page changes and you hear the run's heading, for example "provision (check mode)", level 1.
9. **Follow the output.** The output isn't read line by line as it arrives, and focus doesn't move. The
   run's status sits next to the heading ("Running"). When the run ends you hear its new status once:
   "Succeeded", or for example "Failed · exit 2". The switches say "Follow the output, checkbox, checked"
   and "Wrap long lines, checkbox". Tab to the output: "The lines of the output, region"; read it with
   the arrow keys (NVDA) or VO+Right arrow, and check that the play recap is there. **Download the log**
   is a link.
10. **Switch the theme and the palette.** In the sidebar, the group "Theme": "Dark, radio button,
    selected, 1 of 3". The arrow keys move to "Light" and "System"; the page changes at once, and the
    newly selected radio button is announced. (If you hear "The theme applies to this page only: it
    couldn't be saved.", report it.) The UI has one accent palette, Ember, so there is nothing to choose
    yet; when a palette picker is added, this step gets a line for it.
11. **The phone layout** (optional). Make the window narrow or zoom to 400%: the navigation is behind
    "Menu, button, collapsed". Press it: "expanded", and Tab goes through the navigation. Escape:
    "collapsed", with focus back on the Menu button.
12. **Sign out and in.** Choose **Sign out**: the sign-in page, its heading "Sign in", and focus in
    "Password, secure edit text". A wrong password: "That password is wrong.", read at once. The right
    one: the overview, with its heading read.

### Results

Add a row for each screen reader in the release pull request, with the problems found (each as an issue,
linked here). A step passes when you heard what it describes, in words close to it.

| Release | Date | Screen reader | Browser | Operating system | Result | Problems |
| --- | --- | --- | --- | --- | --- | --- |
| (none yet) | | VoiceOver | Safari | macOS | not run | |
| (none yet) | | NVDA | Firefox or Chrome | Windows | not run | |

## Known gaps

- **One browser engine in CI.** The browser tests run in Chromium only; Firefox and Safari are checked
  by hand, if at all. Native controls (the dropdowns, radio buttons and checkboxes) behave as each browser
  makes them.
- **The screen-reader walk-through hasn't been run yet.** The steps are [below](#screen-reader-walk-through),
  and running them is part of the [pre-release checklist](release-checklist.md#web-ui), but no release has
  recorded a result yet. Automated tests check the structure screen readers rely on (names, roles, live
  regions, focus), not what a screen reader actually says. Reports from people who use NVDA, JAWS or
  VoiceOver are very welcome.
- **It needs JavaScript.** Without it, each page says so and nothing else works.
- **Run output is Ansible's text**, with long lines and many symbols; it reads poorly with a screen reader.
  Downloading the log and reading it in an editor helps.
- **English only.** The UI isn't translated, and its text isn't yet extracted for translation.
