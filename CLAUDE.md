# Rules for working in this repo

**Every push here deploys to a live Pi within 2 minutes.** `/home/pi/deploy.sh`
runs from cron every 2 minutes, pulls `master` and restarts `py_new.service`.
There is no staging and no review step. A bad push reaches the cameras at
Planica immediately, and a Pi that cannot pull cannot be fixed by pushing again.

## Never bulk-commit untracked files here

Do not run `git add -A`, `git add .`, or any "commit everything so nothing is
lost" sweep in this repo. **Add files by name.**

On 2026-09-07 17:43 a machine-wide checkpoint sweep — run across four repos in
three minutes, before a Windows reinstall — committed `__pycache__/*.pyc` and
`install_gatttool.sh`. Those files already existed, untracked, on every Pi:
Python writes the cache the first time `py_new.py` runs. Git will not overwrite
an untracked file it has no copy of, so `git pull` aborted, `HEAD` never moved,
and `deploy.sh` — which never checked whether the pull worked — restarted the
service every 2 minutes for five days.

Nothing reported it. systemd said `active (running)`, `deploy.log` wrote
`Deployed <same sha>` at every attempt as though it had succeeded, and between
restarts the service log showed healthy scans. The cameras stopped firing
entirely: scanner state lives in memory, and the 5-minute absence that re-arms a
camera cannot elapse inside a process that dies every 2 minutes. Fixed in
`1fb9a4b`.

Anything generated on the Pi — `__pycache__/`, `*.pyc`, `*.log`, `.env`,
`last_strong.json` — belongs in `.gitignore`, never in a commit.

## Recovering a Pi that has stopped updating

A stuck Pi cannot pull the fix; it has to be cleared by hand, on the Pi:

```bash
cd /home/pi/Desktop
git pull --ff-only origin master        # prints the real reason
git stash                               # park local edits to tracked files
mv <blocking-file> <blocking-file>.bak  # move untracked collisions aside
git pull --ff-only origin master
sudo systemctl restart py_new
```

## Checking that a deploy actually landed

```bash
tail /home/pi/deploy.log       # quiet = up to date; PULL FAILED = blocked
cd /home/pi/Desktop && git log --oneline -1
```

`deploy.log` writing a line every 2 minutes means something is wrong, whatever
it says.

## Why `install_gatttool.sh` lives in `tools/`

It was added at the repo root on 2026-09-07, where every Pi already had its own
untracked copy — that name collision is what blocked `git pull` and caused the
outage. Moving it to a path no Pi has lets a stuck Pi fast-forward on its own,
without anyone opening an SSH session. Do not move it back to the root.
