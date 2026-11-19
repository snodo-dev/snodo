# Web dashboard

Start the web dashboard from a terminal:

```sh
snodo dashboard --web
```

To scan projects under a folder, pass it as a root:

```sh
snodo dashboard --web --root ~/Dev
```

The dashboard listens on `127.0.0.1`, so it is available on this machine. It
writes only the `.snodo/protocol.yml` file, from the Protocol view, after an
explicit save. Other views remain read-only. Projects are found by scanning the
root folders for a `.snodo/` directory. The selected roots are remembered in
`web.yml` in your Snodo folder.

The dashboard has five views:

- **Now** covers activity in the last 24 hours. Its page head shows how old the
  data is and provides a **Refetch** control. Six counter cards summarize
  **Active projects**, **Plans / Queues**, **Waves**, **Tasks** (with a per-status
  breakdown), **Recons**, and **Commits** over that window.

  An attention strip appears only when a task is **failed**, **blocked**, or
  **errored**. Its **Show task** button opens that task in the detail panel.
  These status words, and every other status shown, are the words carried by the
  project's own records; the dashboard adds none.

  The **In flight** fleet card orders projects by recent activity. Project
  headers summarize liveness; each has plan rows with a segmented progress bar,
  one segment per task, followed by waves and compact one-line task rows. Tasks
  outside a plan appear in their own group. Completed waves are summarized as
  counts. A project is described as **working**, **reporting but not
  progressing**, or **no recent signal**; these are liveness summaries, not task
  or plan states. Where local files cannot support a figure, it is shown as
  unavailable.

  The task tree uses the full width until a task is selected. Select a task row
  to open its detail panel on the right; the tree narrows to make room. The
  panel header identifies the task and provides close and copy actions; the
  facts include status, plan, wave, job, and elapsed time. Close the panel with
  its close button or press **Escape** to return the tree to full width. The
  selected task stays open when the page refreshes.

  The panel shows the task's ticket spec rendered from Markdown. While the spec
  loads, a progress bar estimates the wait: its first expected duration is ten
  seconds, then it follows the average duration of the last ten loads, kept in
  the browser's local storage. If there is no spec for the task on this machine,
  the panel says so. The built-in Markdown renderer is intentionally small: bare
  lines made up of capital letters are section headings, line breaks are kept,
  and underscores inside identifiers are left alone. Unsupported Markdown
  syntax is displayed as plain text; the renderer creates DOM nodes only.

  The theme follows the system preference, with dark as the default; the
  **Theme** button overrides it. The shipped page uses local font stacks and
  loads no fonts or other files from external hosts. The visual reference,
  [`docs/design/now-redesign.html`](design/now-redesign.html), is kept in the
  repository and uses sample data. Its webfont links and illustrative styling
  are not shipped page behavior; use the local stacks and status vocabulary
  documented here rather than copying those details.

  The local page deliberately differs from the cloud Now page: it sees only
  this machine, has no project groups, and keeps a **Waiting on you** list for
  plans not yet merged into `main` and uncommitted files.
- **Protocol** shows the schema state in its head, with **Reload from disk**, six
  summary tiles, the initial-mode pipeline, validator groups with expandable row
  editors, execution settings, and the rules checked on save. You can edit a
  validator's type, phase, severity cap, tools, and criteria. The
  initial-mode pipeline is shown, but changing whether a validator runs there is
  not saved.
  Adding validators is not available. Saving first
  lets you review a diff, then writes `.snodo/protocol.yml`; committing and
  pushing stay with you. Comments and formatting outside edited validators are
  kept. A file changed on disk or an invalid result stops the save with a
  message. The write routes are restricted to loopback Host values; a supplied
  Origin must match the Host, and saves require JSON and the per-process page
  token. Only the known project's protocol file can be written. The visual
  reference, [`docs/design/protocol-editor.html`](design/protocol-editor.html),
  uses web fonts and sample data that the shipped page does not.
- **Delivery** reports flow over 7, 30, or 90 days. Its five cards show landed
  tasks, typical cycle time, first-try rate, flow load, and flow efficiency.
  Three blocks below the cards show weekly landed-task counts, flow-time ranges
  by work type, and a by-type detail table. Date labels are sampled for longer
  windows so they remain readable. The response's `by_type`, `by_week`, and
  `efficiency` fields supply the corresponding type breakdown, weekly series,
  and efficiency measure.

  Work type comes from `task_classified` audit events. The dashboard indexes
  those events once and caches the index outside the project, in the user's
  cache directory. Flow efficiency is the share of start-to-landed time not
  spent after the first send-back. Tasks with unknown send-back history are
  excluded; a task that lands on its first try counts as fully efficient. This
  is a rework measure, not the Flow Framework's active-time-over-total-time
  measure.
- **Gates** aggregates validator send-backs and retry outcomes across projects for a
  selectable 7-, 21-, or 30-day window (21 days by default). Its summary reports
  tasks sent back and those that landed after a retry. **What the validators
  caught** bars compare each validator's send-back count with the highest count
  in the list; a missing validator ID is shown as **unknown**. **Per project**
  bars show the share of sent-back tasks that landed after retry, with the
  landed / sent-back counts alongside. The **The record** table lists up to 100
  newest gate records with task, project, stopping validator, outcome, and
  relative time. **Landed after a retry** means the task's recorded plan status
  is completed; **Not landed yet** means it is not recorded as completed. When
  blocked halts in task state or archived attempts identify send-backs, whether
  or not a fix task record exists. When attempts do not contain a timestamp or stopping validator, those values
  are unavailable (the validator displays as **unknown**); records without a
  usable attempt timestamp cannot be included. With no records in the window,
  the page says so.
- **Plans** lists plans across projects in a table with plan name, project, task
  completion count, progress bar, delivery status, and last-updated date. The
  plan name's hover title shows its intent when recorded. When there is more
  than one project, the **Project** filter switches between all projects and an
  individual project. The progress bar tracks completed tasks out of the plan's
  recorded task total, also shown as a fraction. Delivery pills read **running**
  when any task is in progress, **in main** when the plan's integration branch
  is in `main`, **not in main** when it is not, or **no branch on record** when
  no delivery status is available. Task counts use the plan's recorded statuses;
  a missing status file modification time appears as **n/a** in Updated. If the
  plan has no recorded task statuses, the task fraction is 0 / 0 and its bar is
  empty.

The dashboard header identifies the project by name and Git remote. When the
project id is itself the remote, that identity is shown only once. The read-only
`GET /api/identity` route provides this header identity.

The read-only `GET /api/now/spec` route takes the query values `project`,
`plan`, and `task`. It returns JSON with `project`, `plan`, `task`, and
`markdown` keys. A missing value returns **400**; an unknown project, plan or
task, or a name that is not a plain name, returns **404**. The project is
selected by name from the dashboard's own project list, so the browser never
supplies a filesystem path. Every route runs its blocking work in a worker
thread, so one slow answer does not hold up other requests.

Figures come from files Snodo already writes in each project's `.snodo/`
folder and from Git. When the records cannot support a figure, it appears as
“n/a”. The dashboard does not add new project records.

Use `--port` to choose the web server port; by default, the dashboard
uses the first free port from 44331 upward, so the address stays predictable. The terminal
dashboard remains available with the `--cli` option on `snodo dashboard`.
