# PR review fix-forward isolation

For non-merged reviews, writing the synthesized review comment is the required
result; preparing a fix-forward issue body is a best-effort follow-up. The body
must be built only after the comment output is written, and failures while
building or writing it are reported to stderr without changing the script's
successful exit for the comment. Reuse the pull-request title already fetched
for the review rather than making a second GitHub request. The workflow posts
the comment before its separate fix-forward issue step, which remains
`continue-on-error` so issue failures cannot undo comment publication.
