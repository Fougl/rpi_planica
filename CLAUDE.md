# Rules for working in this repo

**Every push here deploys to a live Pi within 2 minutes.** `deploy.sh` in this
repo runs from cron every 2 minutes: it fetches `master`, `git reset --hard`s to
it, installs `py_new.service` into `/etc/systemd/system` when the repo copy
differs, and restarts the service. There is no staging and no review step. A bad
push reaches the cameras at Planica immediately.

Until 2026-09-13, cron ran a generated `/home/pi/deploy.sh` that used `git pull`
and never installed the unit file. Both of those cost real outages — see below
and `dongle_deafness.md`. The old script is parked as
`/home/pi/deploy.sh.superseded`.

## The dongle goes deaf — start here

`dongle_deafness.md` is the standing record for the scanning radio losing its
hearing: what is proven, what is not, the external reports on this exact dongle,
and where the next episode writes its own evidence (`/home/pi/diag/`). **The root
cause is still open.** Read it before theorising, and before changing anything
about scanning, adapter resets, or the unit file.

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

Since 2026-09-13 the deploy uses `git fetch` + `git reset --hard`, which no
untracked collision or local edit can block the way `git pull` could. This should
not happen again. Kept because it did, for five days:

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

## Never run a scan on the GATT radio

`hci0` (onboard, cloned address) exists to make outbound connections to the
cameras. Do not run `hcitool lescan`, `bluetoothctl scan on`, or anything else
that scans on it. `hcitool lescan` enables scanning with a raw HCI command
behind bluetoothd's back; Ctrl-C leaves it enabled; a scanning controller
refuses outbound connections; every camera write then fails with
`Connection refused`, and nothing at the BlueZ level shows why. That is exactly
what happened on 2026-09-12 while diagnosing the dongle -- and it is the same
latch that left the dongle itself deaf for six days.

To see what a radio hears, observe it: `sudo timeout 20 btmon -i hci0`.
btmon only listens. If hci0 is ever latched anyway:
`sudo hciconfig hci0 down && sudo hciconfig hci0 up`, then confirm
`hciconfig hci0 | grep "BD Address"` still shows `B8:27:EB:91:8B:A6`.
