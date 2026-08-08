/* Riplox Desktop — UI logic */
(function () {
  "use strict";

  var S = window.RIPLOX || {};
  var settings = S.settings || {};
  var labels = S.labels || {};

  var current = null;      // analyzed video or playlist
  var quality = settings.default_quality || "best";
  var pollTimer = null;
  var clipTimer = null;
  var analyzing = false;
  var lastClip = "";
  var dismissedClip = "";

  var $ = function (id) { return document.getElementById(id); };

  function api(path, body) {
    return fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json", "X-Riplox-Token": S.token || "" },
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (r) { return r.json().catch(function () { return { ok: false, error: "Bad response." }; }); });
  }

  function toast(msg, kind) {
    var el = $("toast");
    el.textContent = msg;
    el.className = "toast show " + (kind || "");
    el.hidden = false;
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.className = "toast " + (kind || ""); }, 2600);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function fmtDuration(sec) {
    sec = Math.round(sec || 0);
    if (!sec) return "";
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
    return h ? h + ":" + pad(m) + ":" + pad(s) : m + ":" + pad(s);
  }

  /* ---------------------------------------------------------------- tabs */

  var views = ["capture", "queue", "library", "settings"];

  function moveUnderline(btn) {
    var bar = $("tabUnderline");
    bar.style.width = btn.offsetWidth + "px";
    bar.style.transform = "translateX(" + btn.offsetLeft + "px)";
  }

  function show(view) {
    views.forEach(function (v) {
      $("view-" + v).classList.toggle("is-active", v === view);
    });
    var active = null;
    document.querySelectorAll(".tab").forEach(function (b) {
      var on = b.dataset.view === view;
      b.classList.toggle("is-active", on);
      if (on) active = b;
    });
    if (active) moveUnderline(active);

    if (view === "library") loadHistory();
    if (view === "settings") loadEngineVersion();
  }

  document.querySelectorAll(".tab").forEach(function (b) {
    b.addEventListener("click", function () { show(b.dataset.view); });
  });

  /* ------------------------------------------------------------- analyze */

  function setBusy(on) {
    var btn = $("analyzeBtn");
    analyzing = on;
    btn.disabled = on;
    btn.querySelector(".label").textContent = on ? "Reading" : "Analyze";
    btn.querySelector(".spinner").hidden = !on;
    if (on) {
      $("engineStatus").className = "status busy";
      $("engineLabel").textContent = "reading";
    } else {
      pollJobs();
    }
  }

  $("urlForm").addEventListener("submit", function (e) {
    e.preventDefault();
    analyze($("urlInput").value.trim());
  });

  function analyze(url) {
    if (!url) { toast("Paste a link first.", "bad"); return; }

    $("analyzeError").hidden = true;
    $("preview").hidden = true;
    $("playlistBox").hidden = true;
    setBusy(true);

    api("/api/analyze", { url: url }).then(function (res) {
      setBusy(false);
      if (!res.ok) {
        var box = $("analyzeError");
        box.textContent = res.error || "Could not read that link.";
        box.hidden = false;
        $("engineStatus").className = "status bad";
        $("engineLabel").textContent = "failed";
        return;
      }
      current = res.info;
      renderPreview(res.info);
    }).catch(function () {
      setBusy(false);
      toast("The app lost contact with its engine.", "bad");
    });
  }

  function renderPreview(info) {
    var isList = info.kind === "playlist";

    $("pvKind").textContent = isList ? "PLAYLIST" : "VIDEO";
    $("pvTitle").textContent = info.title || "Untitled";
    var thumb = $("pvThumb");
    // An empty src makes the browser re-request the page itself, so only set
    // it when there is a real image.
    if (info.thumbnail) {
      thumb.src = info.thumbnail;
      thumb.style.visibility = "";
    } else {
      thumb.removeAttribute("src");
      thumb.style.visibility = "hidden";
    }
    thumb.alt = info.title || "";

    var bits = [];
    if (info.uploader) bits.push(esc(info.uploader));
    if (isList) bits.push(info.count + " videos");
    if (!isList && info.duration) bits.push(fmtDuration(info.duration));
    if (!isList && info.extractor) bits.push(esc(info.extractor));
    $("pvMeta").innerHTML = bits.map(function (b) { return "<span>" + b + "</span>"; }).join("");

    var options = (!isList && info.qualities && info.qualities.length)
      ? info.qualities
      : ["best", "1080", "720", "480", "mp3"];

    if (options.indexOf(quality) === -1) quality = options[0];

    $("qualityChips").innerHTML = options.map(function (q) {
      return '<button type="button" class="chip' +
        (q === "mp3" ? " audio" : "") +
        (q === quality ? " is-on" : "") +
        '" data-q="' + q + '">' + esc(labels[q] || q) + "</button>";
    }).join("");

    $("downloadBtn").textContent = isList
      ? "Download all " + info.count
      : "Download";

    $("preview").hidden = false;

    if (isList) {
      $("playlistBox").innerHTML = info.entries.slice(0, 100).map(function (e, i) {
        return "<li><b>" + (i + 1) + "</b><span>" + esc(e.title) + "</span></li>";
      }).join("");
      $("playlistBox").hidden = false;
    }
  }

  $("qualityChips").addEventListener("click", function (e) {
    var chip = e.target.closest(".chip");
    if (!chip) return;
    quality = chip.dataset.q;
    document.querySelectorAll("#qualityChips .chip").forEach(function (c) {
      c.classList.toggle("is-on", c === chip);
    });
  });

  $("resetBtn").addEventListener("click", function () {
    current = null;
    $("preview").hidden = true;
    $("playlistBox").hidden = true;
    $("urlInput").value = "";
    $("urlInput").focus();
  });

  /* ------------------------------------------------------------ download */

  $("downloadBtn").addEventListener("click", function () {
    if (!current) return;

    var items;
    if (current.kind === "playlist") {
      items = current.entries.map(function (e) {
        return { url: e.url, title: e.title, thumbnail: e.thumbnail, uploader: current.uploader };
      }).filter(function (e) { return e.url; });
    } else {
      items = [{
        url: current.url, title: current.title,
        thumbnail: current.thumbnail, uploader: current.uploader
      }];
    }

    api("/api/add", { items: items, quality: quality }).then(function (res) {
      if (!res.ok) { toast(res.error || "Could not queue that.", "bad"); return; }
      toast(res.added > 1 ? res.added + " videos queued" : "Queued", "good");
      $("preview").hidden = true;
      $("playlistBox").hidden = true;
      $("urlInput").value = "";
      current = null;
      show("queue");
      pollJobs();
    });
  });

  /* --------------------------------------------------------------- queue */

  var STATE_TEXT = {
    queued: "queued", starting: "starting", downloading: "downloading",
    converting: "converting", done: "done", error: "failed", cancelled: "cancelled"
  };

  function renderJobs(jobs) {
    var box = $("jobs");
    var active = jobs.filter(function (j) {
      return j.status === "downloading" || j.status === "converting" || j.status === "starting" || j.status === "queued";
    }).length;

    var badge = $("queueBadge");
    badge.textContent = active;
    badge.hidden = active === 0;

    $("queueEmpty").hidden = jobs.length > 0;

    box.innerHTML = jobs.map(function (j) {
      var stats = ['<span class="state">' + esc(STATE_TEXT[j.status] || j.status) + "</span>"];
      if (j.status === "downloading") {
        stats.push("<span>" + j.percent.toFixed(1) + "%</span>");
        if (j.speed) stats.push("<span>" + esc(j.speed) + "</span>");
        if (j.eta) stats.push("<span>ETA " + esc(j.eta) + "</span>");
      } else if (j.status === "done") {
        stats.push("<span>" + esc(j.qualityLabel) + "</span>");
        if (j.size) stats.push("<span>" + esc(j.size) + "</span>");
      } else {
        stats.push("<span>" + esc(j.qualityLabel) + "</span>");
      }

      var actions = "";
      if (j.status === "done") {
        actions =
          '<button class="icon-btn go" data-act="open" data-id="' + j.id + '" title="Play file">&#9654;</button>' +
          '<button class="icon-btn" data-act="reveal" data-id="' + j.id + '" title="Show in folder">&#128193;</button>';
      } else if (j.status === "error" || j.status === "cancelled") {
        actions = '<button class="icon-btn go" data-act="retry" data-id="' + j.id + '" title="Try again">&#8635;</button>';
      } else {
        actions = '<button class="icon-btn stop" data-act="cancel" data-id="' + j.id + '" title="Stop">&#10005;</button>';
      }
      actions += '<button class="icon-btn" data-act="remove" data-id="' + j.id + '" title="Remove">&#128465;</button>';

      return '<div class="job ' + j.status + '">' +
        (j.thumbnail
          ? '<img class="job-thumb" src="' + esc(j.thumbnail) + '" alt="" onerror="this.style.visibility=\'hidden\'">'
          : '<div class="job-thumb"></div>') +
        '<div class="job-main">' +
          '<div class="job-title" title="' + esc(j.title) + '">' + esc(j.title) + "</div>" +
          '<div class="job-stats">' + stats.join("") + "</div>" +
          '<div class="meter"><i style="width:' + (j.status === "done" ? 100 : j.percent) + '%"></i></div>' +
          (j.error ? '<div class="job-error">' + esc(j.error) + "</div>" : "") +
        "</div>" +
        '<div class="job-actions">' + actions + "</div>" +
      "</div>";
    }).join("");

    return active;
  }

  $("jobs").addEventListener("click", function (e) {
    var btn = e.target.closest("[data-act]");
    if (!btn) return;
    var id = btn.dataset.id, act = btn.dataset.act;

    if (act === "open" || act === "reveal") {
      var job = (window._jobs || []).find(function (j) { return j.id === id; });
      if (!job || !job.filepath) { toast("File path unknown.", "bad"); return; }
      api("/api/open", { path: job.filepath, reveal: act === "reveal" }).then(function (r) {
        if (!r.ok) toast(r.error || "Could not open it.", "bad");
      });
      return;
    }

    api("/api/job/" + act, { id: id }).then(function () { pollJobs(); });
  });

  $("clearFinished").addEventListener("click", function () {
    api("/api/clear-finished", {}).then(function () { pollJobs(); });
  });

  function pollJobs() {
    return api("/api/jobs").then(function (res) {
      if (!res.ok) return 0;
      window._jobs = res.jobs;
      var active = renderJobs(res.jobs);

      if (!analyzing) {
        $("engineStatus").className = "status" + (active ? " busy" : "");
        $("engineLabel").textContent = active ? active + " active" : "ready";
      }

      clearTimeout(pollTimer);
      var visible = $("view-queue").classList.contains("is-active");
      if (active > 0) pollTimer = setTimeout(pollJobs, visible ? 700 : 1600);
      return active;
    }).catch(function () { return 0; });
  }

  /* ------------------------------------------------------------- library */

  function loadHistory() {
    api("/api/history").then(function (res) {
      var items = res.history || [];
      $("libraryEmpty").hidden = items.length > 0;
      $("history").innerHTML = items.map(function (h) {
        var when = (h.when || "").replace("T", " ").slice(0, 16);
        return '<div class="hrow" data-path="' + esc(h.filepath) + '">' +
          (h.thumbnail ? '<img src="' + esc(h.thumbnail) + '" alt="" onerror="this.style.visibility=\'hidden\'">' : "<div></div>") +
          '<div><div class="t">' + esc(h.title) + '</div>' +
          '<div class="m">' + esc(labels[h.quality] || h.quality || "") +
          (h.size ? " · " + esc(h.size) : "") + " · " + esc(when) + "</div></div>" +
          '<button class="icon-btn go" data-open="1" title="Play file">&#9654;</button>' +
          "</div>";
      }).join("");
    });
  }

  $("history").addEventListener("click", function (e) {
    var row = e.target.closest(".hrow");
    if (!row) return;
    var path = row.dataset.path;
    if (!path) { toast("File path unknown.", "bad"); return; }
    api("/api/open", { path: path, reveal: !e.target.closest("[data-open]") }).then(function (r) {
      if (!r.ok) toast(r.error || "Could not open it.", "bad");
    });
  });

  $("openFolder").addEventListener("click", function () {
    api("/api/open", {}).then(function (r) {
      if (!r.ok) toast(r.error || "Could not open the folder.", "bad");
    });
  });

  $("clearHistory").addEventListener("click", function () {
    api("/api/history/clear", {}).then(function () {
      loadHistory();
      toast("History cleared");
    });
  });

  /* ------------------------------------------------------------ settings */

  function saveSetting(patch) {
    return api("/api/settings", patch).then(function (res) {
      if (res.ok) settings = res.settings;
      return res;
    });
  }

  $("setQuality").addEventListener("change", function (e) {
    quality = e.target.value;
    saveSetting({ default_quality: e.target.value }).then(function () { toast("Saved"); });
  });

  $("setParallel").addEventListener("change", function (e) {
    saveSetting({ max_parallel: parseInt(e.target.value, 10) }).then(function () { toast("Saved"); });
  });

  $("setCookies").addEventListener("change", function (e) {
    saveSetting({ cookies_browser: e.target.value }).then(function () {
      toast(e.target.value === "none" ? "Cookies off" : "Using " + e.target.value + " cookies");
    });
  });

  function bindToggle(id, key) {
    $(id).addEventListener("click", function () {
      var on = !$(id).classList.contains("on");
      $(id).classList.toggle("on", on);
      var patch = {}; patch[key] = on;
      saveSetting(patch);
      if (key === "auto_paste") on ? startClipboardWatch() : stopClipboardWatch();
    });
  }
  bindToggle("setH264", "prefer_h264");
  bindToggle("setSubfolder", "subfolder_per_site");
  bindToggle("setAutoPaste", "auto_paste");
  bindToggle("setThumb", "write_thumbnail");

  $("chooseDir").addEventListener("click", function () {
    api("/api/choose-folder", {}).then(function (res) {
      if (res.ok) {
        $("dirLabel").textContent = res.settings.download_dir;
        toast("Folder changed");
      } else if (!res.cancelled) {
        toast(res.error || "Could not open the picker.", "bad");
      }
    });
  });

  function loadEngineVersion() {
    api("/api/settings").then(function (res) {
      $("engineVersion").textContent = res.engineVersion && res.engineVersion !== "missing"
        ? "Version " + res.engineVersion
        : "Not installed";
    });
  }

  $("updateEngine").addEventListener("click", function () {
    var btn = $("updateEngine");
    btn.disabled = true;
    btn.textContent = "Checking…";
    api("/api/update-engine", {}).then(function (res) {
      btn.disabled = false;
      btn.textContent = "Update";
      toast(res.message || (res.ok ? "Up to date" : "Update failed"), res.ok ? "good" : "bad");
      loadEngineVersion();
    });
  });

  /* ------------------------------------------------------------ clipboard */

  var URL_RE = /^https?:\/\/[^\s]+$/i;

  function startClipboardWatch() {
    stopClipboardWatch();
    clipTimer = setInterval(checkClipboard, 1400);
  }
  function stopClipboardWatch() {
    clearInterval(clipTimer);
    $("clipHint").hidden = true;
  }

  function checkClipboard() {
    if (!$("view-capture").classList.contains("is-active")) return;
    api("/api/clipboard").then(function (res) {
      var text = (res.text || "").trim();
      if (text === lastClip) return;
      lastClip = text;

      if (!URL_RE.test(text) || text === dismissedClip || text === $("urlInput").value.trim()) {
        $("clipHint").hidden = true;
        return;
      }
      $("clipText").textContent = text;
      $("clipHint").hidden = false;
    }).catch(function () {});
  }

  $("clipUse").addEventListener("click", function () {
    $("urlInput").value = lastClip;
    dismissedClip = lastClip;
    $("clipHint").hidden = true;
    analyze(lastClip);
  });

  $("pasteBtn").addEventListener("click", function () {
    api("/api/clipboard").then(function (res) {
      var text = (res.text || "").trim();
      if (!text) { toast("Clipboard is empty.", "bad"); return; }
      $("urlInput").value = text;
      $("clipHint").hidden = true;
      if (URL_RE.test(text)) analyze(text);
    });
  });

  /* ----------------------------------------------------------------- boot */

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { $("clipHint").hidden = true; dismissedClip = lastClip; }
    if ((e.ctrlKey || e.metaKey) && e.key === "l") { e.preventDefault(); show("capture"); $("urlInput").focus(); $("urlInput").select(); }
  });

  window.addEventListener("resize", function () {
    var active = document.querySelector(".tab.is-active");
    if (active) moveUnderline(active);
  });

  var startTab = document.querySelector(".tab.is-active");
  if (startTab) requestAnimationFrame(function () { moveUnderline(startTab); });

  $("urlInput").focus();
  pollJobs();
  if (settings.auto_paste) startClipboardWatch();
})();
