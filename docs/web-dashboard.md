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

- **Now** shows work running across projects and items waiting for you.
- **Delivery** shows landed tasks, typical time, first-try rate, and flow load.
- **Gates** shows what validators sent back and whether the work landed.
- **Plans** shows each plan, its progress, and whether it reached `main`.

Figures come from files Snodo already writes in each project's `.snodo/`
folder and from Git. When the records cannot support a figure, it appears as
“n/a”. The dashboard does not add new project records.

Use `--port` to choose the web server port. The terminal dashboard remains
available with the `--cli` option on `snodo dashboard`.
