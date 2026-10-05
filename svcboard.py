# -*- coding: utf-8 -*-
"""svcboard - mini app: status lights + restart buttons for fleet services.

Run:
    python svcboard.py             opens browser at http://127.0.0.1:49480
    python svcboard.py --no-open   headless (for tests)

Own SSH tunnel (local ports 2333-2336) through host 'lab'.
Status checks are read-only; restart requires a browser confirm
(the click itself is the user's explicit consent).
"""
import base64
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paramiko

def _load_creds():
    """Secrets from creds.json next to this file (gitignored; template: creds.example.json)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "creds.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit("svcboard: creds.json not found - copy creds.example.json to creds.json and fill it")

CREDS = _load_creds()
HTTP_PORT = 49480
BIND = "0.0.0.0"        # HTTP listen: all interfaces (LAN + port-forwarded internet)
WEB_USER = CREDS["web_user"]
WEB_PASS = CREDS["web_pass"]  # Basic auth for every request except this PC's loopback
SSH_USER = CREDS["ssh_user"]
SSH_PASS = CREDS["ssh_pass"]
MONGO_USER = CREDS["mongo_user"]
MONGO_PASS = CREDS["mongo_pass"]
TUNNEL_HOST = "lab"
# --direct (запуск на ноутбуке lab/roman): без ssh-туннелей через этот
# компьютер - paramiko ходит на ноды напрямую (ip:22); туннели не нужны.
DIRECT = "--direct" in sys.argv
NODES = [
    {"label": "na-22", "ip": "10.228.147.22", "lport": 2333,
     "units": ["mongod", "sbm-agent", "beholder"]},
    {"label": "na-23", "ip": "10.228.147.23", "lport": 2334,
     "units": ["mongod", "sbm-agent", "beholder"]},
    {"label": "na-24", "ip": "10.228.147.24", "lport": 2335,
     "units": ["mongod", "sbm-agent", "beholder"]},
    {"label": "na-57", "ip": "10.228.147.57", "lport": 2336,
     "units": ["visordb-proxy"]},
]
BY_LABEL = {n["label"]: n for n in NODES}
CREATE_NO_WINDOW = 0x08000000

if DIRECT:
    # ноутбук: свой профиль (USERPROFILE\ssh-lab) и свой интерпретатор
    # (тот же, куда поставлен paramiko)
    PYTHON = sys.executable
    SSH_LAB = os.path.join(os.environ.get("USERPROFILE", r"C:\Users\User"),
                           "ssh-lab")
else:
    PYTHON = r"C:\Users\User\miniconda3\python.exe"
    SSH_LAB = r"C:\Users\User\Documents\Default Project\ssh-lab"
RUNNER = os.path.join(SSH_LAB, "run_tests.py")
RUNNER_LOG = os.path.join(SSH_LAB, "runbook-log.txt")
RUNNER_OUT = os.path.join(SSH_LAB, "tests-runner.out")
TEST_LPORT = 2222          # same convention as the /tests slash command
TEST_DEV02 = "10.228.147.28"

tunnel_proc = None
tunnel_lock = threading.Lock()
action_lock = threading.Lock()
tests_ctl = threading.Lock()
TESTS = {"state": "idle", "started": None, "started_ts": None, "ended": None,
         "rc": None, "pytest_rc": None, "summary": [], "tail": "",
         "failed": [], "error": ""}

# DR-сценарии (ssh-lab\dr_easy.py): сборка кластера с кнопки.
# Туннели свои (127.0.0.1:2242-2245[,2248] -> ноды:22 через lab), НЕ трогают
# статус-туннель 2333-2336 и тестовый 2222; уже поднятые порты переиспользуются.
DR_EASY = os.path.join(SSH_LAB, "dr_easy.py")
DR_OUT = os.path.join(SSH_LAB, "dr-easy.out")
DR_PORTS = {2242: "10.228.147.22", 2243: "10.228.147.23",
            2244: "10.228.147.24", 2245: "10.228.147.57",
            2248: "10.228.147.28"}
DR_MODE_PORTS = {"fresh": [2242, 2243, 2244, 2245],
                 "restore": [2242, 2243, 2244, 2245, 2248]}
DR_NAME_RE = re.compile(r"^[A-Za-z0-9:._-]+$")   # дубль safe_name из dr_run
dr_ctl = threading.Lock()
DR = {"state": "idle", "mode": None, "backup": None, "started": None,
      "started_ts": None, "ended": None, "rc": None, "error": "",
      "tunnels": ""}


def log(msg):
    sys.stdout.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stdout.flush()


def port_listening(port):
    import socket
    s = socket.socket()
    s.settimeout(1)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def kill_stale_listeners():
    if DIRECT:
        return                      # туннелей нет - и слушателей не бывает
    busy = [n["lport"] for n in NODES if port_listening(n["lport"])]
    if not busy:
        return
    ports = ",".join(str(p) for p in busy)
    ps = ("(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
          "Where-Object { $_.LocalPort -in @(%s) }).OwningProcess | "
          "ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }") % ports
    subprocess.run(["powershell", "-NoProfile", "-c", ps],
                   capture_output=True, timeout=15)
    time.sleep(0.5)


def ensure_tunnel(force=False):
    global tunnel_proc
    if DIRECT:
        return True                # режим --direct: ноды доступны напрямую
    with tunnel_lock:
        alive = tunnel_proc is not None and tunnel_proc.poll() is None
        if alive and not force:
            return True
        if alive and force:
            try:
                tunnel_proc.kill()
            except Exception:
                pass
            tunnel_proc = None
            time.sleep(0.5)
        kill_stale_listeners()
        args = ["ssh", "-N", "-o", "ExitOnForwardFailure=yes",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
        for n in NODES:
            args += ["-L", "%s:%d:%s:22" % (BIND, n["lport"], n["ip"])]
        args += [TUNNEL_HOST]
        try:
            tunnel_proc = subprocess.Popen(args, creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            log("tunnel spawn FAIL: %r" % e)
            return False
        time.sleep(3)
        ok = tunnel_proc.poll() is None
        log("tunnel %s" % ("up pid=%d" % tunnel_proc.pid if ok else "FAILED"))
        return ok


def ssh_connect(target, timeout=8):
    """target: int = локальный порт туннеля (режим с пробросом),
    str = IP ноды (режим --direct, подключение на :22 напрямую)."""
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    host = target if isinstance(target, str) else "127.0.0.1"
    port = 22 if isinstance(target, str) else target
    cli.connect(host, port=port, username=SSH_USER, password=SSH_PASS,
                look_for_keys=False, allow_agent=False,
                timeout=timeout, banner_timeout=timeout, auth_timeout=timeout)
    return cli


def run_cmd(cli, cmd, timeout=20):
    si, so, se = cli.exec_command(cmd, timeout=timeout)
    out = so.read().decode("utf-8", "replace")
    err = se.read().decode("utf-8", "replace")
    rc = si.channel.recv_exit_status()
    return rc, out, err


def active_state(cli, unit, timeout=10):
    rc, out, err = run_cmd(cli, "systemctl is-active %s" % unit, timeout=timeout)
    out = out.strip()
    return out.splitlines()[-1] if out else "?"


def probe_role(cli, timeout=8):
    """Read-only: replica-set stateStr of this node's mongod (PRIMARY/...).

    Returns None when mongod is down, unreachable or the answer is unknown;
    never raises (a slow/failed probe must not fail the whole node check).
    """
    js = (
        'try{var h=db.hello();var s=rs.status();'
        'var me=s.members.filter(function(m){return m.name===h.me;})[0];'
        'print("ROLE="+(me?me.stateStr:"NO_SELF"));}'
        'catch(e){print("ROLE=ERR:"+e);}')
    try:
        rc, out, err = run_cmd(
            cli,
            "mongosh -u %s -p %s --authenticationDatabase admin"
            " --quiet --eval '%s' 2>/dev/null" % (MONGO_USER, MONGO_PASS, js),
            timeout=timeout)
        for line in reversed(out.strip().splitlines()):
            if line.startswith("ROLE="):
                v = line.partition("=")[2].strip()
                if v and not v.startswith("ERR") and v != "NO_SELF":
                    return v
                return None
    except Exception:
        pass
    return None


def check_node(n):
    res = {"label": n["label"], "ok": False, "error": "", "units": {}}
    try:
        cli = ssh_connect(n["ip"] if DIRECT else n["lport"])
    except Exception as e:
        res["error"] = repr(e)
        return res
    try:
        chk = 'for u in %s; do echo "$u=$(systemctl is-active $u)"; done' % " ".join(n["units"])
        rc, out, err = run_cmd(cli, chk, timeout=15)
        states = {}
        for line in out.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                states[k.strip().replace(".service", "")] = v.strip()
        missing = [u for u in n["units"] if u not in states]
        if missing and not states:
            res["error"] = (err.strip() or out.strip() or "no output")[:300]
        elif missing:
            res["error"] = "missing states: " + ", ".join(missing)
        res["units"] = states
        res["ok"] = not missing
        if states.get("mongod") == "active":
            res["role"] = probe_role(cli)   # e.g. PRIMARY / SECONDARY
    except Exception as e:
        res["error"] = repr(e)
    finally:
        cli.close()
    return res


def probe_all():
    results = [None] * len(NODES)

    def worker(i, n):
        results[i] = check_node(n)

    threads = [threading.Thread(target=worker, args=(i, n))
               for i, n in enumerate(NODES)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return [r if r is not None
            else {"label": n["label"], "ok": False, "error": "probe timeout", "units": {}}
            for r, n in zip(results, NODES)]


def get_status():
    last_probe_ts[0] = time.time()
    up = ensure_tunnel()
    results = probe_all()
    if up and not any(r["ok"] for r in results) and not DIRECT:
        log("all nodes failed -> respawning tunnel once")
        up = ensure_tunnel(force=True)
        results = probe_all()
    record_states(results)
    return {"ts": time.strftime("%H:%M:%S"),
            "tunnel": "up" if up else "down",
            "nodes": results}


ACTIONS = ("restart", "start", "stop", "reset-failed")
# terminal state that counts as success for each action
EXPECTED = {"restart": "active", "start": "active", "stop": "inactive",
            # systemctl reset-failed: failed -> inactive (флаг отказа снят)
            "reset-failed": "inactive"}


def do_action(label, unit, action):
    if not action_lock.acquire(blocking=False):
        return {"ok": False, "error": "another action in progress"}
    try:
        n = BY_LABEL.get(label)
        if n is None or unit not in n["units"]:
            return {"ok": False, "error": "unknown target"}
        if action not in ACTIONS:
            return {"ok": False, "error": "unknown action"}
        if not ensure_tunnel():
            return {"ok": False, "error": "tunnel down"}
        try:
            cli = ssh_connect(n["ip"] if DIRECT else n["lport"], timeout=10)
        except Exception as e:
            return {"ok": False, "error": "connect: %r" % e}
        try:
            pre = active_state(cli, unit)
            t0 = time.time()
            si, so, se = cli.exec_command(
                "sudo -S -p '' systemctl %s %s" % (action, unit), timeout=60)
            si.write(SSH_PASS + "\n")
            si.flush()
            out = so.read().decode("utf-8", "replace")
            err = se.read().decode("utf-8", "replace")
            rc = si.channel.recv_exit_status()
            want = EXPECTED[action]
            post = "?"
            for _ in range(30):
                post = active_state(cli, unit)
                if post == want:
                    break
                time.sleep(1)
            took = round(time.time() - t0, 1)
            r, st, _e = run_cmd(
                cli, "systemctl status --no-pager -l -n 8 %s" % unit, timeout=15)
            ok = (rc == 0 and post == want)
            log("ACTION %s %s/%s -> ok=%s rc=%d pre=%s post=%s %.1fs"
                % (action, label, unit, ok, rc, pre, post, took))
            return {"ok": ok, "action": action, "pre": pre, "post": post,
                    "rc": rc, "took_s": took, "stderr": err.strip(),
                    "stdout": out.strip(), "status": st.strip()}
        finally:
            cli.close()
    finally:
        action_lock.release()


def tests_snapshot():
    d = dict(TESTS)
    if d.get("state") == "running" and d.get("started_ts"):
        d["elapsed_s"] = int(time.time() - d["started_ts"])
    return d


def parse_tests_log():
    with open(RUNNER_LOG, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    summary = re.findall(r"(?m)^__SUMMARY_\w+=.*$", text)[:4]
    tail = ""
    for m in re.finditer(r"(?m)^.*\b\d+ (?:failed|passed).+ in \d+\.\d+s.*$", text):
        tail = m.group(0).strip()
    pytest_rc = None
    m = re.search(r"(?m)^__SUMMARY_PYTEST_RC=(\S+)", text)
    if m:
        v = m.group(1)
        pytest_rc = int(v) if v.isdigit() else v
    # FAILED/ERROR берём только из short summary pytest - вывод самих тестов
    # может содержать эти слова и попадать в счётчик ошибочно:
    pos = text.rfind("short test summary info")
    seg = text[pos:] if pos >= 0 else ""
    failed = []
    for m in re.finditer(r"(?m)^(?:FAILED|ERROR) (.+)$", seg):
        tid, sep, err = m.group(1).strip().partition(" - ")
        failed.append({"test": tid.strip(),
                       "err": (err.strip()[:400] if sep else "")})
    return {"summary": summary, "tail": tail, "pytest_rc": pytest_rc,
            "failed": failed}


def start_tests():
    with tests_ctl:
        if not os.path.exists(RUNNER):
            return {"ok": False, "error": "run_tests.py не найден: %s" % RUNNER}
        if TESTS["state"] == "running":
            return {"ok": False, "error": "already running"}
        if DR["state"] == "running":
            return {"ok": False, "error": "идёт dr_easy (сценарий кластера)"}
        TESTS.update({"state": "running", "started": time.strftime("%H:%M:%S"),
                      "started_ts": time.time(), "ended": None, "rc": None,
                      "pytest_rc": None, "summary": [], "tail": "",
                      "failed": [], "error": ""})
    log("TESTS start requested")
    threading.Thread(target=tests_worker, daemon=True).start()
    return {"ok": True}


def tests_worker():
    tp = None
    try:
        if not DIRECT:
            # stale 2222 = our artifact (same rule as the /tests command):
            # only an ssh.exe listener is touched, a foreign one is left alone
            ps = ("$c=Get-NetTCPConnection -LocalPort 2222 -State Listen "
                  "-ErrorAction SilentlyContinue; if($c){$c.OwningProcess|"
                  "Select-Object -Unique|%"
                  "{if((Get-Process -Id $_ -ErrorAction SilentlyContinue)"
                  ".ProcessName -eq 'ssh'){Stop-Process -Id $_ -Force}}}")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=15)
            time.sleep(0.5)
            tp = subprocess.Popen(
                ["ssh", "-N", "-o", "ExitOnForwardFailure=yes",
                 "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                 "-L", "127.0.0.1:%d:%s:22" % (TEST_LPORT, TEST_DEV02), "lab"],
                creationflags=CREATE_NO_WINDOW)
            time.sleep(4)
            if tp.poll() is not None:
                raise RuntimeError("test tunnel died at start (rc=%s)"
                                   % tp.returncode)
            log("TESTS tunnel up pid=%d" % tp.pid)
        else:
            log("TESTS: режим --direct, туннель не нужен")
        mtime0 = os.path.getmtime(RUNNER_LOG) if os.path.exists(RUNNER_LOG) else 0
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        if DIRECT:
            env["SVCBOARD_DIRECT"] = "1"
        with open(RUNNER_OUT, "w", encoding="utf-8", errors="replace") as fo:
            rc = subprocess.call([PYTHON, RUNNER], cwd=SSH_LAB,
                                 stdout=fo, stderr=subprocess.STDOUT, env=env)
        TESTS["rc"] = rc
        if os.path.exists(RUNNER_LOG) and os.path.getmtime(RUNNER_LOG) > mtime0:
            parsed = parse_tests_log()
            if parsed.get("summary"):
                TESTS.update(parsed)
            else:
                TESTS["error"] = "no summary in log (see tests-runner.out)"
        else:
            TESTS["error"] = "log not updated (runner died early? see tests-runner.out)"
    except Exception as e:
        TESTS["error"] = repr(e)
        log("TESTS worker FAIL: %r" % e)
    finally:
        if tp is not None and tp.poll() is None:
            try:
                tp.kill()
            except Exception:
                pass
        TESTS["ended"] = time.strftime("%H:%M:%S")
        TESTS["state"] = "done"
        log("TESTS done: rc=%s pytest_rc=%s error=%s"
            % (TESTS.get("rc"), TESTS.get("pytest_rc"),
               TESTS.get("error") or "-"))
        tests_hist_add({"epoch": TESTS.get("started_ts") or time.time(),
                        "started": TESTS.get("started"),
                        "ended": TESTS.get("ended"),
                        "rc": TESTS.get("rc"),
                        "pytest_rc": TESTS.get("pytest_rc"),
                        "summary": TESTS.get("summary") or [],
                        "tail": TESTS.get("tail") or "",
                        "failed": TESTS.get("failed") or [],
                        "error": TESTS.get("error") or ""})


# История прогонов автотестов (вкладка «Тесты»): tests_worker дописывает
# запись после каждого прогона; файл рядом с историей статусов.
TESTS_HIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "tests-history.json")
TESTS_HIST_CAP = 50
TESTS_HIST = []                  # новые сверху
tests_hist_lock = threading.Lock()


def load_tests_hist():
    try:
        with open(TESTS_HIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            TESTS_HIST[:] = data[:TESTS_HIST_CAP]
            log("tests history loaded: %d runs" % len(TESTS_HIST))
    except Exception:
        pass


def tests_hist_add(rec):
    """Новый прогон — в начало списка и на диск (перезапуск сохраняет)."""
    try:
        with tests_hist_lock:
            TESTS_HIST.insert(0, rec)
            del TESTS_HIST[TESTS_HIST_CAP:]
            with open(TESTS_HIST_PATH, "w", encoding="utf-8") as f:
                json.dump(TESTS_HIST, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log("tests history save FAIL: %r" % e)


def tests_hist_fail_count():
    """Сколько тестов упало в последнем прогоне (0 = зелёное/нет данных)."""
    if not TESTS_HIST:
        return 0
    last = TESTS_HIST[0]
    if last.get("error") or last.get("pytest_rc") in (0, None):
        return 0
    return len(last.get("failed") or []) or 1


load_tests_hist()


def dr_snapshot():
    d = dict(DR)
    if d.get("state") == "running" and d.get("started_ts"):
        d["elapsed_s"] = int(time.time() - d["started_ts"])
    return d


def dr_out_tail(lines=200):
    """Дословный вывод последнего запуска dr_easy (последние N строк)."""
    try:
        with open(DR_OUT, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def start_dr(mode, backup):
    """Запуск сценария. Никогда не выполняется сам - только по POST с
    подтверждением из UI (клик = явное согласие на разрушение данных)."""
    if not os.path.exists(DR_EASY):
        return {"ok": False, "error": "dr_easy.py не найден: %s" % DR_EASY}
    if mode not in DR_MODE_PORTS:
        return {"ok": False, "error": "mode должен быть fresh или restore"}
    backup = (backup or "").strip()
    if mode == "restore":
        if not backup:
            return {"ok": False, "error": "для restore нужно имя бэкапа"}
        if not DR_NAME_RE.match(backup):
            return {"ok": False, "error": "недопустимое имя бэкапа"}
    else:
        backup = None
    with dr_ctl:
        if DR["state"] == "running":
            return {"ok": False, "error": "dr_easy уже выполняется"}
        if TESTS["state"] == "running":
            return {"ok": False, "error": "идут автотесты — дождитесь их конца"}
        DR.update({"state": "running", "mode": mode, "backup": backup,
                   "started": time.strftime("%H:%M:%S"),
                   "started_ts": time.time(), "ended": None, "rc": None,
                   "error": "", "tunnels": ""})
    log("DR start requested: %s%s"
        % (mode, (" " + backup) if backup else ""))
    threading.Thread(target=dr_worker, args=(mode, backup),
                     daemon=True).start()
    return {"ok": True}


def dr_worker(mode, backup):
    tp = None
    try:
        if DIRECT:
            DR["tunnels"] = "direct"
            log("DR: режим --direct, туннели не нужны")
        else:
            need = DR_MODE_PORTS[mode]
            have = [p for p in need if port_listening(p)]
            miss = [p for p in need if p not in have]
            DR["tunnels"] = "reused" if not miss else "own"
            if have:
                log("DR: туннельные порты уже подняты, использую: %s" % have)
            if miss:
                args = ["ssh", "-N", "-o", "ExitOnForwardFailure=yes",
                        "-o", "ServerAliveInterval=15",
                        "-o", "ServerAliveCountMax=3"]
                for p in miss:
                    args += ["-L", "127.0.0.1:%d:%s:22" % (p, DR_PORTS[p])]
                args += [TUNNEL_HOST]
                tp = subprocess.Popen(args, creationflags=CREATE_NO_WINDOW)
                for _ in range(12):
                    time.sleep(1)
                    if tp.poll() is not None:
                        raise RuntimeError(
                            "др-туннель умер на старте (rc=%s) — порт занят "
                            "старым процессом?" % tp.returncode)
                    if all(port_listening(p) for p in miss):
                        break
                if not all(port_listening(p) for p in miss):
                    raise RuntimeError("др-туннели не поднялись: %s" % miss)
                log("DR tunnel up pid=%d ports=%s" % (tp.pid, miss))
        cmd = [PYTHON, DR_EASY, mode] + ([backup] if backup else [])
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        if DIRECT:
            env["SVCBOARD_DIRECT"] = "1"
        with open(DR_OUT, "w", encoding="utf-8", errors="replace") as fo:
            fo.write("$ %s\n\n" % " ".join(cmd))
            fo.flush()
            r = subprocess.run(cmd, cwd=SSH_LAB, stdout=fo,
                               stderr=subprocess.STDOUT, env=env,
                               timeout=2100)
        DR["rc"] = r.returncode
    except subprocess.TimeoutExpired:
        DR["error"] = "таймаут 35 минут — процесс прерван"
        log("DR worker: timeout 35min")
    except Exception as e:
        DR["error"] = repr(e)
        log("DR worker FAIL: %r" % e)
    finally:
        if tp is not None and tp.poll() is None:
            try:
                tp.kill()
            except Exception:
                pass
        DR["ended"] = time.strftime("%H:%M:%S")
        DR["state"] = "done"
        log("DR done: mode=%s rc=%s error=%s"
            % (DR.get("mode"), DR.get("rc"), DR.get("error") or "-"))


HISTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "history.json")
hist_lock = threading.Lock()
HISTORY = {"baseline": {}, "current": {}, "events": []}
hist_by_key = {}
last_probe_ts = [0.0]


def _rebuild_index():
    hist_by_key.clear()
    for e in HISTORY["events"]:
        hist_by_key.setdefault(e["key"], []).append(e)


def load_history():
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        HISTORY["baseline"] = data.get("baseline", {})
        HISTORY["current"] = data.get("current", {})
        HISTORY["events"] = data.get("events", [])
        _rebuild_index()
        log("history loaded: %d events" % len(HISTORY["events"]))
    except Exception:
        pass


load_history()


def record_states(results):
    """Log state transitions only (first sight = baseline, no event)."""
    now = time.time()
    dirty = False
    with hist_lock:
        for n in results:
            if not n.get("ok"):
                continue          # node unreachable != its services failed
            for unit, st in (n.get("units") or {}).items():
                key = "%s/%s" % (n["label"], unit)
                cur = HISTORY["current"].get(key)
                if cur is None:
                    HISTORY["baseline"][key] = {"state": st, "ts": now}
                    HISTORY["current"][key] = {"state": st, "ts": now}
                    dirty = True
                    continue
                if st == cur["state"]:
                    continue
                ev = {"ts": now, "key": key, "from": cur["state"], "to": st}
                HISTORY["events"].append(ev)
                hist_by_key.setdefault(key, []).append(ev)
                HISTORY["current"][key] = {"state": st, "ts": now}
                dirty = True
                log("HIST %s: %s -> %s" % (key, ev["from"], st))
                if len(HISTORY["events"]) > 50000:
                    HISTORY["events"] = HISTORY["events"][1000:]
                    _rebuild_index()
        if dirty:
            try:
                tmp = HISTORY_PATH + ".tmp"
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(HISTORY, f, ensure_ascii=False)
                os.replace(tmp, HISTORY_PATH)
            except Exception as e:
                log("history save FAIL: %r" % e)


def compute_uptime(now):
    """Uptime %% per service over the whole observation window."""
    with hist_lock:
        out = {}
        for key, base in HISTORY["baseline"].items():
            total = max(0.001, now - base["ts"])
            acc = 0.0
            state = base["state"]
            t = base["ts"]
            for e in hist_by_key.get(key, ()):
                if state == "active":
                    acc += e["ts"] - t
                state = e["to"]
                t = e["ts"]
            if state == "active":
                acc += now - t
            out[key] = round(100.0 * acc / total, 2)
        started = min((b["ts"] for b in HISTORY["baseline"].values()),
                      default=None)
    return out, started


def history_payload():
    now = time.time()
    uptime, started = compute_uptime(now)
    with hist_lock:
        events = list(reversed(HISTORY["events"][-15:]))
    return {"uptime": uptime, "started": started, "events": events}


def recorder_loop():
    """Probe even with the browser closed: history must not depend on UI."""
    while True:
        try:
            if time.time() - last_probe_ts[0] >= 50:
                get_status()
        except Exception as e:
            log("recorder FAIL: %r" % e)
        time.sleep(60)


def lan_ip():
    """Local LAN address of this PC (route lookup towards the laptop, no packet)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.168.1.114", 22))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip if ip and not ip.startswith("127.") else ""
    except Exception:
        return ""


