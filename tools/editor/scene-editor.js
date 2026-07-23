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
    previewTakeUid: null,
    compare: null,
    takesOpen: true,
    importing: false,
    currentJob: null,
    jobPollTimer: null,
    wordTicks: [],
  };

  const sceneVid = $("sceneVid");
  const sceneTimeline = $("sceneTimeline");
  const sceneInner = $("sceneTimelineInner");
  const sceneStill = $("sceneStill");
  const sceneTakePreview = $("sceneTakePreview");
  let cutInitPromise = null;

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
  const activeTake = (scene) => (scene && scene.takes || []).find((take) => take.take_uid === scene.active_take_uid) || null;
  const compById = (id) => S.compositions.find((item) => item.id === id);
  const groupOf = (comp) => comp.group || (comp.source ? comp.source.split("/").slice(-2, -1)[0] : "other") || "other";

  async function switchWorkspace(name) {
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
    } else if (Number.isFinite(playbackMasterTime())) {
      if (!DATA) {
        if (!cutInitPromise) {
          cutInitPromise = init().finally(() => {
            if (!DATA) cutInitPromise = null;
          });
        }
        try {
          await cutInitPromise;
        } catch (error) {
          cutInitPromise = null;
          setStatus(`cut workspace failed to load: ${error.message}`, "err");
          return;
        }
        if (!DATA) return;
      }
      seek(playbackMasterTime());
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

  function takeMediaUrl(scene, takeUid) {
    const token = encodeURIComponent(window.workbenchToken || "");
    return `/media/take/${encodeURIComponent(scene.scene_uid)}/${encodeURIComponent(takeUid)}?token=${token}`;
  }

  function jobMediaUrl(jobId) {
    const token = encodeURIComponent(window.workbenchToken || "");
    return `/media/job/${encodeURIComponent(jobId)}?token=${token}`;
  }

  function bakedPreviewVisible() {
    return !sceneTakePreview.classList.contains("hidden") && Boolean(sceneTakePreview.dataset.jobId);
  }

  function activePlaybackVideo() {
    return bakedPreviewVisible() ? sceneTakePreview : sceneVid;
  }

  function playbackMasterTime() {
    if (!bakedPreviewVisible()) return Math.max(0, num(sceneVid.currentTime));
    return Math.max(0, num(sceneTakePreview.dataset.rangeFrom) + num(sceneTakePreview.currentTime));
  }

  function seekActivePlayback(masterTime) {
    const target = Math.max(0, num(masterTime));
    if (bakedPreviewVisible()) {
      const fromS = num(sceneTakePreview.dataset.rangeFrom);
      const toS = num(sceneTakePreview.dataset.rangeTo, fromS + num(sceneTakePreview.duration));
      sceneTakePreview.currentTime = Math.max(0, Math.min(target, toS) - fromS);
    } else {
      sceneVid.currentTime = Math.min(target, S.duration);
    }
  }

  function applyServerTimeline(data, selectedSceneUid) {
    if (!data.timeline) return;
    S.timeline = data.timeline;
    S.etag = data.etag || S.etag;
    S.issues = data.issues || S.issues;
    S.warnings = data.warnings || [];
    S.selectedIndex = S.timeline.shots.findIndex((scene) => scene.scene_uid === selectedSceneUid);
    if (S.selectedIndex < 0) S.selectedIndex = null;
    S.dirty = false;
    $("sceneSaveBtn").disabled = true;
    renderCatalog();
    renderSceneTimeline();
    renderSceneInspector();
    applyValidation();
  }

  async function refreshSceneData(selectedSceneUid) {
    const response = await fetch("/api/scenes");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "could not refresh scenes");
    applyServerTimeline(data, selectedSceneUid);
    return data;
  }

  function showTakePreview(takeUid) {
    const scene = activeScene();
    const take = (scene && scene.takes || []).find((item) => item.take_uid === takeUid);
    if (!scene || !take) return;
    S.previewTakeUid = takeUid;
    sceneVid.pause();
    sceneTakePreview.muted = true;
    sceneTakePreview.removeAttribute("data-job-id");
    sceneTakePreview.removeAttribute("data-range-from");
    sceneTakePreview.removeAttribute("data-range-to");
    sceneStill.removeAttribute("src");
    sceneStill.className = "hidden";
    const nextUrl = takeMediaUrl(scene, takeUid);
    if (sceneTakePreview.dataset.takeUid !== takeUid) {
      const previousTime = Number.isFinite(sceneTakePreview.currentTime) ? sceneTakePreview.currentTime : 0;
      sceneTakePreview.src = nextUrl;
      sceneTakePreview.dataset.takeUid = takeUid;
      sceneTakePreview.addEventListener("loadedmetadata", () => {
        if (sceneTakePreview.dataset.takeUid !== takeUid) return;
        const maxTime = Number.isFinite(sceneTakePreview.duration) ? Math.max(0, sceneTakePreview.duration - 0.01) : previousTime;
        sceneTakePreview.currentTime = Math.min(previousTime, maxTime);
      }, {once: true});
      sceneTakePreview.load();
    }
    sceneTakePreview.classList.remove("hidden");
    $("sceneEmptyPreview").classList.add("hidden");
    $("sceneClearPreviewBtn").disabled = false;
    renderTakesDrawer();
  }

  function renderCompareControls() {
    const controls = $("sceneCompareControls");
    if (!S.compare) {
      controls.classList.add("hidden");
      return;
    }
    controls.classList.remove("hidden");
    $("sceneCompareLabel").textContent = `Compare ${S.compare.a.slice(-6)} / ${S.compare.b.slice(-6)}`;
    controls.querySelectorAll("[data-compare-side]").forEach((button) => {
      button.classList.toggle("active", button.dataset.compareSide === S.compare.side);
    });
  }

  function showCompareSide(side) {
    if (!S.compare) return;
    S.compare.side = side;
    showTakePreview(side === "a" ? S.compare.a : S.compare.b);
    renderCompareControls();
  }

  function startCompare(takeUid) {
    const scene = activeScene();
    const current = activeTake(scene);
    if (!scene || !current || current.take_uid === takeUid) {
      showTakePreview(takeUid);
      setStatus(current ? "previewing active take" : "previewing candidate; promote a take to enable A/B");
      return;
    }
    S.compare = {a: current.take_uid, b: takeUid, side: "b"};
    showCompareSide("b");
  }

  function closeCompare() {
    const scene = activeScene();
    const current = activeTake(scene);
    S.compare = null;
    renderCompareControls();
    if (current) showTakePreview(current.take_uid);
  }

  function syncScenePreview() {
    if (S.compare) {
      showCompareSide(S.compare.side);
      return;
    }
    const current = activeTake(activeScene());
    if (current) showTakePreview(current.take_uid);
    else clearSceneStill();
  }

  function renderTakesDrawer() {
    const drawer = $("sceneTakesDrawer");
    const strip = $("sceneTakeStrip");
    const scene = activeScene();
    drawer.classList.toggle("open", Boolean(scene && S.takesOpen));
    if (!scene) {
      strip.innerHTML = "";
      $("sceneTakesSummary").textContent = "";
      $("sceneImportState").textContent = "";
      return;
    }
    const takes = Array.isArray(scene.takes) ? scene.takes : [];
    $("sceneTakesSummary").textContent = `${takes.length} take${takes.length === 1 ? "" : "s"}${scene.active_take_uid ? ` · active ${scene.active_take_uid.slice(-6)}` : " · none active"}`;
    $("sceneImportState").textContent = S.importing ? "Uploading and conforming…" : "";
    const cards = takes.map((take) => {
      const provenance = take.provenance || {};
      const provider = provenance.provider || scene.engine || "media";
      const probe = take.probe || {};
      const active = take.take_uid === scene.active_take_uid;
      const note = provenance.note || (provenance.spec && provenance.spec.original_filename) || "Immutable take";
      const meta = [
        Number.isFinite(Number(probe.dur_s)) ? `${Number(probe.dur_s).toFixed(2)}s` : "",
        probe.w && probe.h ? `${probe.w}×${probe.h}` : "",
        take.cost_usd != null ? `$${Number(take.cost_usd).toFixed(2)}` : "",
      ].filter(Boolean).join(" · ");
      return `<article class="scene-take-card${active ? " active" : " candidate"}${S.previewTakeUid === take.take_uid ? " previewing" : ""}" data-take-uid="${esc(take.take_uid)}">
        <div class="scene-take-title"><i class="scene-engine-dot ${esc(provider)}"></i><strong>${esc(take.take_uid)}</strong></div>
        ${active ? '<span class="scene-take-badge">ACTIVE</span>' : ""}
        <div class="scene-take-meta">${esc(meta || take.conform_profile || "take")}</div>
        <div class="scene-take-note" title="${esc(note)}">${esc(note)}</div>
        <div class="scene-take-actions">
          <button type="button" data-take-action="preview">Preview</button>
          ${!active && scene.active_take_uid ? '<button type="button" data-take-action="compare">Compare</button>' : ""}
          ${!active ? '<button type="button" class="promote" data-take-action="promote">Promote</button>' : ""}
        </div>
      </article>`;
    }).join("");
    strip.innerHTML = `${cards}<button type="button" class="scene-take-import${S.importing ? " busy" : ""}" id="sceneTakeImportCard" ${S.importing ? "disabled" : ""}>＋<br>Import take<br><span class="scene-muted">source is preserved</span></button>`;
    strip.querySelectorAll(".scene-take-card").forEach((card) => {
      card.onclick = (event) => {
        const action = event.target.closest("[data-take-action]")?.dataset.takeAction;
        const takeUid = card.dataset.takeUid;
        if (action === "compare") startCompare(takeUid);
        else if (action === "promote") promoteTake(takeUid);
        else showTakePreview(takeUid);
      };
    });
    $("sceneTakeImportCard").onclick = () => {
      if (!S.importing) $("sceneTakeInput").click();
    };
  }

  async function importTake(file) {
    if (!file || S.importing) return;
    let scene = activeScene();
    if (!scene) return;
    if (S.dirty && !(await saveScenes())) return;
    scene = activeScene();
    if (!scene || !scene.scene_uid) return setStatus("save the scene before importing a take", "err");
    const sceneUid = scene.scene_uid;
    const form = new FormData();
    form.append("file", file, file.name);
    if (scene.notes) form.append("note", scene.notes);
    form.append("class_hint", scene.type || "cutaway");
    S.importing = true;
    renderTakesDrawer();
    setStatus(`importing ${file.name}…`);
    try {
      const response = await fetch(`/api/scene/${encodeURIComponent(sceneUid)}/takes/import`, {
        method: "POST", headers: {"If-Match": S.etag}, body: form,
      });
      const data = await response.json();
      if (!response.ok) {
        $("sceneJobLog").textContent = JSON.stringify(data, null, 2);
        if (response.status === 409 && data.code === "E_ETAG_MISMATCH") {
          return setStatus("Timeline changed during import. Reload before continuing so no work is overwritten.", "err");
        }
        return setStatus(data.error || "take import failed", "err");
      }
      applyServerTimeline(data, sceneUid);
      showTakePreview(data.take.take_uid);
      setStatus(data.deduped ? "identical take already exists · reused" : "take imported · source preserved · ready to promote", "ok");
    } catch (error) {
      setStatus(`take import failed: ${error.message}`, "err");
    } finally {
      S.importing = false;
      $("sceneTakeInput").value = "";
      renderTakesDrawer();
    }
  }

  async function promoteTake(takeUid) {
    let scene = activeScene();
    if (!scene) return;
    if (S.dirty && !(await saveScenes())) return;
    scene = activeScene();
    if (!scene || !scene.scene_uid) return;
    const sceneUid = scene.scene_uid;
    setStatus(`promoting ${takeUid.slice(-6)}…`);
    const response = await fetch(`/api/scene/${encodeURIComponent(sceneUid)}/takes/${encodeURIComponent(takeUid)}/promote`, {
      method: "POST", headers: {"Content-Type": "application/json", "If-Match": S.etag}, body: "{}",
    });
    const data = await response.json();
    if (!response.ok) {
      $("sceneJobLog").textContent = JSON.stringify(data, null, 2);
      if (response.status === 409 && data.code === "E_ETAG_MISMATCH") {
        return setStatus("Timeline changed before promotion. Reload before continuing so no work is overwritten.", "err");
      }
      return setStatus(data.error || "take could not be promoted", "err");
    }
    S.compare = null;
    applyServerTimeline(data, sceneUid);
    showTakePreview(takeUid);
    renderCompareControls();
    setStatus("take promoted · timeline asset updated", "ok");
  }

  function openGenerateDialog() {
    const shot = activeScene();
    if (!shot || !["fable", "hyperframe"].includes(shot.engine)) return;
    $("sceneGeneratePrompt").value = shot.notes || shot.cue || "";
    $("sceneGenerateDuration").value = round3(Math.max(.05, num(shot.master_out_s) - num(shot.master_in_s)));
    $("sceneGenerateProvider").textContent = `· ${shot.engine}`;
    $("sceneGenerateDialog").showModal();
    $("sceneGeneratePrompt").focus();
  }

  async function submitGeneration(event) {
    event.preventDefault();
    let shot = activeScene();
    if (!shot || !["fable", "hyperframe"].includes(shot.engine)) return;
    const prompt = $("sceneGeneratePrompt").value.trim();
    const durationS = num($("sceneGenerateDuration").value);
    if (!prompt || durationS <= 0) return setStatus("generation needs a prompt and duration", "err");
    if (S.dirty && !(await saveScenes())) return;
    shot = activeScene();
    if (!shot) return;
    const sceneUid = shot.scene_uid;
    setStatus(`submitting ${shot.engine} generation…`);
    const response = await fetch(`/api/scene/${encodeURIComponent(sceneUid)}/revise`, {
      method: "POST",
      headers: {"Content-Type": "application/json", "If-Match": S.etag},
      body: JSON.stringify({
        prompt,
        duration_s: durationS,
        provider_hint: shot.engine,
      }),
    });
    const data = await response.json();
    if (!response.ok) return setStatus(data.error || "generation could not be submitted", "err");
    $("sceneGenerateDialog").close();
    applyServerTimeline(data, sceneUid);
    setStatus(`generation submitted · drop fulfillment into work/inbox/${data.job_id}`, "ok");
    trackJob(data.job_id);
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
      S.wordTicks = data.word_ticks || [];
      sceneVid.src = `/media/master?token=${encodeURIComponent(window.workbenchToken || "")}`;
      sceneVid.load();
      const maxEnd = Math.max(0, ...(S.timeline.shots || []).map((shot) => num(shot.master_out_s)));
      S.duration = Math.max(num(data.duration, 0), num(S.timeline.preview && S.timeline.preview.end_s, 0), maxEnd, 1);
      S.selectedCatalogId = S.compositions[0] ? S.compositions[0].id : null;
      S.ready = true;
      buildGroupFilter();
      renderCatalog();
      renderSceneTimeline();
      renderSceneInspector();
      $("sceneAddBtn").disabled = !S.selectedCatalogId;
      restoreActiveJob();
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
    sceneInner.querySelectorAll(".scene-block,.scene-tick,.scene-tick-label,.scene-word-tick").forEach((node) => node.remove());
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
    S.wordTicks.forEach((word) => {
      const at = num(word.start_s, -1);
      if (at < 0 || at > S.duration) return;
      const tick = document.createElement("div");
      tick.className = "scene-word-tick";
      tick.style.left = `${at * S.pps}px`;
      tick.title = `${word.text || "word"} · ${fmt(at)}`;
      $("sceneRuler").appendChild(tick);
    });
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
      renderTakesDrawer();
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
        ${["fable", "hyperframe"].includes(engine) ? '<button id="sceneGenerateBtn" class="primary">Revise → submit generation</button>' : ""}
        <button id="sceneRangeBake" class="primary">Preview bake &plusmn;2s</button>
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
    if ($("sceneGenerateBtn")) $("sceneGenerateBtn").onclick = openGenerateDialog;
    $("sceneRangeBake").onclick = bakeSelectedRange;
    $("sceneJumpStart").onclick = () => { seekActivePlayback(num(shot.master_in_s)); updateScenePlayhead(); };
    $("sceneDuplicate").onclick = () => duplicateScene();
    $("sceneDelete").onclick = () => deleteScene();
    wireProjectSettings();
    $("sceneFrameBtn").disabled = engine !== "remotion";
    $("sceneRenderBtn").disabled = engine !== "remotion";
    updateSceneFrameLabel();
    renderTakesDrawer();
  }

  function duplicateScene() {
    const shot = activeScene();
    if (!shot) return;
    const duration = num(shot.master_out_s) - num(shot.master_in_s);
    const copy = JSON.parse(JSON.stringify(shot));
    delete copy.scene_uid;
    copy.takes = [];
    copy.active_take_uid = null;
    delete copy.asset;
    copy.master_in_s = round3(num(shot.master_out_s));
    copy.master_out_s = round3(copy.master_in_s + duration);
    copy.status = copy.status === "approved" ? "draft" : copy.status;
    S.timeline.shots.splice(S.selectedIndex + 1, 0, copy);
    S.selectedIndex += 1;
    clearSceneStill();
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
    const selected = activeScene();
    const selection = selected ? {
      sceneUid: selected.scene_uid,
      engine: selected.engine,
      id: selected.id,
      start: num(selected.master_in_s),
      end: num(selected.master_out_s),
    } : null;
    setStatus("validating scene timeline...");
    const response = await fetch("/api/scenes/save", {
      method: "POST", headers: {"Content-Type": "application/json", "If-Match": S.etag},
      body: JSON.stringify({timeline: S.timeline}),
    });
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 409 && data.code === "E_ETAG_MISMATCH") {
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
    if (selection) {
      S.selectedIndex = S.timeline.shots.findIndex((scene) => (
        (selection.sceneUid && scene.scene_uid === selection.sceneUid)
        || (!selection.sceneUid && scene.engine === selection.engine && scene.id === selection.id
          && num(scene.master_in_s) === selection.start && num(scene.master_out_s) === selection.end)
      ));
      if (S.selectedIndex < 0) S.selectedIndex = null;
    }
    S.etag = data.etag || response.headers.get("ETag")?.replaceAll('"', "") || S.etag;
    S.issues = data.issues || [];
    S.dirty = false;
    $("sceneSaveBtn").disabled = true;
    setStatus(`scene timeline saved${data.backup ? " · backup created" : ""}`, "ok");
    if (data.warnings && data.warnings.length) $("sceneJobLog").textContent = data.warnings.join("\n");
    renderSceneTimeline();
    renderSceneInspector();
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
    pollSceneJob(data.job_id, () => {
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
    pollSceneJob(data.job_id);
  }

  async function bakeScenePreview() {
    if (S.dirty && !(await saveScenes())) return;
    const response = await fetch("/api/scenes/bake", {method: "POST", headers: {"Content-Type": "application/json", "If-Match": S.etag}, body: "{}"});
    const data = await response.json();
    if (!response.ok) return setStatus(data.error || "bake could not start", "err");
    setStatus("baking full scene preview...");
    pollSceneJob(data.job_id, showBakedJobPreview);
  }

  async function bakeSelectedRange() {
    let shot = activeScene();
    if (!shot) return setStatus("select a scene to preview its bake range", "err");
    if (S.dirty && !(await saveScenes())) return;
    shot = activeScene();
    if (!shot) return;
    const previewEnd = num(S.timeline.preview && S.timeline.preview.end_s, S.duration);
    const fromS = round3(Math.max(0, num(shot.master_in_s) - 2));
    const toS = round3(Math.min(previewEnd, num(shot.master_out_s) + 2));
    const response = await fetch("/api/bake/range", {
      method: "POST",
      headers: {"Content-Type": "application/json", "If-Match": S.etag},
      body: JSON.stringify({from_s: fromS, to_s: toS}),
    });
    const data = await response.json();
    if (!response.ok) return setStatus(data.error || "range bake could not start", "err");
    setStatus(`baking ${fmt(fromS)}-${fmt(toS)} around ${shot.id}...`);
    pollSceneJob(data.job_id, showBakedJobPreview);
  }

  function renderJobChip(job) {
    const chip = $("sceneJobChip");
    const label = $("sceneJobLabel");
    const cancel = $("sceneJobCancel");
    chip.classList.remove("idle", "running", "succeeded", "failed", "canceled");
    if (!job) {
      chip.classList.add("idle");
      label.textContent = "Jobs idle";
      cancel.classList.add("hidden");
      chip.title = "No render or bake job is active";
      return;
    }
    const state = job.state || "queued";
    const active = ["queued", "submitted", "running", "awaiting_pick"].includes(state);
    const pct = Math.round(num(job.progress) * 100);
    chip.classList.add(active ? "running" : state);
    label.textContent = active
      ? `${job.kind || "job"} ${pct}% \u00b7 ${job.message || state}`
      : `${job.kind || "job"} ${state}`;
    const cancellable = active && (job.kind === "generate" || (job.pid && job.start_token));
    cancel.classList.toggle("hidden", !cancellable);
    chip.title = `${job.job_id} \u00b7 ${job.message || state}`;
  }

  async function pollDurableJob(jobId, onSuccess) {
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
      const data = await response.json();
      const job = data.job;
      if (!job) throw new Error("durable job record was not found");
      S.currentJob = job;
      renderJobChip(job);
      if (["queued", "submitted", "running"].includes(job.state)) {
        S.jobPollTimer = setTimeout(() => pollDurableJob(jobId, onSuccess), 700);
        return;
      }
      S.jobPollTimer = null;
      if (job.state === "succeeded") {
        if (job.kind === "generate") await refreshSceneData(job.scene_uid);
        if (onSuccess) onSuccess(job);
        setStatus(`${job.kind} complete`, "ok");
      } else if (job.state === "awaiting_pick") {
        await refreshSceneData(job.scene_uid);
        setStatus("generated candidate ready in Takes · preview or promote it", "ok");
      } else if (job.state === "canceled") {
        setStatus(`${job.kind} canceled`, "err");
      } else {
        setStatus(`${job.kind} ${job.state}: ${job.message || "job did not complete"}`, "err");
      }
    } catch (error) {
      S.jobPollTimer = null;
      renderJobChip(null);
      setStatus(`job polling failed: ${error.message}`, "err");
    }
  }

  function trackJob(jobId, onSuccess) {
    if (S.jobPollTimer) clearTimeout(S.jobPollTimer);
    S.currentJob = {job_id: jobId, kind: "job", state: "queued", progress: 0, message: "queued"};
    renderJobChip(S.currentJob);
    pollDurableJob(jobId, onSuccess);
  }

  function pollSceneJob(jobId, onSuccess) {
    trackJob(jobId, onSuccess);
  }

  async function cancelCurrentJob() {
    let job = S.currentJob;
    if (!job || !["queued", "submitted", "running", "awaiting_pick"].includes(job.state)) return;
    setStatus(`canceling ${job.kind}...`);
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const response = await fetch(`/api/jobs/${encodeURIComponent(job.job_id)}/cancel`, {
        method: "POST",
        headers: {"Content-Type": "application/json", "If-Match": job.updated_at || ""},
        body: "{}",
      });
      const data = await response.json();
      if (response.ok) {
        S.currentJob = data.job;
        renderJobChip(data.job);
        setStatus(`${data.job.kind} canceled`, "err");
        return;
      }
      if (data.code === "E_JOB_TOKEN_MISMATCH" && data.details && data.details.job) {
        job = data.details.job;
        S.currentJob = job;
        continue;
      }
      setStatus(data.error || "job could not be canceled", "err");
      return;
    }
    setStatus("job changed while canceling; try again", "err");
  }

  async function restoreActiveJob() {
    try {
      const response = await fetch("/api/jobs");
      const data = await response.json();
      const active = (data.jobs || []).find((job) => ["queued", "submitted", "running", "awaiting_pick"].includes(job.state));
      if (active) {
        trackJob(active.job_id, active.kind === "bake" ? showBakedJobPreview : null);
        return;
      }
      const latestBake = (data.jobs || []).find((job) => job.kind === "bake" && job.state === "succeeded");
      if (latestBake) {
        S.currentJob = latestBake;
        renderJobChip(latestBake);
        showBakedJobPreview(latestBake);
      } else {
        renderJobChip(null);
      }
    } catch (_error) {
      renderJobChip(null);
    }
  }

  function showBakedJobPreview(job) {
    clearSceneStill();
    sceneVid.pause();
    sceneTakePreview.muted = false;
    sceneTakePreview.src = jobMediaUrl(job.job_id);
    sceneTakePreview.removeAttribute("data-take-uid");
    sceneTakePreview.dataset.jobId = job.job_id;
    sceneTakePreview.dataset.rangeFrom = num(job.range && job.range.from_s);
    sceneTakePreview.dataset.rangeTo = num(job.range && job.range.to_s, num(S.timeline.preview && S.timeline.preview.end_s, S.duration));
    sceneTakePreview.classList.remove("hidden");
    $("sceneEmptyPreview").classList.add("hidden");
    $("sceneClearPreviewBtn").disabled = false;
    sceneTakePreview.load();
    sceneTakePreview.play().catch(() => {});
  }

  function clearSceneStill() {
    S.previewTakeUid = null;
    S.compare = null;
    sceneTakePreview.pause();
    sceneTakePreview.muted = true;
    sceneTakePreview.removeAttribute("src");
    sceneTakePreview.removeAttribute("data-take-uid");
    sceneTakePreview.removeAttribute("data-job-id");
    sceneTakePreview.removeAttribute("data-range-from");
    sceneTakePreview.removeAttribute("data-range-to");
    sceneTakePreview.load();
    sceneTakePreview.className = "hidden";
    sceneStill.removeAttribute("src");
    sceneStill.className = "hidden";
    $("sceneEmptyPreview").classList.remove("hidden");
    $("sceneClearPreviewBtn").disabled = true;
    renderCompareControls();
    renderTakesDrawer();
  }

  function updateScenePlayhead() {
    const playback = activePlaybackVideo();
    const time = playbackMasterTime();
    if (playback === sceneTakePreview && Math.abs(num(sceneVid.currentTime) - time) > .05) {
      sceneVid.currentTime = Math.min(time, S.duration);
    }
    $("scenePlayhead").style.left = `${time * S.pps}px`;
    $("sceneTimeinfo").textContent = `${fmt(time)} · ${S.project || "scene timeline"}`;
    updateSceneFrameLabel();
    if (!playback.paused && document.body.dataset.workspace === "scenes") {
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
    $("sceneJobCancel").onclick = cancelCurrentJob;
    $("sceneFrameBtn").onclick = renderCurrentFrame;
    $("sceneClearPreviewBtn").onclick = clearSceneStill;
    $("sceneTakeInput").onchange = (event) => importTake(event.target.files && event.target.files[0]);
    $("sceneGenerateSubmit").onclick = submitGeneration;
    $("sceneCompareControls").querySelectorAll("[data-compare-side]").forEach((button) => {
      button.onclick = () => showCompareSide(button.dataset.compareSide);
    });
    $("sceneCompareClose").onclick = closeCompare;
    $("scenePlayBtn").onclick = () => {
      const playback = activePlaybackVideo();
      playback.paused ? playback.play() : playback.pause();
    };
    $("sceneZoom").oninput = (event) => {
      const center = playbackMasterTime();
      S.pps = num(event.target.value, 8);
      renderSceneTimeline();
      sceneTimeline.scrollLeft = center * S.pps - sceneTimeline.clientWidth / 2;
    };
    sceneVid.addEventListener("timeupdate", updateScenePlayhead);
    sceneTakePreview.addEventListener("timeupdate", updateScenePlayhead);

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
        syncScenePreview();
        event.preventDefault();
        return;
      }
      const rect = sceneInner.getBoundingClientRect();
      seekActivePlayback(Math.max(0, Math.min((event.clientX - rect.left) / S.pps, S.duration)));
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
      if (event.key === " ") {
        event.preventDefault();
        const playback = activePlaybackVideo();
        playback.paused ? playback.play() : playback.pause();
      }
      else if (event.key === "ArrowLeft") seekActivePlayback(playbackMasterTime() - (event.shiftKey ? 10 : 2));
      else if (event.key === "ArrowRight") seekActivePlayback(playbackMasterTime() + (event.shiftKey ? 10 : 2));
      else if (event.key === ",") seekActivePlayback(playbackMasterTime() - 1 / 60);
      else if (event.key === ".") seekActivePlayback(playbackMasterTime() + 1 / 60);
      else if (event.key.toLowerCase() === "a") addSceneAtPlayhead();
      else if (event.key.toLowerCase() === "b" && activeScene()) bakeSelectedRange();
      else if (event.key.toLowerCase() === "v" && activeScene()) {
        S.takesOpen = !S.takesOpen;
        renderTakesDrawer();
      }
    });

    window.addEventListener("beforeunload", (event) => { if (S.dirty) event.preventDefault(); });
  }

  initScenes();
})();
