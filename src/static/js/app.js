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
  var rows = {};           // job id -> cached DOM row, patched in place
  var lastAutoCount = null;
  var hotkeyWarned = false;

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

    if (view === "queue") pollJobs();
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

    // Rows are patched in place. Rebuilding the list every poll reloaded each
    // thumbnail, which made the whole queue flicker while downloading.
    var seen = {};
    jobs.forEach(function (j, index) {
      var row = rows[j.id];
      if (!row) {
        row = buildRow(j);
        rows[j.id] = row;
      }
      updateRow(row, j);
      seen[j.id] = true;

      if (box.children[index] !== row.el) {
        box.insertBefore(row.el, box.children[index] || null);
      }
    });

    Object.keys(rows).forEach(function (id) {
      if (!seen[id]) {
        rows[id].el.remove();
        delete rows[id];
      }
    });

    return active;
  }

  function buildRow(job) {
    var el = document.createElement("div");
    el.className = "job";

    var thumb;
    if (job.thumbnail) {
      thumb = document.createElement("img");
      thumb.className = "job-thumb";
      thumb.alt = "";
      thumb.src = job.thumbnail;
      thumb.onerror = function () { thumb.style.visibility = "hidden"; };
    } else {
      thumb = document.createElement("div");
      thumb.className = "job-thumb";
    }

    var main = document.createElement("div");
    main.className = "job-main";

    var title = document.createElement("div");
    title.className = "job-title";
    title.textContent = job.title;
    title.title = job.title;

    var stats = document.createElement("div");
    stats.className = "job-stats";

    var meter = document.createElement("div");
    meter.className = "meter";
    var fill = document.createElement("i");
    meter.appendChild(fill);

    var error = document.createElement("div");
    error.className = "job-error";
    error.hidden = true;

    main.appendChild(title);
    main.appendChild(stats);
    main.appendChild(meter);
    main.appendChild(error);

    var actions = document.createElement("div");
    actions.className = "job-actions";

    el.appendChild(thumb);
    el.appendChild(main);
    el.appendChild(actions);

    return {
      el: el, title: title, stats: stats, fill: fill,
      error: error, actions: actions,
      lastStatus: null, lastTitle: job.title
    };
  }

  var ACTIONS = {
    done: [["open", "▶", "Play file", "go"], ["reveal", "📁", "Show in folder", ""]],
    error: [["retry", "↻", "Try again", "go"]],
    cancelled: [["retry", "↻", "Try again", "go"]],
    busy: [["cancel", "✕", "Stop", "stop"]]
  };

  function updateRow(row, j) {
    if (j.title !== row.lastTitle) {
      row.title.textContent = j.title;
      row.title.title = j.title;
      row.lastTitle = j.title;
    }

    var bits = [["state", STATE_TEXT[j.status] || j.status]];
    if (j.status === "downloading") {
      bits.push(["", j.percent.toFixed(1) + "%"]);
      if (j.speed) bits.push(["", j.speed]);
      if (j.eta) bits.push(["", "ETA " + j.eta]);
    } else {
      bits.push(["", j.qualityLabel]);
      if (j.status === "done" && j.size) bits.push(["", j.size]);
    }

    var text = bits.map(function (b) { return b[1]; }).join("");
    if (text !== row.lastStats) {
      row.stats.textContent = "";
      bits.forEach(function (b) {
        var span = document.createElement("span");
        if (b[0]) span.className = b[0];
        span.textContent = b[1];
        row.stats.appendChild(span);
      });
      row.lastStats = text;
    }

    row.fill.style.width = (j.status === "done" ? 100 : j.percent) + "%";

    if (j.error) {
      row.error.textContent = j.error;
      row.error.hidden = false;
    } else if (!row.error.hidden) {
      row.error.hidden = true;
    }

    if (j.status !== row.lastStatus) {
      row.el.className = "job " + j.status;

      var set = ACTIONS[j.status] || ACTIONS.busy;
      row.actions.textContent = "";
      set.concat([["remove", "🗑", "Remove", ""]]).forEach(function (a) {
        var btn = document.createElement("button");
        btn.className = "icon-btn " + a[3];
        btn.dataset.act = a[0];
        btn.dataset.id = j.id;
        btn.title = a[2];
        btn.textContent = a[1];
        row.actions.appendChild(btn);
      });
      row.lastStatus = j.status;
    }
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

  var BLOCKED_BROWSERS = ["chrome", "edge", "brave", "opera", "vivaldi"];

  $("setCookies").addEventListener("change", function (e) {
    var value = e.target.value;
    saveSetting({ cookies_browser: value }).then(function () {
      if (value === "none") {
        toast("Cookies off");
      } else if (BLOCKED_BROWSERS.indexOf(value) !== -1) {
        toast(value + " locks its cookies — use a cookies file instead", "bad");
      } else {
        toast("Using " + value + " cookies", "good");
      }
    });
  });

  $("chooseCookies").addEventListener("click", function () {
    api("/api/choose-cookies", {}).then(function (res) {
      if (res.ok) {
        $("cookieFileLabel").textContent = res.settings.cookies_file;
        toast("Cookies file set", "good");
      } else if (!res.cancelled) {
        toast(res.error || "Could not open the picker.", "bad");
      }
    });
  });

  $("clearCookies").addEventListener("click", function () {
    saveSetting({ cookies_file: "" }).then(function () {
      $("cookieFileLabel").textContent = "Not set";
      toast("Cookies file cleared");
    });
  });

  function bindToggle(id, key) {
    $(id).addEventListener("click", function () {
      var on = !$(id).classList.contains("on");
      $(id).classList.toggle("on", on);
      var patch = {}; patch[key] = on;
      saveSetting(patch);
      if (key === "auto_paste" && !on) $("clipHint").hidden = true;
      if (key === "hotkey") toast("Restart Riplox for this to take effect");
    });
  }
  bindToggle("setH264", "prefer_h264");
  bindToggle("setSubfolder", "subfolder_per_site");
  bindToggle("setAutoDownload", "auto_download");
  bindToggle("setHotkey", "hotkey");
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
    api("/api/clipboard").then(function (res) {
      // Instant downloads are queued by the app itself; just say so.
      if (typeof res.autoCount === "number") {
        if (lastAutoCount !== null && res.autoCount > lastAutoCount) {
          toast("Copied link queued", "good");
          pollJobs();
        }
        lastAutoCount = res.autoCount;
      }

      if (!hotkeyWarned && res.hotkey && res.hotkey !== "off") {
        hotkeyWarned = true;
        var label = $("hotkeyLabel");
        var note = $("hotkeyNote");
        if (res.hotkey === "taken" && note) {
          note.innerHTML = '<b class="warn">Every shortcut Riplox tried is ' +
            'already used by another program.</b> Use the Paste button, or ' +
            'close whatever owns those keys and restart Riplox.';
          if (label) label.textContent = "unavailable";
        } else if (label && res.hotkeyLabel) {
          label.textContent = res.hotkeyLabel;
        }

        // The shortcut is the fastest way to use Riplox, so the first screen
        // shows it instead of leaving it buried in Settings.
        var tip = $("hotkeyTip"), keys = $("hotkeyTipKeys");
        if (tip && keys && res.hotkey === "on" && res.hotkeyLabel) {
          keys.textContent = res.hotkeyLabel;
          tip.hidden = false;
        }
      }

      if (!$("view-capture").classList.contains("is-active")) return;

      var text = (res.pending || "").trim();
      lastClip = text || (res.text || "").trim();

      if (!text || text === dismissedClip || text === $("urlInput").value.trim()) {
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
    api("/api/clipboard/dismiss", {});
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
  // Always polling: even with clipboard watching off, this is how the window
  // hears about downloads the global shortcut started.
  startClipboardWatch();
})();
