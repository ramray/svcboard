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

LOAD:   Вкладка «Нагрузка» (третья, после «Тесты»): те же карточки нод и
        сервисов, что на Доске, плюс метрики с каждой ноды:
        - Load 1/5/15 + число ядер;
        - CPU% — из дельты двух снимков /proc/stat (sleep 1 внутри команды);
        - RAM used/total в ГБ и % (из /proc/meminfo, fallback на free -m).
        Цвет бара по проценту: <70 зелёный, <90 жёлтый, иначе красный.
        API: GET /api/load -> {ts, tunnel, nodes:[...,{load:{...}}]}.
        Опрос отдельный от /api/status: история сюда НЕ пишется (её ведёт
        get_status), чтобы дубли не портили uptime. Кнопки RS/GO/SP/FR на
        карточках работают как на Доске. Роль реплики [P]/[S] берётся из
        последнего /api/status — mongosh лишний раз не дёргается.
        Обновление: раз в 15 с, только пока вкладка открыта (уход = стоп).
        Dev-проверки: python check-js.py svcboard.py && node --check <файл>
        и node svcboard-smoke.cjs svcboard.py (покрывает и вкладку «Нагрузка»).

STAND:   Нагрузочный стенд (YCSB) — вкладка «Нагрузка», верх = карточки хостов,
         низ = блок запуска YCSB.
         Источник правды: load-stands\INSTRUCTION-NEW-SESSION.md и
         load-stands\REPORT-inventory.md (оттуда дословно команды YCSB).
         Карточки = РЕАЛЬНЫЕ хосты стенда (LS_HOSTS в коде), не флот:
           na-61 10.228.147.61  mongod (rs0, target direct)
           na-62 10.228.147.62  visordb-proxy, mongod, beholder (target proxy)
           na-51 10.228.147.51  mongod, visordb-proxy, beholder + YCSB/Web UI
           na-63 10.228.147.63  карточка-заглушка: выключен (VMware)
         В каждой карточке: IP и роль хоста, юниты (systemctl is-active),
         метрики (load 1/5/15, CPU%, RAM), роль реплики [P]/[S],
         кнопки RS/GO/SP — как на Доске (по прямому указанию пользователя
         07.10, отклонение от «только чтение» брифа load-stands).
         Кнопки идут через общий do_action(): свой туннель стенда, тот же
         confirm() (клик = явное согласие), после успеха сбрасывается кэш
         карточек (_ls_cards_cache). Пока идёт прогон YCSB — действия на
         стенде блокируются сервером («идёт прогон YCSB — … заблокированы»).
         Опрос карточек кэшируется 20 с (LS_CARDS_TTL).
         na-51, na-61 и na-62 (флаг extra в LS_HOSTS): общее место и занятость
         дисков (df -PT, до 3 томов), их I/O (r/w ops, МБ/с, %util — дельта
         /proc/diskstats за 1 с, т.к. пакета sysstat/iostat на стенде нет)
         и кэш WiredTiger монги (serverStatus, если mongod активен) —
         по просьбе пользователя 07.10 (таймауты проб малы, висящий хост
         не роняет карточку); na-51 добавлен той же датой (метрика загрузки
          диска — видна во время прогонов YCSB). Выравнивание строк
          метрик (просьба 07.10): название в колонке 74px без переноса
          (ellipsis), бар и значение на одной вертикали, между строками
          разделитель 1px solid, между секциями — пунктир (dashed).
          Карточки вкладки шире:
          #lgrid minmax(360px,1fr)
         (+20% к 300px; на узких экранах по-прежнему 300px).
         Шапка «Mongo primary: na-22 [P]» на этой вкладке скрыта
         (setTab: primsum display=none) — там про флот, а не про стенд.
         Строка статуса стенда (.lsbox) — display:block + nowrap:
         наследование flex от .tbox рвало её построчно.
         Туннели: 2351->.51, 2352->.61, 2353->.62 через lab; поднимаются по
         требованию одним ssh (только недостающие порты), в DIRECT не нужны.
         API: GET /api/ls -> {status, run, out, metrics}; POST /api/ls
         {action:'load'|'run', target:'direct'|'proxy', workload, threads,
         records} либо {action:'stop'}.
         Запуск: YCSB в foreground через SSH-канал, вывод построчно в
         ЛОКАЛЬНЫЙ ssh-lab\ycsb-load.out (виден и после обрыва туннеля;
         схема как у tests-runner.out/dr-easy.out). Таймаут 20 минут, по
         нему же состояние -> done/error; summary берётся из [OVERALL]
         RunTime/Throughput. Остановка: pkill 'bin/ycsb [l]oad|bin/ycsb [r]un'
         (скобки [l]/[r] — чтобы pkill не убил собственную оболочку).
         Статус (read-only, кэш 20 с): bin/ycsb, цели direct/proxy по nc,
         Web UI :8080, ping .63, идущие процессы ycsb.
         Белый список workload: workloada…f, tsworkloada; URI строится из
         creds.json (mongo_user/mongo_pass), в логи не пишется.
         ВАЖНО: обе кнопки требуют confirm() — клик есть явное согласие
         на нагрузку и запись в базу ycsb (правило брифа load-stands).
         Взаимоблокировка с автотестами/DR не ставилась: это другие хосты
         (.61/.62 vs .22/.23/.24/.28), ресурсы не пересекаются.

