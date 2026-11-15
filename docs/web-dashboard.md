# Web dashboard

Start the web dashboard from a terminal:

```sh
snodo dashboard --web
```

To scan projects under a folder, pass it as a root:

```sh
snodo dashboard --web --root ~/Dev
```

The dashboard listens on `127.0.0.1`, so it is available on this machine. It is
read-only and does not write to projects. Projects are found by scanning the
root folders for a `.snodo/` directory. The selected roots are remembered in
`web.yml` in your Snodo folder.

The dashboard has four views:

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

  Select a task row to open its detail panel on the right. Its header identifies
  the task and provides close and copy actions; the facts include status, plan,
  wave, job, and elapsed time. The panel also shows the task's ticket spec
  rendered from Markdown. Close it with the close button or press **Escape**.
  The selected task stays open when the page refreshes. If there is no spec for
  the task on this machine, the panel says so. The built-in Markdown renderer is
  intentionally small: it creates DOM nodes only, and displays unsupported
  Markdown syntax as plain text.

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
- **Delivery** shows landed tasks, typical time, first-try rate, and flow load.
- **Gates** shows what validators sent back and whether the work landed.
- **Plans** shows each plan, its progress, and whether it reached `main`.

The read-only `GET /api/now/spec` route takes the query values `project`,
`plan`, and `task`. It returns JSON with `project`, `plan`, `task`, and
`markdown` keys. A missing value returns **400**; an unknown project, plan or
task, or a name that is not a plain name, returns **404**. The project is
selected by name from the dashboard's own project list, so the browser never
supplies a filesystem path.

Figures come from files Snodo already writes in each project's `.snodo/`
folder and from Git. When the records cannot support a figure, it appears as
“n/a”. The dashboard does not add new project records.

Use `--port` to choose the web server port; by default, the dashboard
uses the first free port from 44331 upward, so the address stays predictable. The terminal
dashboard remains available with the `--cli` option on `snodo dashboard`.
