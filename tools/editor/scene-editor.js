(function () {
  "use strict";

  const S = {
    ready: false,
    project: "",
    duration: 60,
    timeline: null,
    compositions: [],
    selectedCatalogId: null,
    selectedIndex: null,
    dirty: false,
    pps: 8,
    drag: null,
    warnings: [],
    issues: [],
    etag: "",
  };

  const sceneVid = $("sceneVid");
  const sceneTimeline = $("sceneTimeline");
  const sceneInner = $("sceneTimelineInner");
  const sceneStill = $("sceneStill");

  const esc = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const num = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const round3 = (value) => Math.round(num(value) * 1000) / 1000;
  const fmt = (seconds) => {
    const value = Math.max(0, num(seconds));
    return `${String(Math.floor(value / 60)).padStart(2, "0")}:${(value % 60).toFixed(2).padStart(5, "0")}`;
  };
  const activeScene = () => S.selectedIndex == null ? null : S.timeline.shots[S.selectedIndex] || null;
  const compById = (id) => S.compositions.find((item) => item.id === id);
  const groupOf = (comp) => comp.group || (comp.source ? comp.source.split("/").slice(-2, -1)[0] : "other") || "other";

  function switchWorkspace(name) {
    document.body.dataset.workspace = name;
    document.querySelectorAll(".cut-workspace").forEach((node) => node.classList.toggle("hidden", name !== "cut"));
    document.querySelectorAll(".scene-workspace").forEach((node) => node.classList.toggle("hidden", name !== "scenes"));
    document.querySelectorAll(".cut-only").forEach((node) => node.classList.toggle("hidden", name !== "cut"));
    document.querySelectorAll(".scene-only").forEach((node) => node.classList.toggle("hidden", name !== "scenes"));
    $("cutTab").classList.toggle("active", name === "cut");
    $("scenesTab").classList.toggle("active", name === "scenes");
    if (name === "scenes") {
      if (Number.isFinite(vid.currentTime)) sceneVid.currentTime = vid.currentTime;
      renderSceneTimeline();
    } else if (Number.isFinite(sceneVid.currentTime)) {
      seek(sceneVid.currentTime);
    }
  }

  function markSceneDirty(message, rerenderInspector = false) {
    S.dirty = true;
    $("sceneSaveBtn").disabled = false;
    setStatus(message || "scene timeline changed");
    renderSceneTimeline();
    if (rerenderInspector) renderSceneInspector();
  }

  function applyValidation() {
    const blocking = (S.issues || []).filter((item) => item.severity === "E");
    $("sceneBakeBtn").disabled = blocking.length > 0;
    $("sceneBakeBtn").title = blocking.length
      ? `${blocking.length} blocking validation issue${blocking.length === 1 ? "" : "s"}`
      : "Bake the current validated timeline";
    return blocking;
  }

  async function initScenes() {
    wireSceneEvents();
    try {
      const response = await fetch("/api/scenes");
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "could not load scene workspace");
      S.project = data.project;
      S.timeline = data.timeline;
      S.etag = data.etag || response.headers.get("ETag")?.replaceAll('"', "") || "";
      S.compositions = data.compositions || [];
      S.warnings = data.warnings || [];
      S.issues = data.issues || [];
      const maxEnd = Math.max(0, ...(S.timeline.shots || []).map((shot) => num(shot.master_out_s)));
      S.duration = Math.max(num(data.duration, 0), num(S.timeline.preview && S.timeline.preview.end_s, 0), maxEnd, 1);
      S.selectedCatalogId = S.compositions[0] ? S.compositions[0].id : null;
      S.ready = true;
      buildGroupFilter();
      renderCatalog();
      renderSceneTimeline();
      renderSceneInspector();
      $("sceneAddBtn").disabled = !S.selectedCatalogId;
      $("sceneCount").textContent = `${S.compositions.length} compositions · ${S.timeline.shots.length} placed scenes`;
      const blocking = applyValidation();
      if (blocking.length) setStatus(`${blocking.length} issue${blocking.length === 1 ? "" : "s"} blocking bake`, "err");
      else if (S.warnings.length) setStatus(S.warnings[0], "err");
      if (new URLSearchParams(location.search).get("workspace") === "scenes") switchWorkspace("scenes");
    } catch (error) {
      $("sceneCount").textContent = "Scene workspace could not load";
      $("sceneCatalog").innerHTML = `<div class="scene-warning">${esc(error.message)}</div>`;
      setStatus(error.message, "err");
    }
  }

  function buildGroupFilter() {
    const groups = [...new Set(S.compositions.map(groupOf))].sort();
    $("sceneGroup").innerHTML = '<option value="">All groups</option>' + groups
      .map((group) => `<option value="${esc(group)}">${esc(group)}</option>`).join("");
  }

  function filteredCompositions() {
    const query = $("sceneSearch").value.trim().toLowerCase();
    const group = $("sceneGroup").value;
    return S.compositions.filter((comp) => {
      const haystack = `${comp.id} ${groupOf(comp)} ${comp.source || ""}`.toLowerCase();
      return (!query || haystack.includes(query)) && (!group || groupOf(comp) === group);
    });
  }

  function renderCatalog() {
    if (!S.ready) return;
    const rows = filteredCompositions();
    $("sceneCount").textContent = `${rows.length} of ${S.compositions.length} compositions · ${S.timeline.shots.length} placed`;
    $("sceneCatalog").innerHTML = rows.map((comp) => `
      <article class="scene-card ${comp.id === S.selectedCatalogId ? "selected" : ""}" data-comp-id="${esc(comp.id)}" title="Double-click to add at the playhead">
        <div class="scene-card-id">${esc(comp.id)}</div>
        <div class="scene-card-meta">${num(comp.durationInSeconds).toFixed(1)}s · ${comp.width}×${comp.height} · ${comp.fps}fps
          ${comp.transparent ? '<span class="scene-badge alpha">alpha</span>' : '<span class="scene-badge">opaque</span>'}
        </div>
        <div class="scene-card-group">${esc(groupOf(comp))}</div>
      </article>`).join("") || '<div class="scene-muted">No matching compositions.</div>';
    document.querySelectorAll(".scene-card").forEach((card) => {
      card.onclick = () => {
        S.selectedCatalogId = card.dataset.compId;
        $("sceneAddBtn").disabled = false;
        renderCatalog();
      };
      card.ondblclick = () => addSceneAtPlayhead(card.dataset.compId);
    });
  }

  function addSceneAtPlayhead(id = S.selectedCatalogId) {
    const comp = compById(id);
    if (!comp || !S.timeline) return;
    const start = Math.max(0, Math.min(num(sceneVid.currentTime), S.duration - .1));
    const end = Math.min(S.duration, start + num(comp.durationInSeconds, 5));
    const shot = {
      id: comp.id,
      composition_id: comp.id,
      engine: "remotion",
      type: comp.transparent ? "overlay" : "cutaway",
      master_in_s: round3(start),
      master_out_s: round3(Math.max(start + .1, end)),
      enabled: true,
      status: "draft",
      cue: "",
      notes: "",
    };
    S.timeline.shots.push(shot);
    S.timeline.shots.sort((a, b) => num(a.master_in_s) - num(b.master_in_s));
    S.selectedIndex = S.timeline.shots.indexOf(shot);
    clearSceneStill();
    markSceneDirty(`added ${comp.id} at ${fmt(start)}`, true);
    renderCatalog();
  }

  function overlappingIndexes() {
    const overlap = new Set();
    const shots = S.timeline ? S.timeline.shots : [];
    for (let i = 0; i < shots.length; i++) {
      if (shots[i].enabled === false) continue;
      for (let j = i + 1; j < shots.length; j++) {
        if (shots[j].enabled === false || shots[i].type !== shots[j].type) continue;
        if (num(shots[i].master_in_s) < num(shots[j].master_out_s) && num(shots[j].master_in_s) < num(shots[i].master_out_s)) {
          overlap.add(i); overlap.add(j);
        }
      }
    }
    return overlap;
  }

  function renderSceneTimeline() {
    if (!S.ready || !S.timeline) return;
    const width = Math.max(S.duration * S.pps, sceneTimeline.clientWidth || 0);
    sceneInner.style.width = `${width}px`;
    sceneInner.querySelectorAll(".scene-block,.scene-tick,.scene-tick-label").forEach((node) => node.remove());
    const overlaps = overlappingIndexes();
    const step = S.pps >= 18 ? 5 : (S.pps >= 8 ? 10 : 30);
    for (let at = 0; at <= S.duration; at += step) {
      const tick = document.createElement("div");
      tick.className = `scene-tick${at % 60 === 0 ? " major" : ""}`;
      tick.style.left = `${at * S.pps}px`;
      $("sceneRuler").appendChild(tick);
      if (at % 60 === 0) {
        const label = document.createElement("div");
        label.className = "scene-tick-label";
        label.style.left = `${at * S.pps}px`;
        label.textContent = `${at / 60}:00`;
        $("sceneRuler").appendChild(label);
      }
    }
    S.timeline.shots.forEach((shot, index) => {
      const block = document.createElement("div");
      block.className = `scene-block ${shot.type === "overlay" ? "overlay" : "cutaway"}`
        + (shot.enabled === false ? " disabled" : "")
        + (S.selectedIndex === index ? " selected" : "")
        + (overlaps.has(index) ? " overlap" : "");
      block.dataset.sceneIndex = index;
      block.style.left = `${num(shot.master_in_s) * S.pps}px`;
      block.style.width = `${Math.max((num(shot.master_out_s) - num(shot.master_in_s)) * S.pps, 5)}px`;
      block.title = `${shot.id}\n${fmt(shot.master_in_s)} - ${fmt(shot.master_out_s)}${overlaps.has(index) ? "\nOVERLAPS ANOTHER ACTIVE SCENE" : ""}`;
      block.innerHTML = `<span class="scene-edge left"></span><strong>${esc(shot.id)}</strong><small>${esc(shot.engine || "remotion")} · ${(num(shot.master_out_s) - num(shot.master_in_s)).toFixed(2)}s</small><span class="scene-edge right"></span>`;
      sceneInner.appendChild(block);
    });
    updateScenePlayhead();
  }

  function renderProjectSettings() {
    const preview = S.timeline.preview || (S.timeline.preview = {});
    return `
      <div class="scene-field"><label>Master video path</label><input id="sceneMaster" value="${esc(S.timeline.master || "")}"></div>
      <div class="scene-field-grid">
        <div class="scene-field"><label>Preview end (s)</label><input id="scenePreviewEnd" type="number" min="0.1" step="0.1" value="${num(preview.end_s, S.duration)}"></div>
        <div class="scene-field"><label>Preview fps</label><input id="scenePreviewFps" type="number" min="1" step="1" value="${num(preview.fps, 30)}"></div>
      </div>
      <div class="scene-field"><label>Preview output</label><input id="scenePreviewOut" value="${esc(preview.out || "")}"></div>`;
  }

  function wireProjectSettings() {
    const preview = S.timeline.preview;
    $("sceneMaster").oninput = (event) => { S.timeline.master = event.target.value; markSceneDirty("master path changed"); };
    $("scenePreviewEnd").onchange = (event) => {
      preview.end_s = Math.max(.1, num(event.target.value, S.duration));
      S.duration = Math.max(S.duration, preview.end_s);
      markSceneDirty("preview end changed");
    };
    $("scenePreviewFps").onchange = (event) => { preview.fps = Math.max(1, Math.round(num(event.target.value, 30))); markSceneDirty("preview fps changed"); };
    $("scenePreviewOut").oninput = (event) => { preview.out = event.target.value; markSceneDirty("preview output changed"); };
  }

  function renderSceneInspector() {
    if (!S.ready || !S.timeline) return;
    const shot = activeScene();
    if (!shot) {
      $("sceneInspectorBody").className = "scene-inspector-form";
      $("sceneInspectorBody").innerHTML = `<h3 style="margin-bottom:12px">Bake settings</h3>${renderProjectSettings()}`;
      wireProjectSettings();
      $("sceneFrameBtn").disabled = true;
      $("sceneRenderBtn").disabled = true;
      return;
    }
    const engine = shot.engine || "remotion";
    const comp = compById(shot.id);
    const opaqueWarning = shot.type === "overlay" && engine === "remotion" && comp && !comp.transparent;
    const noAssetWarning = engine !== "remotion" && shot.enabled !== false && !shot.asset;
    const remotionOptions = S.compositions.map((item) => `<option value="${esc(item.id)}" ${item.id === shot.id ? "selected" : ""}>${esc(item.id)}</option>`).join("");
    $("sceneInspectorBody").className = "scene-inspector-form";
    $("sceneInspectorBody").innerHTML = `
      <div class="scene-field-grid">
        <div class="scene-field"><label>Engine</label><select id="sceneEngine">
          <option value="remotion" ${engine === "remotion" ? "selected" : ""}>Remotion</option>
          <option value="fable" ${engine === "fable" ? "selected" : ""}>Fable</option>
          <option value="hyperframe" ${engine === "hyperframe" ? "selected" : ""}>Hyperframe</option>
          <option value="media" ${engine === "media" ? "selected" : ""}>Media file</option>
        </select></div>
        <div class="scene-field"><label>Layer</label><select id="sceneType">
          <option value="cutaway" ${shot.type === "cutaway" ? "selected" : ""}>Cutaway</option>
          <option value="overlay" ${shot.type === "overlay" ? "selected" : ""}>Overlay</option>
        </select></div>
      </div>
      <div class="scene-field" id="sceneRemotionIdWrap" ${engine !== "remotion" ? 'style="display:none"' : ""}><label>Composition</label><select id="sceneRemotionId">${remotionOptions}</select></div>
      <div class="scene-field" id="sceneExternalIdWrap" ${engine === "remotion" ? 'style="display:none"' : ""}><label>Placement id</label><input value="${esc(shot.scene_uid || "assigned when saved")}" readonly></div>
      <div class="scene-field" id="sceneAssetWrap" ${engine === "remotion" ? 'style="display:none"' : ""}><label>Active take</label><input value="${esc(shot.asset || "Import a take to enable this scene")}" readonly></div>
      <div class="scene-field-grid">
        <div class="scene-field"><label>Start (s)</label><input id="sceneStart" type="number" min="0" step="0.01" value="${num(shot.master_in_s)}"></div>
        <div class="scene-field"><label>End (s)</label><input id="sceneEnd" type="number" min="0.05" step="0.01" value="${num(shot.master_out_s)}"></div>
      </div>
      <div class="scene-field-grid">
        <div class="scene-field"><label>Status</label><select id="sceneStatus">
          ${["planned", "generating", "draft", "approved"].map((value) => `<option value="${value}" ${shot.status === value ? "selected" : ""}>${value}</option>`).join("")}
        </select></div>
        <div class="scene-field"><span class="scene-label">Bake</span><label class="scene-check"><input id="sceneEnabled" type="checkbox" ${shot.enabled !== false ? "checked" : ""}> Enabled</label></div>
      </div>
      ${opaqueWarning ? '<div class="scene-warning">This composition is opaque. Use it as a cutaway or author an alpha-enabled Remotion shot.</div>' : ""}
      ${noAssetWarning ? '<div class="scene-warning">This external scene needs a rendered asset path before it can bake.</div>' : ""}
      <div class="scene-field"><label>Narration cue / intent</label><textarea id="sceneCue" placeholder="What the viewer should understand at this beat">${esc(shot.cue || "")}</textarea></div>
      <div class="scene-field"><label>Change notes</label><textarea id="sceneNotes" placeholder="Describe the revision you want for Remotion or Hyperframe">${esc(shot.notes || "")}</textarea></div>
      <div class="scene-field"><span class="scene-label">Source</span><div class="scene-source">${esc(engine === "remotion" && comp && comp.source ? `remotion/${comp.source}` : shot.asset || "No external render mapped yet")}</div></div>
      <div class="scene-actions">
        <button id="sceneJumpStart">Jump to start</button>
        <button id="sceneDuplicate">Duplicate</button>
        <button id="sceneDelete" class="danger">Delete</button>
      </div>
      <hr style="border:0;border-top:1px solid #343a45;margin:18px 0 13px">
      <h3 style="margin-bottom:12px">Bake settings</h3>
      ${renderProjectSettings()}`;

    const update = (message, rerender = false) => markSceneDirty(message, rerender);
    $("sceneEngine").onchange = (event) => {
      shot.engine = event.target.value;
      if (shot.engine === "remotion") {
        shot.id = S.selectedCatalogId || S.compositions[0]?.id || shot.id;
        shot.composition_id = shot.id;
        shot.enabled = true;
        shot.status = "draft";
      } else {
        delete shot.composition_id;
        shot.enabled = false;
        shot.status = "planned";
      }
      update(`changed engine to ${shot.engine}`, true);
    };
    $("sceneType").onchange = (event) => { shot.type = event.target.value; update(`changed ${shot.id} layer`, true); };
    $("sceneRemotionId").onchange = (event) => {
      shot.id = event.target.value;
      shot.composition_id = shot.id;
      const replacement = compById(shot.id);
      if (replacement) shot.master_out_s = round3(num(shot.master_in_s) + num(replacement.durationInSeconds, 5));
      update(`changed composition to ${shot.id}`, true);
    };
    $("sceneStart").onchange = (event) => {
      const duration = Math.max(.05, num(shot.master_out_s) - num(shot.master_in_s));
      shot.master_in_s = Math.max(0, round3(event.target.value));
      shot.master_out_s = round3(shot.master_in_s + duration);
      update(`retimed ${shot.id}`, true);
    };
    $("sceneEnd").onchange = (event) => { shot.master_out_s = Math.max(num(shot.master_in_s) + .05, round3(event.target.value)); update(`retimed ${shot.id}`, true); };
    $("sceneStatus").onchange = (event) => { shot.status = event.target.value; update(`marked ${shot.id} ${shot.status}`); };
    $("sceneEnabled").onchange = (event) => { shot.enabled = event.target.checked; update(`${shot.enabled ? "enabled" : "disabled"} ${shot.id}`, true); };
    $("sceneCue").oninput = (event) => { shot.cue = event.target.value; S.dirty = true; $("sceneSaveBtn").disabled = false; };
    $("sceneNotes").oninput = (event) => { shot.notes = event.target.value; S.dirty = true; $("sceneSaveBtn").disabled = false; };
    $("sceneJumpStart").onclick = () => { sceneVid.currentTime = num(shot.master_in_s); updateScenePlayhead(); };
    $("sceneDuplicate").onclick = () => duplicateScene();
    $("sceneDelete").onclick = () => deleteScene();
    wireProjectSettings();
    $("sceneFrameBtn").disabled = engine !== "remotion";
    $("sceneRenderBtn").disabled = engine !== "remotion";
    updateSceneFrameLabel();
  }

  function duplicateScene() {
    const shot = activeScene();
    if (!shot) return;
    const duration = num(shot.master_out_s) - num(shot.master_in_s);
    const copy = JSON.parse(JSON.stringify(shot));
    delete copy.scene_uid;
    if (copy.engine !== "remotion") {
      copy.takes = [];
      copy.active_take_uid = null;
      delete copy.asset;
    }
    copy.master_in_s = round3(num(shot.master_out_s));
    copy.master_out_s = round3(copy.master_in_s + duration);
    copy.status = copy.status === "approved" ? "draft" : copy.status;
    S.timeline.shots.splice(S.selectedIndex + 1, 0, copy);
    S.selectedIndex += 1;
    markSceneDirty(`duplicated ${shot.id}`, true);
  }

  function deleteScene() {
    const shot = activeScene();
    if (!shot) return;
    S.timeline.shots.splice(S.selectedIndex, 1);
    S.selectedIndex = null;
    clearSceneStill();
    markSceneDirty(`deleted ${shot.id}`, true);
    renderCatalog();
  }

  async function saveScenes() {
    if (!S.timeline) return false;
    setStatus("validating scene timeline...");
    const response = await fetch("/api/scenes/save", {
      method: "POST", headers: {"Content-Type": "application/json", "If-Match": S.etag},
      body: JSON.stringify({timeline: S.timeline}),
    });
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 409 && data.code === "E_ETAG_MISMATCH") {
        S.etag = data.current_etag || S.etag;
        setStatus("Timeline changed in another session. Reload before saving so no work is overwritten.", "err");
        $("sceneJobLog").textContent = JSON.stringify(data, null, 2);
        return false;
      }
      const detail = (data.errors || [data.error || "save failed"]).join("; ");
      setStatus(detail, "err");
      $("sceneJobLog").textContent = JSON.stringify(data, null, 2);
      return false;
    }
    S.timeline = data.timeline;
    S.etag = data.etag || response.headers.get("ETag")?.replaceAll('"', "") || S.etag;
    S.issues = data.issues || [];
    S.dirty = false;
    $("sceneSaveBtn").disabled = true;
    setStatus(`scene timeline saved${data.backup ? " · backup created" : ""}`, "ok");
    if (data.warnings && data.warnings.length) $("sceneJobLog").textContent = data.warnings.join("\n");
    renderSceneTimeline();
    applyValidation();
    return true;
  }

  function localFrameForScene(shot) {
    const comp = compById(shot.id);
    if (!comp) return 0;
    const localSeconds = Math.max(0, Math.min(num(sceneVid.currentTime) - num(shot.master_in_s), num(comp.durationInSeconds)));
    return Math.max(0, Math.min(Math.round(localSeconds * num(comp.fps, 30)), Math.round(num(comp.durationInSeconds) * num(comp.fps, 30)) - 1));
  }

  function updateSceneFrameLabel() {
    const shot = activeScene();
    if (!shot || shot.engine !== "remotion") {
      $("sceneFrameLabel").textContent = "";
      return;
    }
    const comp = compById(shot.id);
    $("sceneFrameLabel").textContent = comp ? `${shot.id} · frame ${localFrameForScene(shot)} / ${Math.round(num(comp.durationInSeconds) * num(comp.fps)) - 1}` : shot.id;
  }

  async function renderCurrentFrame() {
    const shot = activeScene();
    if (!shot || shot.engine !== "remotion") return;
    const frame = localFrameForScene(shot);
    setStatus(`rendering ${shot.id} frame ${frame}...`);
    const response = await fetch("/api/scenes/still", {
      method: "POST", headers: {"Content-Type": "application/json", "If-Match": S.etag},
      body: JSON.stringify({id: shot.id, frame}),
    });
    const data = await response.json();
    if (!response.ok) { setStatus(data.error || "still render failed", "err"); return; }
    pollSceneJob("still", () => {
      sceneStill.src = `${data.url}?v=${Date.now()}`;
      sceneStill.className = shot.type === "overlay" ? "overlay-preview" : "cutaway-preview";
      $("sceneEmptyPreview").classList.add("hidden");
      $("sceneClearPreviewBtn").disabled = false;
    });
  }

  async function renderSelectedScene() {
    const shot = activeScene();
    if (!shot || shot.engine !== "remotion") return setStatus("select a Remotion scene first", "err");
    if (S.dirty && !(await saveScenes())) return;
    const response = await fetch("/api/scenes/render", {
      method: "POST", headers: {"Content-Type": "application/json", "If-Match": S.etag},
      body: JSON.stringify({id: shot.id, scale: 1}),
    });
    const data = await response.json();
    if (!response.ok) return setStatus(data.error || "render could not start", "err");
    setStatus(`rendering ${shot.id}...`);
    pollSceneJob("render");
  }

  async function bakeScenePreview() {
    if (!(await saveScenes())) return;
    const response = await fetch("/api/scenes/bake", {method: "POST", headers: {"Content-Type": "application/json", "If-Match": S.etag}, body: "{}"});
    const data = await response.json();
    if (!response.ok) return setStatus(data.error || "bake could not start", "err");
    setStatus("baking full scene preview...");
    pollSceneJob("bake");
  }

  function pollSceneJob(kind, onSuccess) {
    const timer = setInterval(async () => {
      const response = await fetch("/api/scenes/jobs");
      const jobs = await response.json();
      const job = jobs[kind];
      const tail = (job.log || "").trim().split("\n").slice(-8).join("\n");
      $("sceneJobLog").textContent = tail || `${kind} job running...`;
      $("sceneJobLog").scrollTop = $("sceneJobLog").scrollHeight;
      if (job.running) return;
      clearInterval(timer);
      if (job.ok) {
        if (onSuccess) onSuccess(job);
        const suffix = kind === "bake" && job.output ? ` · ${job.output}` : "";
        setStatus(`${kind} complete${suffix}`, "ok");
      } else {
        setStatus(`${kind} failed - see job log`, "err");
      }
    }, 900);
  }

  function clearSceneStill() {
    sceneStill.removeAttribute("src");
    sceneStill.className = "hidden";
    $("sceneEmptyPreview").classList.remove("hidden");
    $("sceneClearPreviewBtn").disabled = true;
  }

  function updateScenePlayhead() {
    const time = Math.max(0, num(sceneVid.currentTime));
    $("scenePlayhead").style.left = `${time * S.pps}px`;
    $("sceneTimeinfo").textContent = `${fmt(time)} · ${S.project || "scene timeline"}`;
    updateSceneFrameLabel();
    if (!sceneVid.paused && document.body.dataset.workspace === "scenes") {
      const x = time * S.pps - sceneTimeline.scrollLeft;
      if (x > sceneTimeline.clientWidth * .85 || x < 0) sceneTimeline.scrollLeft = time * S.pps - sceneTimeline.clientWidth * .15;
    }
  }

  function wireSceneEvents() {
    $("cutTab").onclick = () => switchWorkspace("cut");
    $("scenesTab").onclick = () => switchWorkspace("scenes");
    $("sceneSearch").oninput = renderCatalog;
    $("sceneGroup").onchange = renderCatalog;
    $("sceneAddBtn").onclick = () => addSceneAtPlayhead();
    $("sceneSaveBtn").onclick = saveScenes;
    $("sceneRenderBtn").onclick = renderSelectedScene;
    $("sceneBakeBtn").onclick = bakeScenePreview;
    $("sceneFrameBtn").onclick = renderCurrentFrame;
    $("sceneClearPreviewBtn").onclick = clearSceneStill;
    $("scenePlayBtn").onclick = () => sceneVid.paused ? sceneVid.play() : sceneVid.pause();
    $("sceneZoom").oninput = (event) => {
      const center = num(sceneVid.currentTime);
      S.pps = num(event.target.value, 8);
      renderSceneTimeline();
      sceneTimeline.scrollLeft = center * S.pps - sceneTimeline.clientWidth / 2;
    };
    sceneVid.addEventListener("timeupdate", updateScenePlayhead);

    sceneInner.addEventListener("mousedown", (event) => {
      const block = event.target.closest(".scene-block");
      if (block) {
        const index = Number(block.dataset.sceneIndex);
        const shot = S.timeline.shots[index];
        S.selectedIndex = index;
        const side = event.target.classList.contains("left") ? "left" : (event.target.classList.contains("right") ? "right" : "move");
        S.drag = {
          index, side, originX: event.clientX,
          start: num(shot.master_in_s), end: num(shot.master_out_s), moved: false,
        };
        clearSceneStill();
        renderSceneTimeline();
        renderSceneInspector();
        event.preventDefault();
        return;
      }
      const rect = sceneInner.getBoundingClientRect();
      sceneVid.currentTime = Math.max(0, Math.min((event.clientX - rect.left) / S.pps, S.duration));
      updateScenePlayhead();
    });

    window.addEventListener("mousemove", (event) => {
      if (!S.drag || !S.timeline) return;
      const shot = S.timeline.shots[S.drag.index];
      if (!shot) return;
      const delta = (event.clientX - S.drag.originX) / S.pps;
      if (S.drag.side === "left") shot.master_in_s = round3(Math.max(0, Math.min(S.drag.start + delta, num(shot.master_out_s) - .05)));
      else if (S.drag.side === "right") shot.master_out_s = round3(Math.max(num(shot.master_in_s) + .05, S.drag.end + delta));
      else {
        const duration = S.drag.end - S.drag.start;
        shot.master_in_s = round3(Math.max(0, Math.min(S.drag.start + delta, Math.max(0, S.duration - duration))));
        shot.master_out_s = round3(shot.master_in_s + duration);
      }
      S.drag.moved = true;
      renderSceneTimeline();
    });

    window.addEventListener("mouseup", () => {
      if (!S.drag) return;
      if (S.drag.moved) markSceneDirty(`retimed ${S.timeline.shots[S.drag.index].id}`, true);
      S.drag = null;
    });

    document.addEventListener("keydown", (event) => {
      if (document.body.dataset.workspace !== "scenes") return;
      if (["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) return;
      if (event.key === " ") { event.preventDefault(); sceneVid.paused ? sceneVid.play() : sceneVid.pause(); }
      else if (event.key === "ArrowLeft") sceneVid.currentTime = Math.max(0, sceneVid.currentTime - (event.shiftKey ? 10 : 2));
      else if (event.key === "ArrowRight") sceneVid.currentTime = Math.min(S.duration, sceneVid.currentTime + (event.shiftKey ? 10 : 2));
      else if (event.key === ",") sceneVid.currentTime = Math.max(0, sceneVid.currentTime - 1 / 60);
      else if (event.key === ".") sceneVid.currentTime = Math.min(S.duration, sceneVid.currentTime + 1 / 60);
      else if (event.key.toLowerCase() === "a") addSceneAtPlayhead();
    });

    window.addEventListener("beforeunload", (event) => { if (S.dirty) event.preventDefault(); });
  }

  initScenes();
})();
