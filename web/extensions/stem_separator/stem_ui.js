import { app } from "/scripts/app.js";

app.registerExtension({
  name: "stem_separator.ui",
  async setup() {
    const panel = document.createElement("div");
    panel.id = "stem-sep-panel";
    panel.innerHTML = `
      <div class="stem-sep-title">Stem Separator</div>
      <div class="stem-sep-row"><span>Status:</span> <span id="stem-sep-status">…</span></div>
      <div class="stem-sep-row"><span>State:</span> <span id="stem-sep-state">…</span></div>
      <div class="stem-sep-row"><span>Active:</span> <span id="stem-sep-job">…</span></div>
      <div class="stem-sep-row"><span>Last error:</span> <span id="stem-sep-err">—</span></div>
      <div class="stem-sep-actions">
        <button id="stem-sep-unload">Unload Models</button>
        <button id="stem-sep-restart">Restart Worker</button>
      </div>
      <div class="stem-sep-hint">Phase 1: worker infra + stop/cancel + restart.</div>
    `;

    document.body.appendChild(panel);

    const $ = (id) => panel.querySelector(id);
    const statusEl = $("#stem-sep-status");
    const stateEl = $("#stem-sep-state");
    const jobEl = $("#stem-sep-job");
    const errEl = $("#stem-sep-err");
    const unloadBtn = $("#stem-sep-unload");
    const restartBtn = $("#stem-sep-restart");

    async function postJson(url, body) {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      return await r.json();
    }

    unloadBtn.onclick = async () => {
      unloadBtn.disabled = true;
      try {
        await postJson("/stem_separator/unload", {});
      } finally {
        unloadBtn.disabled = false;
      }
    };

    restartBtn.onclick = async () => {
      restartBtn.disabled = true;
      try {
        await postJson("/stem_separator/restart", {});
      } finally {
        restartBtn.disabled = false;
      }
    };

    async function poll() {
      try {
        const r = await fetch("/stem_separator/status");
        const j = await r.json();
        const s = j?.status || {};
        statusEl.textContent = s.alive ? "alive" : "dead";
        stateEl.textContent = s.state || "—";
        jobEl.textContent = s.active_job_id || "—";
        const le = s.last_error;
        errEl.textContent = le ? `${le.code || "ERR"}: ${le.message || ""}` : "—";

        // Basic disable logic
        const running = (s.state === "RUNNING" || s.state === "CANCELLING");
        unloadBtn.disabled = running; // policy: don't unload while running
      } catch (e) {
        statusEl.textContent = "error";
      } finally {
        setTimeout(poll, 1000);
      }
    }

    poll();
  },
});