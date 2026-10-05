svcboard - status lights + restart buttons for fleet services
==============================================================

WHAT:    http://127.0.0.1:49480 (localhost only, not exposed to the network)
         na-22/23/24: mongod, sbm-agent, beholder
         na-57:       visordb-proxy
         Green = active, yellow = activating, red = inactive/failed, gray = unknown.
         Replset roles (notation matches `sbm status`): the node card shows [P] on
         the current PRIMARY and [S] on secondaries, header shows "Mongo primary: ...".
         Source: read-only rs.status executed locally on each node (only while mongod
         is active) - independent of sbm/na-28; unknown = no badge.
         Buttons: RS = restart, GO = start, SP = stop (hover = Russian name,
         SP outlined in red). Every click -> confirm dialog (the click IS the
         explicit consent), then pre -> post and elapsed time.
         When sbm-agent shows "failed", that row gets an extra mini
         button FR = failed-reset (systemctl reset-failed): clears the failed
         flag (pre=failed -> post=inactive), touches nothing else, same
         confirm flow; rendered only for sbm-agent and only while failed.

HISTORY: "История статусов" section at the bottom of the page:
         - uptime chips per service, computed over the whole observation
           window (first sight = baseline; transitions = history events);
         - last 15 state changes (time, node/unit, from -> to);
         - stored in svcboard\history.json (atomic write, survives restarts,
           cap 50000 events), recorded by a background poller every 60 s -
           works even with the browser closed; an unreachable node is NOT
           treated as "services failed" (no observations, no events);
         - self-test of the engine: python history-selftest.py (isolated,
           uses a temp file, no real data touched).

TESTS:   the "Тесты" tab (next to "Доска") hosts the runner:
         "Автотесты dev-02" button runs the proven runbook:
         tunnel 127.0.0.1:2222 -> ssh-lab\run_tests.py (cd, sudo, git pull,
         pytest) -> verbatim block (4 __SUMMARY_* lines + pytest result)
         appears under the button when done. ~8 minutes, async - state and a
         live timer show up next to the button; pytest_rc: 0 = green,
         1 = orange (known single env fail), other = red.
         While it runs do NOT invoke the /tests or /status slash commands -
         by convention they kill stale 2222-2225 tunnels and would collide.
         Runner stdout copy (debug, overwritten each run):
         ssh-lab\tests-runner.out

RESULTS: same "Тесты" tab - "Результаты прогонов" list under the button:
         - every finished run is appended to svcboard\tests-history.json
           (cap 50, survives restarts): time, rc/pytest_rc, the 4 summary
           lines, tail line and each FAILED/ERROR test with its error head;
         - newest first, click a row to expand details;
         - red badge with a number on the tab = failed tests in the latest
           run (no badge = green); /api/status carries hist_n/hist_fail;
         - GET /api/tests_hist -> {runs:[...]}, the whole list;
         - tests-history-backfill.py - one-off importer of runbook-log.txt
           into that file (history starts empty, import past runs manually).

DR:      "DR-сценарий" buttons on the same "Тесты" tab (under the runner)
         run ssh-lab\dr_easy.py:
         - fresh  - stop, wipe /var/lib/mongodb, rs.initiate, users, rs.add,
                    start services -> an EMPTY new cluster (no restore);
         - restore - the same + sbm config/resync, sbm restore <backup>,
                    config/resync again, services, admin email, proxy restart.
         Backup name for restore sits in the input field (default = drill base
         backup 2026-10-03T22:19:33Z). Every click -> confirm dialog (the click
         IS the explicit consent to destroy data), then a live timer and the
         verbatim dr_easy output block (last 200 lines, refresh every 3 s).
         Own tunnels 127.0.0.1:2242-2245 (+2248 for restore) -> node:22 via
         'lab'; already-listening ports are reused, spawned ones are killed
         when the run ends. Guard: refuses while tests run (and vice versa).
         API: POST /api/dr {mode, backup}; GET /api/status -> payload.dr;
         GET /api/drout - verbatim tail. Output copy: ssh-lab\dr-easy.out.
         Refuses invalid mode/backup name without starting anything.

START:   start-svcboard.ps1     (double-click; no window appears; browser opens itself)
STOP:    stop-svcboard.ps1
STATUS:  start again while it runs = harmless (single-instance guard by port 49480).

LOGS:    svcboard.log          - app output (append, since every start)
         %TEMP%\svcboard-start.log - launcher events (relaunch/guard/stop)

PORTS:   49480  HTTP (127.0.0.1)
         2333-2336  SSH tunnel to 10.228.147.22/23/24/57 via host 'lab'
                     (separate from the slash-command tunnel 2222-2225, no clash;
                      a stale tunnel on 2333-2336 is killed automatically on start)

CREDS:   All secrets (SSH, Web UI, Mongo) live in creds.json next to svcboard.py -
         gitignored, one copy per machine (template: creds.example.json).
         Web UI: Basic auth from the LAN/internet; 127.0.0.1 skips it.
         SSH: one auth attempt per node (lockout protection).

UPDATES: repo https://github.com/ramray/svcboard (private), branch main.
         PC:     Startup\vbs -> watchdog.ps1 -> git pull origin/main -> start (hidden).
         Laptop: PC pushes to the laptop bare repo (remote 'lab' =
                 lab:C:/Users/roman/svcboard.git - scp-style form; the ssh://
                 form does not resolve against the Windows sshd), then the
                 laptop pulls that local bare - it never talks to github
                 (corporate policy, ssh key via the PC only).
                 Logon: Startup\vbs -> watchdog-laptop.ps1 -> git pull of the
                 local bare -> start svcboard.py --direct (hidden).
                 Remote/manual restart (no logon): scheduled task
                 'svcboard-manual' runs the same watchdog -
                 Start-ScheduledTask svcboard-manual. Needed when starting
                 over ssh: children of an ssh session are killed with it.
         Release: on the PC - edit, commit, `git push origin main`, then
                  `git push lab main`; the laptop picks it up at next logon
                  (or trigger the task above).
         Laptop pre-migration copy: C:\Users\roman\svcboard.bak-20261005.

FILES:   svcboard.py            - the app (Python 3 + paramiko, stdlib HTTP)
         run patterns: same engines as the /status and /restart slash commands.
