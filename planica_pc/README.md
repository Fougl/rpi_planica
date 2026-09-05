# Planica PC — getting into the Pi

Notes for the on-site Windows PC at Planica (`DESKTOP-6E17C9J`, wired,
`192.168.1.101`). The Pi is `pitest`, reserved at **192.168.1.119**.

Everything below is set up **on the Windows PC**, not on the Pi. If your prompt
says `pi@pitest:~ $` you are in the wrong place — type `exit` first.

## One-time: log in without a password

The key baked into the SD card at flash time belongs to the *home* desktop, so this
PC gets a password prompt every time. Give this PC its own key. Never copy a private
key between machines and never put one in OneDrive — one key per machine, and each
can be revoked on its own by deleting its line from `authorized_keys`.

In **cmd** (not PowerShell — PowerShell writes UTF-16 with a BOM into the pipe and
the key lands mangled, failing silently):

```
ssh-keygen -t ed25519 -C "planica-pc"
```

Press Enter three times: default path, empty passphrase, confirm. A passphrase would
just move the prompt rather than remove it. If it says the file exists, answer `n`
and keep the key you already have.

Then, still in cmd — this is the last time you type the Pi's password:

```
type %USERPROFILE%\.ssh\id_ed25519.pub | ssh pi@192.168.1.119 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

If this PC is ever reinstalled, just make a new key and repeat — keys are disposable.
Afterwards prune the stale line from `~/.ssh/authorized_keys` on the Pi; the
`-C "planica-pc"` comment is what lets you tell the lines apart.

**Do not remove the password fallback on the Pi**
(`/etc/ssh/sshd_config.d/10-password-backdoor.conf`, `PasswordAuthentication yes`).
That is the recovery path for a machine with no key. Lose the key *and* the password
and the only way back in is a keyboard and monitor at Planica, or reflashing the card.

## One-time: the `enter rpi` shortcut

Windows only runs commands it can find in a fixed list of folders. So: put a small
command file in a folder, then add that folder to the list.

In **PowerShell**, one line at a time:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\bin"
```

```powershell
'@if /i "%~1"=="rpi" (ssh pi@192.168.1.119) else (echo Usage: enter rpi)' | Out-File -Encoding ascii "$env:USERPROFILE\bin\enter.cmd"
```

```powershell
[Environment]::SetEnvironmentVariable("Path",[Environment]::GetEnvironmentVariable("Path","User")+";$env:USERPROFILE\bin","User")
```

`-Encoding ascii` matters: PowerShell's default UTF-16-with-BOM makes both `ssh` and
`cmd` choke on the first line. And use that `SetEnvironmentVariable` line rather than
`setx PATH "%PATH%;..."` — setx merges the system PATH into the user PATH and
truncates at 1024 characters, which quietly breaks unrelated things.

**Close PowerShell and open a new cmd window** — windows that are already open keep
the old folder list. Then:

```
enter rpi
```

`'enter' is not recognized` means either the PATH step did not take or the window
predates it. Check with `echo %PATH%` for `...\bin` at the end.

To change the address later, edit `%USERPROFILE%\bin\enter.cmd`.

## The router trap — read this before blaming a device

The MR600 has two features that both claim IP addresses and **do not check each
other**:

| Page | What it does |
|---|---|
| **DHCP Server → Address Reservation** | **Assigns** the IP. "Always give this MAC this address." |
| **Binding List / ARP List** | **Filters** traffic. "Only this MAC may use this address." Assigns nothing. |

Binding an address does **not** remove it from the DHCP pool (`.100`-`.199`). So DHCP
can lease a bound address to a different device, and the binding filter then drops
that device's routed traffic. The result looks impossible: the device is associated
to the WiFi, appears in the client list, holds a valid lease, answers a ping from
this PC — and cannot reach the internet at all.

Confirmed 2026-09-05: `.100` was bound to `14-4F-8A-57-E3-56` while DHCP had leased
`.100` to the Tapo plug `3C-6A-D2-79-FC-B9`. The plug read "offline" in the Tapo app
for a long time because the app reaches it through TP-Link's cloud, and the route out
was blocked. Local pings still worked, which is why it looked fine from here.
Disabling that one binding fixed it instantly.

**Use Address Reservation only. Leave the Binding List empty.** Bindings buy nothing
on a private router with four devices, and a binding without a matching reservation
is a mine.

Still armed as of 2026-09-05, all Raspberry Pi MACs (`B8:27:EB`, `E4:5F:01`) from
retired Pis — anything that draws one of these addresses goes dark:

```
B8-27-EB-76-9E-A9 -> .102     E4-5F-01-BD-34-D7 -> .110
B8-27-EB-23-CB-FC -> .104     B8-27-EB-E9-5D-60 -> .117
B8-27-EB-4C-62-01 -> .106
```

Page 2 of the Binding List has never been read. Check it before reserving any new
address.

## If the Pi will not answer

1. `ping -n 2 192.168.1.119` — no reply means it is not reachable at that address.
2. `for /L %i in (1,1,254) do @ping -n 1 -w 100 192.168.1.%i >nul` then
   `arp -a | findstr /i "d8-3a-dd"` — finds it wherever it actually landed.
3. Router → **Wireless → Statistics** — if its MAC is listed it is on the WiFi, so
   the problem is above layer 2. Check the Binding List next.
4. Ping works but SSH hangs, or it is on the WiFi but unreachable: that is the
   binding trap. See above.

The Pi's plug can be power-cycled from the Tapo app as a last resort. `py_new` also
restarts itself now if the BLE scan wedges, so a hung scanner no longer needs one.