START:   start-svcboard.ps1     (double-click; no window appears; browser opens itself)
STOP:    stop-svcboard.ps1
STATUS:  start again while it runs = harmless (single-instance guard by port 49480).

LOGS:    svcboard.log          - app output (append, since every start)
         %TEMP%\svcboard-start.log - launcher events (relaunch/guard/stop)

PORTS:   49480  HTTP (127.0.0.1)
         2333-2336  SSH tunnel to 10.228.147.22/23/24/57 via host 'lab'
                     (separate from the slash-command tunnel 2222-2225, no clash;
                      a stale tunnel on 2333-2336 is killed automatically on start)
         2351-2353  SSH tunnels to the load stand 10.228.147.51/61/62 via
               'lab' (YCSB; own tunnels, spawned on demand by /api/ls, killed
                by stop-svcboard.ps1; not touched by the status tunnel)

CREDS:   All secrets (SSH, Web UI, Mongo) live in creds.json next to svcboard.py -
         gitignored, one copy per machine (template: creds.example.json).
         Web UI: Basic auth from the LAN/internet; 127.0.0.1 skips it.
          Modern Chromium (Chrome/Edge/Brave) shows its native basic-auth
          dialog hidden (or never shows it), leaving the tab blocked/white,
          so the server answers 401 WITHOUT WWW-Authenticate: the browser
          just renders an HTML login form in the body -> user/pass ->
          POST /login -> Set-Cookie (svcboard=..., 7 days) -> the board
          opens; basic auth is still accepted for curl/scripts (curl -u),
          phone simply uses the same form. Wrong password -> the form
          again with an error note.
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
                 Git safety: C:\Users\roman\svcboard and the bare
                 C:\Users\roman\svcboard.git are owned by
                 BUILTIN\Administrators, so the laptop user needs
                 `git config --global --add safe.directory <path>` for both
                 (set 2026-10-05; user.name/email of the corporate git
                 untouched). Git matches the entry literally against the
                 remote path - register the bare in the SAME slash form as
                 the remote URL (the laptop has both C:/... and C:\...;
                 remote origin uses the C:/ form). Without all this pull
                 fails with "dubious ownership" in task/logon context -
                 watch exit= in %TEMP%\svcboard-pull.log.
         Release: on the PC - edit, commit, `git push origin main`, then
                  `git push lab main`; the laptop picks it up at next logon
                  (or trigger the task above).
         Laptop pre-migration copy: C:\Users\roman\svcboard.bak-20261005.

FILES:   svcboard.py            - the app (Python 3 + paramiko, stdlib HTTP)
         run patterns: same engines as the /status and /restart slash commands.
