// Dev smoke test: extract the <script> block from svcboard.py, run it against
// a minimal DOM stub + fetch stub, then exercise the new tab/results code.
// Usage: node svcboard-smoke.js <path\to\svcboard.py>
// Exit code = number of failed checks.
const fs = require("fs");

const src = fs.readFileSync(process.argv[2], "utf8");
const m = /<script>([\s\S]*?)<\/script>/.exec(src);
if (!m) { console.log("NO_SCRIPT_BLOCK"); process.exit(1); }

const NODES_JSON = JSON.stringify([
  { label: "na-22", units: ["mongod", "sbm-agent", "beholder"] },
  { label: "na-57", units: ["visordb-proxy"] }
]);
const js = m[1].replace("__NODES_JSON__", NODES_JSON).replace("__LAN__", "x");

// --- minimal DOM stub (auto-creates elements by id) ---
const els = new Map();
function mkEl(id) {
  const e = {
    id, style: {}, children: [], className: "", textContent: "",
    title: "", disabled: false, value: "",
    appendChild(c) { this.children.push(c); }
  };
  // real DOM: assigning innerHTML='' clears the children - mimic that
  let html = "";
  Object.defineProperty(e, "innerHTML", {
    get() { return html; },
    set(v) { html = v; if (v === "") e.children = []; }
  });
  els.set(id, e);
  return e;
}
global.document = {
  getElementById(id) { return els.get(id) || mkEl(id); },
  createElement() { return mkEl("dyn:" + Math.random()); }
};

// --- fetch stub: /api/tests_hist vs /api/status ---
const RUNREC = {
  epoch: 1791111093.0, started: "13:51:33", ended: "13:59:57", rc: 0,
  pytest_rc: 1,
  summary: ["__SUMMARY_CD_RC=0", "__SUMMARY_UID=0/root",
            "__SUMMARY_GIT_RC=0", "__SUMMARY_PYTEST_RC=1"],
  tail: "2 failed, 729 passed, 23 skipped in 499.97s (0:08:19)",
  failed: [{ test: "misc/test_33_connection_options.py::TestReadPreference::x",
             err: "ServerSelectionTimeoutError" }],
  error: ""
};
const STATUS = {
  ts: "2026-10-04 14:30:00", tunnel: "up",
  nodes: [
    { label: "na-22", ok: true, role: "PRIMARY",
      units: { "mongod": "active", "sbm-agent": "active",
               "beholder": "active" } },
    { label: "na-57", ok: true, units: { "visordb-proxy": "active" } }
  ],
  avail: { tests: true, dr: true },
  tests: { state: "idle" }, dr: { state: "idle" },
  hist_n: 1, hist_fail: 2,
  history: {
    started: 1791000000,
    uptime: { "na-22/mongod": 99.9 },
    events: [{ key: "na-22/mongod", ts: 1791100000,
               from: "active", to: "inactive" }]
  }
};
global.fetch = (url) => Promise.resolve({
  ok: true,
  json: async () => String(url).indexOf("tests_hist") >= 0
    ? { runs: [RUNREC] } : STATUS,
  text: async () => ""
});
global.confirm = () => true;

