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

- **Now** covers activity in the last 24 hours. Its header shows how old the
  data is and provides a **Refetch** control. Six counters summarize **Active
  projects**, **Plans / Queues**, **Waves**, **Tasks** (with a per-status
  breakdown), **Recons**, and **Commits** over that window.

  The **In flight** tree orders projects, then plans, waves, and tasks by last
  activity. Completed waves are summarised as counts, and tasks outside a plan
  appear in their own group. A project is described as **working**, **reporting
  but not progressing**, or **no recent signal**; these are liveness summaries,
  not task or plan states. Where the local files cannot support a figure, it is
  shown as unavailable.

  Select a task row to open its detail panel on the right. The panel shows the
  task id, status, plan, wave, job, and elapsed time, followed by the task's
  ticket spec rendered from Markdown. Close it with the panel's close button or
  press **Escape**. The selected task stays open when the page refreshes. If
  there is no spec for the task on this machine, the panel says so. The built-in
  Markdown renderer is intentionally small: it creates DOM nodes only, and
  displays unsupported Markdown syntax as plain text.

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

Use `--port` to choose the web server port; by default, the OS chooses a free
port outside the reserved `snodo serve` range 55441-55460. The terminal
dashboard remains available with the `--cli` option on `snodo dashboard`.
