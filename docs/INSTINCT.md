# Instinct as a worker

Skye can hand a job to an Instinct agent through a git branch. No server and no model key on the
Skye side.

Set `SKYE_INSTINCT_REPO` to a git URL or path you can push to, and pick the `instinct` adapter.
Optional: `SKYE_INSTINCT_BRANCH` (default `skye-jobs`), `SKYE_INSTINCT_POLL_SECONDS` (default 30).

1. Skye pushes `jobs/<id>/job.json` (goal, role, the text files in the workspace) to the branch.
2. Instinct reads the pending jobs (`python -m skye.instinct_queue pending REPO`), does the work,
   and pushes `jobs/<id>/result.json` with `{"status", "reply", "files"}`
   (`python -m skye.instinct_queue answer REPO JOB_ID ANSWER.json`).
3. Skye polls, writes the returned files into the workspace (paths outside it are refused) and
   settles the step like any other worker.

Skye's own approval gate still applies: the first run of the `instinct` adapter for a role waits
for the owner's approval. Answers take as long as it takes Instinct to wake and read the branch.
Anyone who can push to the branch can answer a job, so keep the repo private.