const harness = `
;(async () => {
  const fails = [];
  const chk = (name, cond, extra) => {
    console.log((cond ? "PASS " : "FAIL ") + name + (extra !== undefined ? " -> " + extra : ""));
    if (!cond) fails.push(name);
  };
  await new Promise(r => setTimeout(r, 50));
  await load();
  await fetchRuns();
  // badge from hist_fail
  const hb = document.getElementById("tabfail");
  chk("badge visible on failed run", hb.style.display === "inline", hb.style.display);
  chk("badge shows failed count", hb.textContent === "2", hb.textContent);
  // grid rendered
  chk("grid has node cards", document.getElementById("grid").children.length === 2,
      document.getElementById("grid").children.length);
  chk("primary badge in primsum",
      (document.getElementById("primsum").textContent || "").indexOf("na-22") >= 0,
      document.getElementById("primsum").textContent);
  // tab switch
  setTab("tests");
  chk("tests pane shown", document.getElementById("pane-tests").style.display === "");
  chk("board pane hidden", document.getElementById("pane-board").style.display === "none");
  chk("tab-tests active", document.getElementById("tab-tests").className === "tab on",
      document.getElementById("tab-tests").className);
  chk("tab-board inactive", document.getElementById("tab-board").className === "tab");
  // runs list
  chk("one run rendered", RUNS.length === 1, RUNS.length);
  const runsHTML = document.getElementById("runs").innerHTML;
  chk("run row has state text", runsHTML.indexOf("pytest_rc=1") >= 0);
  chk("run row has failed chip", runsHTML.indexOf("rfail") >= 0);
  chk("run row shows failed test id", runsHTML.indexOf("test_33_connection_options") >= 0);
  chk("run counter header", (document.getElementById("runsn").textContent || "").indexOf("1 прогон") >= 0,
      document.getElementById("runsn").textContent);
  chk("summary lines in details", runsHTML.indexOf("__SUMMARY_PYTEST_RC=1") >= 0);
  // details toggle (auto-created stub element)
  try { toggleRun(0); chk("toggleRun no throw", true); }
  catch (e) { chk("toggleRun no throw", false, e && e.message); }
  // done-state rendering of the live tests box
  const d2 = Object.assign({}, STATUS, { tests: {
    state: "done", started: "13:51:33", started_ts: 1791111093, ended: "13:59:57",
    rc: 0, pytest_rc: 1, summary: STATUS_SUMMARY.slice(), tail: "2 failed, 729 passed",
    failed: [{test: "x", err: "y"}], error: "" } });
  render(d2);
  chk("live box shows summary", (document.getElementById("tsum").textContent || "").indexOf("__SUMMARY_PYTEST_RC") >= 0,
      (document.getElementById("tsum").textContent || "").slice(0, 40));
  chk("live box state warn", document.getElementById("tstate").className === "warn",
      document.getElementById("tstate").className);
  // back to board tab
  setTab("board");
  chk("board pane back", document.getElementById("pane-board").style.display === "");
  chk("tests pane hidden again", document.getElementById("pane-tests").style.display === "none");
  // history section rendered
  chk("uptime chip", (document.getElementById("up").innerHTML || "").indexOf("99.90") >= 0,
      document.getElementById("up").innerHTML);
  chk("history event row", (document.getElementById("hlist").innerHTML || []).length > 0);
  // failed-reset button: only on sbm-agent, only while failed
  const d3 = JSON.parse(JSON.stringify(STATUS));
  d3.nodes[0].units["sbm-agent"] = "failed";
  render(d3);
  const card22 = document.getElementById("grid").children[0].innerHTML;
  chk("FR button on failed sbm-agent", card22.indexOf("reset-failed") >= 0 && card22.indexOf(">FR<") >= 0);
  const d4 = JSON.parse(JSON.stringify(STATUS));
  d4.nodes[0].units["mongod"] = "failed";
  render(d4);
  const cardM = document.getElementById("grid").children[0].innerHTML;
  chk("no FR on failed mongod", cardM.indexOf("reset-failed") < 0);
  chk("mongod failure still shown", cardM.indexOf("failed") >= 0);
  render(STATUS);
  chk("no FR when everything active", document.getElementById("grid").children[0].innerHTML.indexOf("reset-failed") < 0);
  // ================= вкладка «Нагрузка» =================
  setTab("load");
  chk("load pane shown", document.getElementById("pane-load").style.display === "");
  chk("board pane hidden on load", document.getElementById("pane-board").style.display === "none");
  chk("tab-load active", document.getElementById("tab-load").className === "tab on",
      document.getElementById("tab-load").className);
  chk("tab-board inactive on load", document.getElementById("tab-board").className === "tab");
  chk("empty state before fetch", document.getElementById("lgrid").innerHTML === "");
  chk("lnote has hint", (document.getElementById("lnote").textContent || "").length > 0);
  // подменяем fetch для /api/ls и рендерим карточки хостов стенда
  const LSSTATUS = {
    status: { ok: true, ycsb: "yes", ui: "up", gen63: "down",
              targets: { direct: "up", proxy: "up" }, procs: [] },
    run: { state: "idle" },
    out: "",
    metrics: { ts: "11:50:12", cards: [
      { label: "na-61", ip: "10.228.147.61", hint: "MongoDB rs0",
        ok: true, error: "", role: "PRIMARY",
        units: { "mongod": "active" },
        extra: { disk: [{mount: "/", size_gb: 119.2, used_gb: 12.3, pct: 38}],
                 wt: {used: 1073741824, max: 23622320128, pct: 4.5},
                 io: {rs: 3.5, ws: 15.2, rmb: 0.4, wmb: 1.1, util: 6.8} },
        load: { l1: 0.86, l5: 0.99, l15: 0.96, cores: 24, cpu: 32.4,
                mem_used: 5740, mem_total: 48162, mem_pct: 11.9 } },
      { label: "na-62", ip: "10.228.147.62", hint: "visordb-proxy → na-61",
        ok: true, error: "", role: null,
        units: { "visordb-proxy": "active", "mongod": "active",
                 "beholder": "failed" },
        extra: { disk: [{mount: "/", size_gb: 30.4, used_gb: 12.1, pct: 42},
                        {mount: "/home", size_gb: 12.7, used_gb: 1.3, pct: 11}],
                 wt: {used: 5698458, max: 1509949440, pct: 0.4},
                 io: {rs: 12.3, ws: 45.6, rmb: 1.2, wmb: 2.0, util: 87.4} },
        load: { l1: 0.11, l5: 0.12, l15: 0.09, cores: 4, cpu: 3.1,
                mem_used: 1200, mem_total: 3965, mem_pct: 30.2 } },
      { label: "na-51", ip: "10.228.147.51", hint: "YCSB + Web UI :8080",
        ok: true, error: "", role: null,
        units: { "mongod": "active", "visordb-proxy": "active",
                 "beholder": "active" },
        extra: { disk: [{mount: "/", size_gb: 60.0, used_gb: 31.4, pct: 52}],
                 wt: {used: 536870912, max: 1509949440, pct: 35.7},
                 io: {rs: 150.2, ws: 820.5, rmb: 4.2, wmb: 33.1, util: 41.3} },
        load: { l1: 2.31, l5: 2.10, l15: 1.87, cores: 8, cpu: 47.5,
                mem_used: 9000, mem_total: 15700, mem_pct: 57 } },
      { label: "na-63", ip: "10.228.147.63",
        hint: "два YCSB-генератора (gen1→.61, gen2→.62)",
        ok: true, error: "", role: null,
        units: { "docker": "active", "ycsb-gen1": "inactive",
                 "ycsb-gen2": "active" },
        load: { l1: 0.03, l5: 0.04, l15: 0.01, cores: 4, cpu: 1.2,
                mem_used: 1300, mem_total: 3965, mem_pct: 33 },
        gen: { "ycsb-gen1": null,
               "ycsb-gen2": { ops: 24531.7, rt: 300123,
                              when: "08.10 14:32" } } },
    ]}
  };
  const realFetch = global.fetch;
  global.fetch = (url) => Promise.resolve({
    ok: true, json: async () => String(url).indexOf("/api/ls") >= 0 ? LSSTATUS : STATUS,
    text: async () => ""
  });
  await fetchLs();
  global.fetch = realFetch;
  const lg = document.getElementById("lgrid");
  chk("stand grid has 4 cards", lg.children.length === 4, lg.children.length);
  const cardL = lg.children[0].innerHTML;
  chk("card shows real stand host", cardL.indexOf("na-61") >= 0);
  chk("card shows host ip", cardL.indexOf("10.228.147.61") >= 0);
  chk("load value rendered", cardL.indexOf("0.86 / 0.99 / 0.96") >= 0);
  chk("core count rendered", cardL.indexOf("24 cores") >= 0);
  chk("cpu percent rendered", cardL.indexOf("32.4%") >= 0);
  chk("mem rendered with GB", cardL.indexOf("5.6 ГБ / 47.0 ГБ") >= 0,
      (cardL.match(/[0-9.]+ ГБ[^<]*/)||[""])[0]);
  chk("load bar fill present", cardL.indexOf("lfill") >= 0);
  chk("role [P] from card data", cardL.indexOf(">[P]<") >= 0);
  chk("services block header", cardL.indexOf("Сервисы") >= 0);
  chk("unit rendered", cardL.indexOf(">mongod<") >= 0);
  chk("RS/GO/SP on stand card", cardL.indexOf(">RS<") >= 0
      && cardL.indexOf(">GO<") >= 0 && cardL.indexOf(">SP<") >= 0);
  chk("stand buttons carry stand flag", cardL.indexOf("act('restart','na-61','mongod',1)") >= 0);
  chk("primsum hidden on load tab",
      document.getElementById("primsum").style.display === "none",
      document.getElementById("primsum").style.display);
  const card62 = lg.children[1].innerHTML;
  chk("62 units rendered", card62.indexOf(">beholder<") >= 0);
  chk("failed unit visible", card62.indexOf("failed") >= 0);
  chk("62 root disk rendered", card62.indexOf("12.1 / 30.4 ГБ (42%)") >= 0);
  chk("62 home disk row", card62.indexOf("1.3 / 12.7 ГБ (11%)") >= 0);
  chk("62 wt cache row", card62.indexOf("кэш монги") >= 0
      && card62.indexOf("5.4 МБ / 1.4 ГБ (0.4%)") >= 0);
  chk("62 disk io row", card62.indexOf("диск I/O") >= 0
      && card62.indexOf("r 12.3 / w 45.6 ops/s · 3.2 МБ/с · util 87.4%") >= 0);
  chk("61 disk row rendered", cardL.indexOf("12.3 / 119.2 ГБ (38%)") >= 0);
  chk("61 wt cache row", cardL.indexOf("1.0 ГБ / 22.0 ГБ (4.5%)") >= 0);
  chk("61 disk io row", cardL.indexOf("r 3.5 / w 15.2 ops/s · 1.5 МБ/с · util 6.8%") >= 0);
  const card51 = lg.children[2].innerHTML;
  chk("51 root disk row", card51.indexOf("31.4 / 60.0 ГБ (52%)") >= 0);
  chk("51 wt cache row", card51.indexOf("512.0 МБ / 1.4 ГБ (35.7%)") >= 0);
  chk("51 disk io row", card51.indexOf("r 150.2 / w 820.5 ops/s · 37.3 МБ/с · util 41.3%") >= 0);
  const card63 = lg.children[3].innerHTML;
  chk("63 load rendered", card63.indexOf("0.03 / 0.04 / 0.01") >= 0);
  chk("63 docker unit", card63.indexOf(">docker<") >= 0);
  chk("63 gen units", card63.indexOf(">ycsb-gen1<") >= 0
      && card63.indexOf(">ycsb-gen2<") >= 0);
  chk("63 gen buttons", card63.indexOf("act('start','na-63','ycsb-gen2'") >= 0);
  chk("63 last run stats", card63.indexOf("24.5k ops/s") >= 0
      && card63.indexOf("08.10 14:32") >= 0);
  chk("63 no-run hint", card63.indexOf("ещё не гоняли") >= 0);
  chk("63 off stub gone", card63.indexOf("выключен (VMware") < 0);
  chk("stand note timestamp", (document.getElementById("lnote").textContent || "").indexOf("11:50:12") >= 0);
  chk("stand status line rendered",
      (document.getElementById("lsStatus").innerHTML || "").indexOf("direct") >= 0);
  setTab("board");
  chk("back to board hides load", document.getElementById("pane-load").style.display === "none");
  console.log(fails.length === 0 ? "SMOKE_OK" : "SMOKE_FAIL count=" + fails.length);
  process.exit(fails.length);
})().catch(e => { console.log("SMOKE_CRASH " + (e && e.stack || e)); process.exit(9); });
`;
const STATUS_SUMMARY = JSON.stringify(RUNREC.summary);
const finalJs = js + "\nconst STATUS_SUMMARY = " + STATUS_SUMMARY + ";\n" + harness;
eval(finalJs);
