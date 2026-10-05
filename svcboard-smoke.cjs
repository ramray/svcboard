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
  console.log(fails.length === 0 ? "SMOKE_OK" : "SMOKE_FAIL count=" + fails.length);
  process.exit(fails.length);
})().catch(e => { console.log("SMOKE_CRASH " + (e && e.stack || e)); process.exit(9); });
`;
const STATUS_SUMMARY = JSON.stringify(RUNREC.summary);
const finalJs = js + "\nconst STATUS_SUMMARY = " + STATUS_SUMMARY + ";\n" + harness;
eval(finalJs);
