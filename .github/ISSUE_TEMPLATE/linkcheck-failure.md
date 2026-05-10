---
title: "docs: weekly linkcheck failed ({{ date | date('YYYY-MM-DD') }})"
labels: [docs, linkcheck]
---
The scheduled `docs-linkcheck` workflow detected broken external links in
the Sphinx documentation.

- **Run:** [#{{ env.GITHUB_RUN_NUMBER }}]({{ env.GITHUB_SERVER_URL }}/{{ env.GITHUB_REPOSITORY }}/actions/runs/{{ env.GITHUB_RUN_ID }})
- **Trigger:** `{{ env.GITHUB_EVENT_NAME }}`
- **Commit:** `{{ env.GITHUB_SHA }}`

Inspect the run's `linkcheck-output` artifact (or the job log) for the
specific URLs and HTTP statuses, then either:

1. Fix or replace the broken link.
2. If the link is intermittently flaky, add it to `linkcheck_ignore` in
   `docs/conf.py` with a short comment explaining why.

This issue auto-closes once a subsequent linkcheck run succeeds.