LAN_IP = lan_ip()
LAN_HINT = ("для телефона: http://%s:%d" % (LAN_IP, HTTP_PORT)) if LAN_IP else ""


def _authorized(handler):
    # this PC (loopback) = no password; LAN and internet must auth
    if handler.client_address[0] in ("127.0.0.1", "::1"):
        return True
    a = handler.headers.get("Authorization", "")
    if not a.startswith("Basic "):
        return False
    try:
        up = base64.b64decode(a[6:].strip()).decode("utf-8", "replace")
        u, _, p = up.partition(":")
    except Exception:
        return False
    return u == WEB_USER and p == WEB_PASS


def _deny(handler):
    body = b"401: authorization required"
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", 'Basic realm="svcboard"')
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Сервисы — статус</title>
<style>
:root{--bg:#0f1420;--card:#171d2c;--line:#242c42;--tx:#e6e9f0;--dim:#8b93a7}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--tx);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
.top{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:16px}
h1{font-size:16px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin:0}
.tbox{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.tbox-bottom{margin-top:16px}
.tbox-top{margin-top:0}
button.tbtn{background:#1c3a2a;border-color:#2e6b4a;color:#8ff0b8;font-weight:600;padding:7px 14px}
button.tbtn:hover{background:#234a36}
button.tbtn:disabled{opacity:.55;cursor:default}
#tstate{font-size:12.5px;color:var(--dim)}
#tstate.ok{color:#7ee2a8}
#tstate.warn{color:#f5a623}
#tstate.bad{color:#ff9ea3}
#tsum{display:none;margin:0 0 16px;padding:10px 14px;background:var(--card);border:1px solid var(--line);border-radius:10px;font-size:12px;color:#9fb0d0;white-space:pre-wrap;overflow-x:auto;font-family:ui-monospace,Consolas,monospace}
#drout{display:none;margin:0 0 16px;padding:10px 14px;background:var(--card);border:1px solid var(--line);border-radius:10px;font-size:12px;color:#9fb0d0;white-space:pre-wrap;overflow-x:auto;max-height:420px;overflow-y:auto;font-family:ui-monospace,Consolas,monospace}
.drlabel{color:var(--dim);font-size:12.5px}
#drbak{background:#1a2136;color:var(--tx);border:1px solid #35405e;border-radius:8px;padding:5px 10px;font-size:12.5px;width:210px;font-family:ui-monospace,Consolas,monospace}
#drbak:disabled{opacity:.5}
button.dbtn{background:#3a2a10;border-color:#7a5a1a;color:#ffd45e;font-weight:600}
button.dbtn:hover{background:#4a3512}
button.dbtn:disabled{opacity:.5;cursor:default}
#drstate{font-size:12.5px;color:var(--dim)}
.hist{margin-top:18px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.hhead{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;font-size:13px;font-weight:600;letter-spacing:.06em;color:#9fb0d0;margin-bottom:12px}
.hstart{font-weight:400;font-size:11.5px;color:#5c6580}
.hcols{display:grid;grid-template-columns:minmax(240px,1fr) minmax(280px,1.5fr);gap:20px}
@media (max-width:760px){.hcols{grid-template-columns:1fr}}
.htitle{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#5c6580;margin-bottom:8px}
.up{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:#1e2740;border:1px solid #2b3554;border-radius:6px;padding:3px 8px;font-size:11.5px;font-family:ui-monospace,Consolas,monospace;color:#8b93a7}
.chip b{font-weight:600}
.v-ok{color:#7ee2a8}
.v-warn{color:#f5a623}
.v-bad{color:#ff9ea3}
.ev{display:flex;gap:8px;flex-wrap:wrap;font-family:ui-monospace,Consolas,monospace;font-size:11.5px;padding:4px 0;border-top:1px solid #1e2740;color:#8b93a7}
.ev:first-of-type{border-top:0}
.ev .t{color:#5c6580}
.ev .who{color:#9fb0d0}
.to-active{color:#7ee2a8}
.to-bad{color:#ff9ea3}
.empty{color:#5c6580;font-size:12px}
#lan{margin-left:auto}
@media (max-width:600px){
  body{padding:12px}
  .grid{grid-template-columns:1fr}
  .svc{gap:6px}
  button{padding:9px 14px}
  button.mini{padding:8px 10px;font-size:12px}
  button.tbtn{padding:10px 16px}
  footer{flex-wrap:wrap;gap:8px}
  #lan{margin-left:0}
  #tsum{font-size:11px}
  .chip{font-size:11px}
}
#banner{display:none;margin-bottom:16px;padding:10px 14px;border-radius:10px;font-size:13px;white-space:pre-wrap}
#banner.ok{display:block;background:#123d24;border:1px solid #1e6b3f;color:#7ee2a8}
#banner.bad{display:block;background:#3d1519;border:1px solid #7a2a30;color:#ff9ea3}
#banner.info{display:block;background:#15263d;border:1px solid #2a4a7a;color:#9cc3ff}
.tl{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
#tabs{display:flex;gap:6px}
button.tab{background:transparent;border:1px solid transparent;color:var(--dim);padding:5px 14px;font-size:12.5px;font-weight:600;letter-spacing:.05em;border-radius:8px}
button.tab:hover{background:#232b40}
button.tab.on{background:#1e2740;border-color:#35405e;color:var(--tx)}
#tabfail{display:none;margin-left:7px;background:#3d1519;border:1px solid #7a2a30;color:#ff9ea3;border-radius:6px;padding:0 6px;font-size:10.5px;font-weight:700}
.runs{display:flex;flex-direction:column;gap:8px}
.rhead{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;cursor:pointer;font-family:ui-monospace,Consolas,monospace;font-size:12px}
.rhead:hover{opacity:.85}
.rwhen{color:#5c6580}
.rstate{color:#8b93a7}
.rstate.ok{color:#7ee2a8}
.rstate.warn{color:#f5a623}
.rstate.bad{color:#ff9ea3}
.rfail{background:#3d1519;border:1px solid #7a2a30;color:#ff9ea3;border-radius:6px;padding:0 6px;font-size:10.5px;font-weight:700}
.rtog{color:#5c6580;margin-left:auto}
.rdet{margin-top:6px;padding:8px 10px;background:#161d31;border:1px solid var(--line);border-radius:8px;font-family:ui-monospace,Consolas,monospace;font-size:11.5px;color:#9fb0d0;word-break:break-all}
.rsumm{color:#8b93a7;margin-bottom:6px}
.fail{padding:5px 0;border-top:1px solid #1e2740}
.fail:first-of-type{border-top:0}
.fid{color:#ff9ea3}
.ferr{color:#c76b70;font-size:11px;white-space:pre-wrap;max-height:140px;overflow-y:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.node{font-weight:600;font-size:13px;letter-spacing:.06em;color:#9fb0d0;margin-bottom:6px}
.svc{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid var(--line)}
.svc:first-of-type{border-top:0}
.dot{width:11px;height:11px;border-radius:50%;background:#3a3f4c;flex:none}
.dot.ok{background:#2ecc40;box-shadow:0 0 7px rgba(46,204,64,.55)}
.dot.warn{background:#f5a623;box-shadow:0 0 7px rgba(245,166,35,.55)}
.dot.bad{background:#ff4d4f;box-shadow:0 0 7px rgba(255,77,79,.55)}
.svc .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:ui-monospace,Consolas,monospace;font-size:13px}
.svc .st{color:var(--dim);font-size:12px;text-align:right;white-space:nowrap}
button{background:#232b40;color:var(--tx);border:1px solid #35405e;border-radius:8px;padding:5px 12px;font-size:12.5px;cursor:pointer}
button:hover{background:#2c3650}
.btns{display:flex;gap:4px}
button.mini{padding:3px 7px;font-size:11px;border-radius:6px;font-weight:600;letter-spacing:.04em}
button.danger{border-color:#5e2a30;color:#ff9ea3}
button.danger:hover{background:#3a2026}
.err{color:#ff9ea3;font-size:12.5px;padding:6px 0;white-space:pre-wrap;word-break:break-all}
.pbadge{background:#3a2e10;border:1px solid #8a6d1f;color:#ffd45e;border-radius:6px;padding:1px 7px;font-size:11px;font-weight:700;margin-left:8px;vertical-align:middle}
.sbadge{background:#1e2740;border:1px solid #2b3554;color:#8b93a7;border-radius:6px;padding:1px 7px;font-size:11px;margin-left:8px;vertical-align:middle}
.rbadge{background:#3a2a10;border:1px solid #7a5a1a;color:#f5a623;border-radius:6px;padding:1px 7px;font-size:11px;margin-left:8px;vertical-align:middle}
.primsum{margin-left:auto;font-size:12.5px;color:var(--dim)}
footer{margin-top:16px;color:#5c6580;font-size:12px;display:flex;gap:14px;align-items:center}
</style>
</head>
<body>
<div class="top">
<div class="tl">
<h1>Статус сервисов</h1>
<nav id="tabs">
<button class="tab on" id="tab-board" onclick="setTab('board')">Доска</button>
<button class="tab" id="tab-tests" onclick="setTab('tests')">Тесты<span id="tabfail"></span></button>
</nav>
</div>
<span id="primsum" class="primsum"></span>
</div>
<div id="banner"></div>
<div id="pane-board">
<div class="grid" id="grid"></div>
<div class="hist">
  <div class="hhead"><span>История статусов</span><span class="hstart" id="hstart"></span></div>
  <div class="hcols">
    <div><div class="htitle">Аптайм с начала наблюдения</div><div class="up" id="up"></div></div>
    <div><div class="htitle">Последние изменения</div><div id="hlist"></div></div>
  </div>
</div>
</div>
<div id="pane-tests" style="display:none">
<div class="tbox tbox-top" id="testsbox"><button id="tbtn" class="tbtn" onclick="startTests()">▶ Автотесты dev-02 (~8 мин)</button><span id="tstate"></span></div>
<pre id="tsum"></pre>
<div class="tbox tbox-bottom" id="drbox"><span class="drlabel">DR-сценарий (dr_easy):</span><button id="drb0" class="dbtn" onclick="startDr('fresh')">⚠ fresh — новый кластер</button><button id="drb1" class="dbtn" onclick="startDr('restore')">♻ restore — с восстановлением</button><input id="drbak" value="2026-10-03T22:19:33Z" spellcheck="false" title="Имя бэкапа (для restore)"><span id="drstate"></span></div>
<pre id="drout"></pre>
<div class="hist">
  <div class="hhead"><span>Результаты прогонов</span><span class="hstart" id="runsn"></span></div>
  <div class="runs" id="runs"><span class="empty">загрузка…</span></div>
</div>
</div>
<footer><span id="ts">загрузка…</span><button onclick="load()">Обновить</button><span id="lan">__LAN__</span></footer>
<script>
const NODES = __NODES_JSON__;
let RUNS = [], prevTestsState = null;
function dotClass(s){
  if(s==='active')return 'ok';
  if(s==='activating'||s==='reloading')return 'warn';
  if(s==='?'||s==='unknown')return '';
  return 'bad';
}
function banner(msg,kind){const b=document.getElementById('banner');b.textContent=msg||'';b.className=msg?(kind||''):'';}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function render(data){
  const g=document.getElementById('grid');g.innerHTML='';
  NODES.forEach(sn=>{
    const n=(data.nodes.find(x=>x.label===sn.label))||{label:sn.label,ok:false,error:'нет данных'};
    const c=document.createElement('div');c.className='card';
    let h='<div class="node">'+esc(sn.label);
    if(n.role==='PRIMARY')h+='<span class="pbadge" title="реплика-сет: текущий primary">[P]</span>';
    else if(n.role==='SECONDARY')h+='<span class="sbadge" title="реплика-сет: secondary">[S]</span>';
    else if(n.role)h+='<span class="rbadge" title="реплика-сет: состояние">'+esc(n.role)+'</span>';
    h+='</div>';
    if(!n.ok){
      h+='<div class="err">'+esc(n.error||'нет данных')+'</div>';
    }else{
      sn.units.forEach(u=>{
        const st=(n.units&&n.units[u])||'?';
        const stTxt=(st==='active')?'':esc(st);
        h+='<div class="svc"><span class="dot '+dotClass(st)+'" title="'+esc(st)+'"></span>'
          +'<span class="nm" title="'+esc(u)+'">'+esc(u)+'</span><span class="st">'+stTxt+'</span>'
          +'<span class="btns">'
          +'<button class="mini" title="Рестарт" onclick="act(\'restart\',\''+esc(sn.label)+'\',\''+esc(u)+'\')">RS</button>'
          +'<button class="mini" title="Старт" onclick="act(\'start\',\''+esc(sn.label)+'\',\''+esc(u)+'\')">GO</button>'
          +'<button class="mini danger" title="Стоп" onclick="act(\'stop\',\''+esc(sn.label)+'\',\''+esc(u)+'\')">SP</button>'
          +(u==='sbm-agent'&&st==='failed'
            ?'<button class="mini danger" title="failed-reset (systemctl reset-failed): сбросить состояние failed" onclick="act(\'reset-failed\',\''+esc(sn.label)+'\',\''+esc(u)+'\')">FR</button>'
            :'')
          +'</span></div>';
      });
    }
    c.innerHTML=h;g.appendChild(c);
  });
  const prim=NODES.find(sn=>{const x=data.nodes.find(y=>y.label===sn.label);return x&&x.role==='PRIMARY';});
  const ps=document.getElementById('primsum');
  if(data.nodes.some(x=>'role' in x)){
    ps.textContent=prim?('Mongo primary: '+prim.label+' [P]'):'Mongo primary: не определён';
  }
  renderTests(data.tests);
  renderDr(data.dr);
  const av=data.avail||{tests:true,dr:true};
  const tbs=document.getElementById('testsbox'),tsm=document.getElementById('tsum');
  if(tbs)tbs.style.display=av.tests?'':'none';
  if(tsm&&!av.tests)tsm.style.display='none';
  const dbs=document.getElementById('drbox'),dou=document.getElementById('drout');
  if(dbs)dbs.style.display=av.dr?'':'none';
  if(dou&&!av.dr)dou.style.display='none';
  renderHistory(data.history);
  const hb=document.getElementById('tabfail');
  if(hb){
    const n=data.hist_fail|0;
    hb.style.display=n>0?'inline':'none';
    hb.textContent=String(n);
    hb.title=n>0?('последний прогон: упало '+n+' тест(ов) — вкладка «Тесты»')
                    :'последний прогон без падений';
  }
  if(data.tests&&data.tests.state!==prevTestsState){
    if(prevTestsState==='running'&&data.tests.state==='done')fetchRuns();
    prevTestsState=data.tests.state;
  }
}
async function load(){
  try{
    const r=await fetch('/api/status',{cache:'no-store'});
    const d=await r.json();
    render(d);
    document.getElementById('ts').textContent=(d.ts||'')+' · туннель: '+d.tunnel;
  }catch(e){
    document.getElementById('ts').textContent='нет связи с приложением';
    banner('Не удалось получить статус: '+e,'bad');
  }
}
const ACTS={
  restart:{v:'Перезапустить',consent:'на остановку и повторный запуск',name:'Рестарт'},
  start:{v:'Запустить',consent:'на запуск',name:'Старт'},
  stop:{v:'Остановить',consent:'на остановку',name:'Стоп'}
};
async function act(action,node,unit){
  const m=ACTS[action];
  if(!confirm(m.v+' '+unit+' на '+node+'?\n\nКлик = явное согласие '+m.consent+' боевого сервиса.'))return;
  banner(m.name+': '+unit+' на '+node+'…','info');
  try{
    const r=await fetch('/api/svc',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({node:node,unit:unit,action:action})});
    const d=await r.json();
    if(d.ok)banner('✓ принято: '+unit+' на '+node+' ['+action+'] — pre='+d.pre+' → post='+d.post+', '+d.took_s+' с','ok');
    else banner('✗ НЕ принято: '+(d.error||('rc='+d.rc+' post='+d.post))+(d.stderr?'\n'+d.stderr:''),'bad');
  }catch(e){banner('✗ Ошибка запроса: '+e,'bad');}
  load();
}
function fmtTs(ts,forceDate){
  const d=new Date(ts*1000),p=n=>String(n).padStart(2,'0');
  const sameDay=d.toDateString()===new Date().toDateString();
  const date=(forceDate||!sameDay)?(p(d.getDate())+'.'+p(d.getMonth()+1)+' '):'';
  return date+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());
}
function renderHistory(h){
  const up=document.getElementById('up'),list=document.getElementById('hlist'),st=document.getElementById('hstart');
  if(!h)return;
  st.textContent=h.started?('наблюдение с '+fmtTs(h.started,true)):'наблюдение ещё не начато';
  const keys=Object.keys(h.uptime||{}).sort();
  up.innerHTML=keys.length?keys.map(k=>{
    const v=h.uptime[k];
    const cls=(v>=99.5?'v-ok':(v>=95?'v-warn':'v-bad'));
    return '<span class="chip">'+esc(k)+' <b class="'+cls+'">'+(v===100?'100':v.toFixed(2))+'%</b></span>';
  }).join(''):'<span class="empty">данных пока нет</span>';
  const evs=h.events||[];
  list.innerHTML=evs.length?evs.map(e=>{
    const i=e.key.indexOf('/'),node=e.key.slice(0,i),unit=e.key.slice(i+1);
    const c=x=>(x==='active')?'to-active':'to-bad';
    return '<div class="ev"><span class="t">'+fmtTs(e.ts)+'</span><span class="who">'+esc(node)+' '+esc(unit)+'</span>'
      +'<span class="'+c(e.from)+'">'+esc(e.from)+'</span><span class="t">→</span><span class="'+c(e.to)+'">'+esc(e.to)+'</span></div>';
  }).join(''):'<span class="empty">изменений пока не было — всё стабильно</span>';
}
function setTab(t){
  document.getElementById('pane-board').style.display=(t==='board')?'':'none';
  document.getElementById('pane-tests').style.display=(t==='tests')?'':'none';
  document.getElementById('tab-board').className='tab'+(t==='board'?' on':'');
  document.getElementById('tab-tests').className='tab'+(t==='tests'?' on':'');
  if(t==='tests')fetchRuns();
}
async function fetchRuns(){
  try{
    const r=await fetch('/api/tests_hist',{cache:'no-store'});
    const d=await r.json();
    RUNS=d.runs||[];
    renderRuns();
  }catch(e){}
}
function runState(r){
  if(r.error)return{cls:'bad',txt:'ошибка: '+r.error};
  const t=(r.tail?' · '+r.tail:'');
  if(r.pytest_rc===0)return{cls:'ok',txt:'pytest_rc=0'+t};
  if(r.pytest_rc!==null&&r.pytest_rc!==undefined)
    return{cls:(r.pytest_rc===1?'warn':'bad'),txt:'pytest_rc='+r.pytest_rc+t};
  return{cls:'warn',txt:'rc='+r.rc+' (pytest не дошёл)'};
}
function renderRuns(){
  const box=document.getElementById('runs'),n=document.getElementById('runsn');
  if(!box)return;
  if(n)n.textContent=RUNS.length?(RUNS.length+' прогон(ов), новый сверху')
                                :'прогонов пока нет';
  if(!RUNS.length){box.innerHTML='<span class="empty">результатов пока нет — запусти автотесты</span>';return;}
  box.innerHTML=RUNS.map((r,i)=>{
    const s=runState(r);
    const when=r.epoch?fmtTs(r.epoch,true):((r.date||'')+' '+(r.started||''));
    const fails=r.failed||[];
    const summ=(r.summary||[]).map(l=>esc(l)).join('<br>');
    let det='';
    if(summ)det+='<div class="rsumm">'+summ+'</div>';
    if(fails.length){
      det+=fails.map(f=>'<div class="fail"><div class="fid">'+esc(f.test)+'</div>'
        +(f.err?'<div class="ferr">'+esc(f.err)+'</div>':'')+'</div>').join('');
    }else if(!r.error){
      det+='<div class="empty">упавших тестов нет</div>';
    }
    if(r.error)det+='<div class="fid">'+esc(r.error)+'</div>';
    return '<div class="run">'
      +'<div class="rhead" onclick="toggleRun('+i+')" title="показать/скрыть детали">'
      +'<span class="rwhen">'+esc(when)+'</span>'
      +'<span class="rstate '+s.cls+'">'+esc(s.txt)+'</span>'
      +(fails.length?'<span class="rfail">'+fails.length+' failed</span>':'')
      +'<span class="rtog" id="rtog'+i+'">▾</span></div>'
      +'<div class="rdet" id="rdet'+i+'" style="display:none">'+det+'</div>'
      +'</div>';
  }).join('');
}
function toggleRun(i){
  const d=document.getElementById('rdet'+i),t=document.getElementById('rtog'+i);
  if(!d)return;
  const open=d.style.display!=='none';
  d.style.display=open?'none':'block';
  t.textContent=open?'▾':'▴';
}
let tTick=null;
function renderTests(t){
  const b=document.getElementById('tbtn'),s=document.getElementById('tstate'),sum=document.getElementById('tsum');
  if(tTick){clearInterval(tTick);tTick=null;}
  if(!t)return;
  if(t.state==='running'){
    b.disabled=true;b.textContent='Идёт прогон…';
    const upd=()=>{const e=t.started_ts?Math.max(0,Math.floor(Date.now()/1000-t.started_ts)):0;
      s.className='';s.textContent='старт '+t.started+' · '+e+' с';};
    upd();tTick=setInterval(upd,1000);
    sum.style.display='none';
    return;
  }
  b.disabled=false;b.textContent='▶ Автотесты dev-02 (~8 мин)';
  if(t.state==='done'){
    if(t.error){s.className='bad';s.textContent='ошибка: '+t.error;}
    else{s.className=(t.pytest_rc==0?'ok':(t.pytest_rc==1?'warn':'bad'));
      s.textContent='pytest_rc='+t.pytest_rc+(t.tail?' · '+t.tail:'');}
    const lines=(t.summary||[]).concat(t.tail?[t.tail]:[]);
    if(lines.length){sum.textContent=lines.join('\n');sum.style.display='block';}
    else{sum.style.display='none';}
  }else{s.className='';s.textContent='';sum.style.display='none';}
}
async function startTests(){
  if(!confirm('Запустить полный прогон автотестов на dev-02?\n\n~8 минут (git pull + pytest).\nПока идёт — не запускай /tests и /status.'))return;
  try{
    const r=await fetch('/api/tests',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const d=await r.json();
    if(!d.ok)banner('✗ Тесты не запущены: '+d.error,'bad');
    else banner('Автотесты запущены (dev-02) — состояние сверху, дословный блок появится по готовности.','info');
  }catch(e){banner('✗ Ошибка запроса: '+e,'bad');}
  load();
}
let drTick=null,drOutTick=null;
async function refreshDrOut(){
  try{
    const r=await fetch('/api/drout',{cache:'no-store'});
    if(!r.ok)return;
    const t=await r.text();
    const o=document.getElementById('drout');
    o.textContent=t;o.style.display=t?'block':'none';
  }catch(e){}
}
function renderDr(d){
  const b0=document.getElementById('drb0'),b1=document.getElementById('drb1'),
        bak=document.getElementById('drbak'),s=document.getElementById('drstate');
  if(drTick){clearInterval(drTick);drTick=null;}
  if(drOutTick){clearInterval(drOutTick);drOutTick=null;}
  if(!d)return;
  if(d.state==='running'){
    b0.disabled=true;b1.disabled=true;bak.disabled=true;
    const upd=()=>{const e=d.started_ts?Math.max(0,Math.floor(Date.now()/1000-d.started_ts)):0;
      s.className='';s.textContent='dr_easy '+d.mode+' · старт '+d.started+' · '+e+' с';};
    upd();drTick=setInterval(upd,1000);
    refreshDrOut();drOutTick=setInterval(refreshDrOut,3000);
    return;
  }
  b0.disabled=false;b1.disabled=false;bak.disabled=false;
  if(d.state==='done'){
    s.className=(d.rc===0?'ok':'bad');
    s.textContent=d.mode+' · rc='+d.rc+(d.error?' · '+d.error:'');
    refreshDrOut();
  }else{s.className='';s.textContent='';}
}
async function startDr(mode){
  const bak=(document.getElementById('drbak').value||'').trim();
  if(mode==='fresh'){
    if(!confirm('Запустить dr_easy.py fresh?\n\n⚠ УДАЛИТ /var/lib/mongodb на na-22/23/24\nи соберёт ПУСТОЙ кластер (данных не будет).\n\nКлик = явное согласие на разрушение данных.'))return;
  }else{
    if(!bak){banner('✗ Укажите имя бэкапа для restore','bad');return;}
    if(!confirm('Запустить dr_easy.py restore '+bak+'?\n\n⚠ УДАЛИТ /var/lib/mongodb на na-22/23/24,\nпересоберёт кластер и восстановит данные\nиз бэкапа:\n  '+bak+'\n\nКлик = явное согласие на разрушение данных.'))return;
  }
  try{
    const r=await fetch('/api/dr',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({mode:mode,backup:bak})});
    const d=await r.json();
    if(!d.ok)banner('✗ Сценарий не запущен: '+d.error,'bad');
    else banner('DR-сценарий запущен ('+mode+') — дословный вывод ниже, таймер рядом с кнопками.','info');
  }catch(e){banner('✗ Ошибка запроса: '+e,'bad');}
  load();
}
render({nodes:NODES.map(n=>({label:n.label,ok:true,
  units:Object.fromEntries(n.units.map(u=>[u,'?']))}))});
load();
fetchRuns();
setInterval(load,12000);
</script>
</body>
</html>
"""

HTML = PAGE.replace("__NODES_JSON__", json.dumps(
    [{"label": n["label"], "units": n["units"]} for n in NODES],
    ensure_ascii=False)).replace("__LAN__", LAN_HINT)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if not _authorized(self):
            _deny(self)
            return
        if self.path in ("/", "/index.html"):
            self._send(200, "text/html", HTML)
        elif self.path == "/api/status":
            try:
                payload = get_status()
                payload["tests"] = tests_snapshot()
                payload["dr"] = dr_snapshot()
                payload["avail"] = {"tests": os.path.exists(RUNNER),
                                    "dr": os.path.exists(DR_EASY)}
                payload["history"] = history_payload()
                payload["hist_n"] = len(TESTS_HIST)
                payload["hist_fail"] = tests_hist_fail_count()
                self._send(200, "application/json",
                           json.dumps(payload, ensure_ascii=False))
            except Exception as e:
                self._send(500, "application/json",
                           json.dumps({"error": repr(e)}, ensure_ascii=False))
        elif self.path == "/api/drout":
            self._send(200, "text/plain", dr_out_tail())
        elif self.path == "/api/tests_hist":
            with tests_hist_lock:
                blob = json.dumps({"runs": TESTS_HIST}, ensure_ascii=False)
            self._send(200, "application/json", blob)
        else:
            self._send(404, "text/plain", "not found")

    def do_POST(self):
        if not _authorized(self):
            _deny(self)
            return
        if self.path not in ("/api/svc", "/api/tests", "/api/dr"):
            self._send(404, "text/plain", "not found")
            return
        try:
            ln = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(ln) or b"{}")
            if self.path == "/api/svc":
                res = do_action(req.get("node", ""), req.get("unit", ""),
                                req.get("action", ""))
            elif self.path == "/api/dr":
                res = start_dr(req.get("mode", ""), req.get("backup", ""))
            else:
                res = start_tests()
            self._send(200, "application/json",
                       json.dumps(res, ensure_ascii=False))
        except Exception as e:
            self._send(500, "application/json",
                       json.dumps({"ok": False, "error": repr(e)},
                                  ensure_ascii=False))


def main():
    open_browser = "--no-open" not in sys.argv
    try:
        server = ThreadingHTTPServer((BIND, HTTP_PORT), Handler)
    except OSError as e:
        log("cannot bind %s:%d - %r" % (BIND, HTTP_PORT, e))
        sys.exit(1)
    log("svcboard: http://127.0.0.1:%d (LAN: %s)" % (HTTP_PORT, LAN_IP or "n/a"))
    threading.Thread(target=recorder_loop, daemon=True).start()
    log("history recorder started (60s poll)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(
            "http://127.0.0.1:%d" % HTTP_PORT)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("stopping...")
    finally:
        with tunnel_lock:
            if tunnel_proc is not None and tunnel_proc.poll() is None:
                try:
                    tunnel_proc.kill()
                except Exception:
                    pass
        server.server_close()


if __name__ == "__main__":
    main()
