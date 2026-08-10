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

  /* ------------------------------------------------------------- dialogs */

  /* Riplox's own ask/tell box.
     WebView2 heads a native confirm() with the address it is served from -
     "127.0.0.1:50473 says" - so a routine "remove this device?" arrives
     looking like a browser security warning, complete with a port number.
     Same replacement Transport Ledger and RentLedger use. */
  var dlgResolve = null;
  var dlgMode = "tell";

  function dlgOpen(mode, message, opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      dlgResolve = resolve;
      dlgMode = mode;

      $("xdlgTitle").textContent = opts.title ||
        (mode === "ask" ? "Please confirm" : mode === "prompt" ? "Riplox needs a name" : "Riplox");
      $("xdlgMsg").textContent = String(message == null ? "" : message);

      $("xdlgInputWrap").hidden = mode !== "prompt";
      $("xdlgInput").value = mode === "prompt" ? (opts.value || "") : "";

      $("xdlgCancel").hidden = mode === "tell";
      $("xdlgOk").textContent = opts.ok || "OK";
      $("xdlgOk").classList.toggle("danger", !!opts.danger);
      $("xdlg").hidden = false;

      setTimeout(function () {
        if (mode === "prompt") { $("xdlgInput").focus(); $("xdlgInput").select(); }
        else $("xdlgOk").focus();
      }, 50);
    });
  }

  function dlgClose(value) {
    $("xdlg").hidden = true;
    var resolve = dlgResolve;
    dlgResolve = null;
    if (resolve) resolve(value);
  }

  function dlgCancel() {
    dlgClose(dlgMode === "ask" ? false : dlgMode === "prompt" ? null : undefined);
  }

  function dlgOk() {
    dlgClose(dlgMode === "ask" ? true
      : dlgMode === "prompt" ? $("xdlgInput").value : undefined);
  }

  function ask(message, opts) { return dlgOpen("ask", message, opts); }
  function tell(message, title) { return dlgOpen("tell", message, { title: title }); }

  $("xdlgOk").addEventListener("click", dlgOk);
  $("xdlgCancel").addEventListener("click", dlgCancel);
  $("xdlg").addEventListener("click", function (e) {
    if (e.target === $("xdlg")) dlgCancel();     // clicking the dim area is Cancel
  });
  document.addEventListener("keydown", function (e) {
    if (!dlgResolve) return;
    if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); dlgOk(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); dlgCancel(); }
  }, true);

  // Anything that still reaches for the native one gets ours instead.
  window.alert = function (message) { tell(message); };

  function copyText(text) {
    // The embedded browser does not always grant the clipboard API, so fall
    // back to the old selection trick rather than failing silently.
    function fallback() {
      var box = document.createElement("textarea");
      box.value = text;
      box.style.position = "fixed";
      box.style.opacity = "0";
      document.body.appendChild(box);
      box.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
      document.body.removeChild(box);
      toast(ok ? "Copied" : "Could not copy.", ok ? "good" : "bad");
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text)
        .then(function () { toast("Copied", "good"); })
        .catch(fallback);
      return;
    }
    fallback();
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

  /* --------------------------------------------------------------- theme */

  // Three choices, and "auto" means the system decides - so the media query is
  // watched, not read once.
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;

  function paintTheme() {
    var pick = settings.theme || "auto";
    var mode = pick === "auto" ? (media && media.matches ? "light" : "dark") : pick;
    document.documentElement.setAttribute("data-theme", mode);
    document.querySelectorAll("#themePick button").forEach(function (b) {
      b.classList.toggle("is-on", b.dataset.theme === pick);
    });
  }

  document.querySelectorAll("#themePick button").forEach(function (b) {
    b.addEventListener("click", function () {
      settings.theme = b.dataset.theme;
      paintTheme();
      saveSetting({ theme: settings.theme });
    });
  });

  if (media) {
    var onScheme = function () { if ((settings.theme || "auto") === "auto") paintTheme(); };
    if (media.addEventListener) media.addEventListener("change", onScheme);
    else if (media.addListener) media.addListener(onScheme);
  }

  paintTheme();

  /* ---------------------------------------------------------------- tabs */

  var views = ["capture", "queue", "library", "convert", "watch", "sharing",
               "settings"];

  function show(view) {
    views.forEach(function (v) {
      $("view-" + v).classList.toggle("is-active", v === view);
    });
    document.querySelectorAll(".tab").forEach(function (b) {
      b.classList.toggle("is-active", b.dataset.view === view);
    });

    if (view === "queue") pollJobs();
    if (view === "library") loadHistory();
    if (view === "convert") loadConvert();
    if (view === "watch") loadWatch();
    if (view === "sharing") loadSharing();
    if (view === "settings") { loadEngineVersion(); loadCookies(); loadPot(); }
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
    $("playlistWrap").hidden = true;
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
    // A channel is not a video and not a playlist - it is a set of sections.
    // Show those, and let opening one become an ordinary playlist.
    if (info.kind === "channel") {
      renderChannel(info);
      return;
    }

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

    // A rung that only exists because YouTube enlarged the video with AI says
    // so on the chip. "1440p" over a 480p source is the app lying about the
    // file it is about to hand over.
    var upscaled = (info && info.upscaled) || {};

    $("qualityChips").innerHTML = options.map(function (q) {
      var from = upscaled[q];
      return '<button type="button" class="chip' +
        (q === "mp3" ? " audio" : "") +
        (from ? " upscaled" : "") +
        (q === quality ? " is-on" : "") +
        '" data-q="' + q + '"' +
        (from ? ' title="YouTube made this with AI from a ' + from + 'p original"' : "") +
        ">" + esc(labels[q] || q) +
        (from ? '<em> · AI-upscaled from ' + from + "p</em>" : "") +
        "</button>";
    }).join("");

    $("preview").hidden = false;

    // Trimming needs ffmpeg, and only makes sense for one video at a time.
    $("trimBlock").hidden = isList || !S.hasFfmpeg;
    resetTrim();
    $("channelWrap").hidden = true;

    // Closed again for every new link, on purpose: it is not a mode, and
    // nothing set for the last video should carry into this one.
    $("moreBox").open = false;
    resetMore();
    fillFormats(info);
    // A playlist has no single format table, and a name for one file makes no
    // sense across forty of them.
    $("fmtSec").hidden = isList;
    $("optName").disabled = isList;
    $("optName").placeholder = isList ? "One name cannot cover a playlist"
                                      : "%(title)s [%(id)s].%(ext)s";

    if (isList) {
      renderPlaylist(info.entries);
    } else {
      selected = null;
      $("downloadBtn").disabled = false;
      $("downloadBtn").textContent = "Download";
    }
  }

  function resetTrim() {
    $("trimOn").checked = false;
    $("trimRow").hidden = true;
    $("exactCut").checked = false;
    $("toEnd").checked = true;
    ["startMin", "startSec", "endMin", "endSec"].forEach(function (id) {
      $(id).value = 0;
    });
    $("endMin").disabled = true;
    $("endSec").disabled = true;
  }

  /* -------------------------------------------------------------- channel */

  function renderChannel(info) {
    current = null;                       // nothing here is downloadable yet
    $("preview").hidden = true;
    $("playlistWrap").hidden = true;
    $("channelTitle").textContent = info.title || "Channel";

    $("channelTabs").innerHTML = (info.tabs || []).map(function (t, i) {
      return '<button type="button" class="chip" data-tab="' + i + '">' +
        esc(t.title) + (t.count ? " · " + t.count : "") + "</button>";
    }).join("");
    $("channelTabs").dataset.tabs = JSON.stringify(info.tabs || []);
    $("channelWrap").hidden = false;
  }

  $("channelTabs").addEventListener("click", function (e) {
    var chip = e.target.closest("[data-tab]");
    if (!chip) return;
    var tabs = JSON.parse($("channelTabs").dataset.tabs || "[]");
    var tab = tabs[parseInt(chip.dataset.tab, 10)];
    if (!tab || !tab.url) return;
    $("channelWrap").hidden = true;
    $("urlInput").value = tab.url;
    analyze(tab.url);
  });

  /* ------------------------------------------------------------- playlist */

  // Rows rendered at once. "Select all" still queues everything past this.
  var PL_LIMIT = 800;
  var plRows = [];          // entry index -> <li>
  var selected = null;      // Set of picked indices; null for a single video
  var lastPicked = -1;      // anchor for shift-click ranges
  var tailIncluded = false; // are the entries past PL_LIMIT still in?

  function tailCount() {
    if (!tailIncluded || !current || !current.entries) return 0;
    return Math.max(0, current.entries.length - PL_LIMIT);
  }

  function renderPlaylist(entries) {
    var shown = Math.min(entries.length, PL_LIMIT);
    selected = new Set();
    lastPicked = -1;
    // Entries past the render limit ride along with Select all. They only drop
    // when Select all is switched off - unticking one visible row must not
    // silently take a hundred others with it.
    tailIncluded = entries.length > shown;

    var html = [];
    for (var i = 0; i < shown; i++) {
      selected.add(i);
      html.push(
        '<li data-i="' + i + '">' +
          '<input type="checkbox" class="pl-check" checked>' +
          "<b>" + (i + 1) + "</b>" +
          "<span>" + esc(entries[i].title) + "</span>" +
          "<em>" + esc(fmtDuration(entries[i].duration)) + "</em>" +
          '<button type="button" class="pl-get" title="Download just this one">&#8595;</button>' +
        "</li>");
    }

    var box = $("playlistBox");
    box.innerHTML = html.join("");
    plRows = Array.prototype.slice.call(box.children);

    // A sort is offered only when the listing actually carries what it sorts
    // by. A flat listing often has no dates, and YouTube's Shorts tab has no
    // durations either - and an option that silently does nothing is worse
    // than no option at all.
    var head = entries.slice(0, shown);
    var dated = head.some(function (e) { return e.timestamp; });
    var timed = head.some(function (e) { return e.duration; });
    $("plSort").querySelector('[value="new"]').hidden = !dated;
    $("plSort").querySelector('[value="old"]').hidden = !dated;
    $("plSort").querySelector('[value="long"]').hidden = !timed;
    $("plSort").querySelector('[value="short"]').hidden = !timed;

    $("plFilter").value = "";
    $("plSort").value = "order";
    $("plFirst").value = "";
    $("playlistWrap").hidden = false;
    updateSelectionUi();
  }

  /* Sorting moves the rows that are on screen; it never renumbers anything.
     Each row keeps the index it was drawn with, so the selection, the
     download-just-this-one button and the not-drawn tail all keep meaning
     exactly what they meant before. */
  function sortPlaylist(how) {
    if (!plRows.length) return;

    var entryOf = function (li) { return current.entries[parseInt(li.dataset.i, 10)]; };
    var order = plRows.slice();

    if (how === "long" || how === "short") {
      order.sort(function (a, b) {
        var d = (entryOf(b).duration || 0) - (entryOf(a).duration || 0);
        return how === "long" ? d : -d;
      });
    } else if (how === "title") {
      order.sort(function (a, b) {
        return String(entryOf(a).title || "").localeCompare(String(entryOf(b).title || ""));
      });
    } else if (how === "new" || how === "old") {
      order.sort(function (a, b) {
        var d = (entryOf(b).timestamp || 0) - (entryOf(a).timestamp || 0);
        return how === "new" ? d : -d;
      });
    } else {
      order.sort(function (a, b) {
        return parseInt(a.dataset.i, 10) - parseInt(b.dataset.i, 10);
      });
    }

    var box = $("playlistBox");
    order.forEach(function (li) { box.appendChild(li); });
    lastPicked = -1;
    updateSelectionUi();
  }

  $("plSort").addEventListener("change", function (e) { sortPlaylist(e.target.value); });

  // "First 10" means the first ten of what is on screen right now - after any
  // filter and in whatever order is showing - because that is what someone
  // looking at the list means by it.
  $("plFirstGo").addEventListener("click", function () {
    var n = parseInt($("plFirst").value, 10);
    if (!n || n < 1) { toast("Type how many you want.", "bad"); return; }

    var rows = visibleRows();
    rows.forEach(function (li, at) {
      setPicked(parseInt(li.dataset.i, 10), at < n);
    });
    // Rows that were never drawn cannot be part of "the first N".
    if (tailIncluded) tailIncluded = false;
    updateSelectionUi();
  });

  $("plInvert").addEventListener("click", function () {
    visibleRows().forEach(function (li) {
      var i = parseInt(li.dataset.i, 10);
      setPicked(i, !selected.has(i));
    });
    updateSelectionUi();
  });

  function visibleRows() {
    return plRows.filter(function (li) { return !li.classList.contains("is-hidden"); });
  }

  function setPicked(i, on) {
    var li = plRows[i];
    if (!li) return;
    if (on) selected.add(i); else selected.delete(i);
    li.querySelector(".pl-check").checked = on;
    li.classList.toggle("is-off", !on);
  }

  function updateSelectionUi() {
    if (!selected || !current || !current.entries) return;

    var total = current.entries.length;
    var count = selected.size + tailCount();

    var rows = visibleRows();
    var pickedHere = rows.filter(function (li) {
      return selected.has(parseInt(li.dataset.i, 10));
    }).length;

    $("plAll").checked = rows.length > 0 && pickedHere === rows.length;
    $("plAll").indeterminate = pickedHere > 0 && pickedHere < rows.length;
    $("plCount").textContent = count + " of " + total + " selected";

    var btn = $("downloadBtn");
    btn.disabled = count === 0;
    btn.textContent = count === 0 ? "Nothing selected"
      : count === total ? "Download all " + total
      : "Download " + count + " selected";

    updateNote(total);
  }

  // Two things the count alone cannot say: rows that were never drawn, and
  // picked rows the filter is currently hiding. Both would otherwise make the
  // button disagree with what is on screen.
  function updateNote(total) {
    var shown = Math.min(total, PL_LIMIT);
    var lines = [];

    if (total > shown) {
      lines.push("Showing the first " + shown + " of " + total + ". The other " +
        (total - shown) + (tailIncluded ? " are included." : " are not selected."));
      // Sorting can only reach the rows that were drawn, so say so rather than
      // letting "Longest first" imply it searched the whole playlist.
      if ($("plSort").value !== "order") {
        lines.push("Sorted within those " + shown + ".");
      }
    }

    var buried = plRows.filter(function (li) {
      return li.classList.contains("is-hidden") &&
             selected.has(parseInt(li.dataset.i, 10));
    }).length;
    if (buried) {
      lines.push(buried + (buried === 1 ? " selected video is" : " selected videos are") +
        " hidden by the filter.");
    }

    var note = $("plNote");
    note.textContent = lines.join(" ");
    note.hidden = lines.length === 0;
  }

  function selectedItems() {
    var all = current.entries;
    var indices = [];

    selected.forEach(function (i) { indices.push(i); });
    indices.sort(function (a, b) { return a - b; });

    if (tailIncluded) {
      for (var i = PL_LIMIT; i < all.length; i++) indices.push(i);
    }

    return indices.map(function (i) {
      return {
        url: all[i].url, title: all[i].title,
        thumbnail: all[i].thumbnail, uploader: current.uploader
      };
    }).filter(function (e) { return e.url; });
  }

  function queueOne(i) {
    var entry = current.entries[i];
    if (!entry || !entry.url) { toast("That video has no link.", "bad"); return; }

    api("/api/add", {
      items: [{
        url: entry.url, title: entry.title,
        thumbnail: entry.thumbnail, uploader: current.uploader
      }],
      quality: quality
    }).then(function (res) {
      if (!res.ok) { toast(res.error || "Could not queue that.", "bad"); return; }
      toast("Queued: " + entry.title.slice(0, 42), "good");
      plRows[i].classList.add("is-queued");
      pollJobs();
    });
  }

  $("playlistBox").addEventListener("click", function (e) {
    var li = e.target.closest("li");
    if (!li) return;
    var i = parseInt(li.dataset.i, 10);

    if (e.target.closest(".pl-get")) { queueOne(i); return; }

    // The checkbox flips itself; a click anywhere else in the row toggles it.
    var box = li.querySelector(".pl-check");
    var on = e.target === box ? box.checked : !selected.has(i);

    if (e.shiftKey && lastPicked !== -1) {
      var from = Math.min(lastPicked, i), to = Math.max(lastPicked, i);
      for (var k = from; k <= to; k++) {
        if (plRows[k] && !plRows[k].classList.contains("is-hidden")) setPicked(k, on);
      }
    } else {
      setPicked(i, on);
    }

    lastPicked = i;
    updateSelectionUi();
  });

  $("plAll").addEventListener("change", function () {
    var on = this.checked;
    // Only what is on screen, so a filter plus Select all means "these".
    visibleRows().forEach(function (li) {
      setPicked(parseInt(li.dataset.i, 10), on);
    });
    // With no filter this really does mean the whole playlist, so the entries
    // past the render limit follow it.
    if (!$("plFilter").value.trim()) tailIncluded = on;
    lastPicked = -1;
    updateSelectionUi();
  });

  $("plFilter").addEventListener("input", function () {
    var needle = this.value.trim().toLowerCase();
    plRows.forEach(function (li) {
      var title = li.querySelector("span").textContent.toLowerCase();
      li.classList.toggle("is-hidden", needle !== "" && title.indexOf(needle) === -1);
    });
    lastPicked = -1;
    updateSelectionUi();
  });

  $("qualityChips").addEventListener("click", function (e) {
    var chip = e.target.closest(".chip");
    if (!chip) return;
    quality = chip.dataset.q;
    document.querySelectorAll("#qualityChips .chip").forEach(function (c) {
      c.classList.toggle("is-on", c === chip);
    });
    refreshCommand();
  });

  $("resetBtn").addEventListener("click", function () {
    current = null;
    selected = null;
    resetTrim();
    $("moreBox").open = false;
    resetMore();
    $("preview").hidden = true;
    $("playlistWrap").hidden = true;
    $("urlInput").value = "";
    $("urlInput").focus();
  });

  /* ------------------------------------------------------------ download */

  /* -------------------------------------------------- links in, many at once */

  // Deliberately not called URL_RE: the clipboard watcher further down already
  // owns that name, with an anchored single-URL pattern. Two `var`s of the same
  // name in one scope is one variable, and the later one wins - which silently
  // turned "twenty links at once" into "no link in that".
  var LINKS_RE = /https?:\/\/[^\s"'<>]+/g;

  function findLinks(text) {
    var found = (text || "").match(LINKS_RE) || [];
    var seen = {}, out = [];
    found.forEach(function (u) {
      u = u.replace(/[.,)\]]+$/, "");        // trailing punctuation from prose
      if (!seen[u]) { seen[u] = true; out.push(u); }
    });
    return out;
  }

  function queueMany(links) {
    var items = links.map(function (u) { return { url: u, title: u }; });
    api("/api/add", { items: items, quality: quality }).then(function (res) {
      if (!res.ok) { toast(res.error || "Could not queue those.", "bad"); return; }
      toast(res.warning || (res.added + " links queued"),
            res.warning ? "warn" : "good");
      $("urlInput").value = "";
      show("queue");
      pollJobs();
    });
  }

  // One link behaves as it always has. Several go straight to the queue -
  // asking someone to analyze twenty links one at a time is not a feature.
  function handleIncoming(text, viaDrop) {
    var links = findLinks(text);
    if (links.length > 1) { queueMany(links); return true; }
    if (links.length === 1 && viaDrop) {
      $("urlInput").value = links[0];
      $("analyzeBtn").click();
      return true;
    }
    return false;
  }

  $("urlInput").addEventListener("paste", function (e) {
    var text = (e.clipboardData || window.clipboardData).getData("text");
    if (findLinks(text).length > 1) {
      e.preventDefault();
      handleIncoming(text, false);
    }
  });

  // Dropping a link on the window. Both handlers must cancel the default or
  // WebView2 navigates away from the app and there is no way back.
  ["dragenter", "dragover"].forEach(function (name) {
    document.addEventListener(name, function (e) {
      e.preventDefault();
      document.body.classList.add("dropping");
    });
  });
  ["dragleave", "dragend"].forEach(function (name) {
    document.addEventListener(name, function (e) {
      if (e.relatedTarget === null) document.body.classList.remove("dropping");
    });
  });
  document.addEventListener("drop", function (e) {
    e.preventDefault();
    document.body.classList.remove("dropping");
    var dt = e.dataTransfer;
    if (!dt) return;
    var text = dt.getData("text/uri-list") || dt.getData("text/plain") || "";
    if (!handleIncoming(text, true)) toast("No link in that.", "bad");
  });

  /* ------------------------------------------------------------------ trim */

  // Minutes and seconds, driven by the arrows only. Seconds roll over into
  // minutes the way a clock does, so holding the arrow past 59 keeps working
  // instead of stopping dead.
  function stepperValue(id) {
    return parseInt($(id).value, 10) || 0;
  }

  function bindStepper(minId, secId) {
    var minBox = $(minId), secBox = $(secId);

    function normalise() {
      var mins = stepperValue(minId);
      var secs = stepperValue(secId);
      if (secs > 59) { mins += 1; secs = 0; }
      else if (secs < 0) {
        if (mins > 0) { mins -= 1; secs = 59; } else { secs = 0; }
      }
      minBox.value = Math.max(0, Math.min(599, mins));
      secBox.value = secs;
      updateTrimSummary();
    }

    [minBox, secBox].forEach(function (box) {
      box.addEventListener("input", normalise);
      // Typing is deliberately off; the arrows and the keyboard arrows work.
      box.addEventListener("keydown", function (e) {
        var allowed = ["ArrowUp", "ArrowDown", "Tab", "Escape"];
        if (allowed.indexOf(e.key) === -1) e.preventDefault();
      });
      box.addEventListener("paste", function (e) { e.preventDefault(); });
    });
  }

  bindStepper("startMin", "startSec");
  bindStepper("endMin", "endSec");

  function clock(mins, secs) {
    return mins + ":" + (secs < 10 ? "0" + secs : secs);
  }

  function trimTimes() {
    var startSecs = stepperValue("startMin") * 60 + stepperValue("startSec");
    var open = $("toEnd").checked;
    var endSecs = stepperValue("endMin") * 60 + stepperValue("endSec");
    return {
      start: startSecs ? clock(stepperValue("startMin"), stepperValue("startSec")) : "",
      end: open ? "" : clock(stepperValue("endMin"), stepperValue("endSec")),
      startSecs: startSecs,
      endSecs: open ? Infinity : endSecs,
      open: open
    };
  }

  function updateTrimSummary() {
    var t = trimTimes();
    var box = $("trimSummary");
    if (!box) return;

    if (!t.startSecs && t.open) {
      box.textContent = "The whole video.";
      return;
    }
    if (!t.open && t.endSecs <= t.startSecs) {
      box.textContent = "The end has to come after the start.";
      return;
    }
    var from = clock(stepperValue("startMin"), stepperValue("startSec"));
    box.textContent = t.open
      ? from + " to the end"
      : from + " to " + t.end + "  (" + (t.endSecs - t.startSecs) + "s)";
    refreshCommand();
  }

  function readTrim() {
    var t = trimTimes();
    if (!t.startSecs && t.open) {
      toast("Set a start time, or an end time.", "bad");
      return null;
    }
    if (!t.open && t.endSecs <= t.startSecs) {
      toast("The end has to come after the start.", "bad");
      return null;
    }
    return { start: t.start, end: t.end, exact: $("exactCut").checked };
  }

  $("trimOn").addEventListener("change", function (e) {
    $("trimRow").hidden = !e.target.checked;
    if (e.target.checked) updateTrimSummary();
    refreshCommand();
  });

  /* -------------------------------------------------------- more options */

  /* Rule this whole panel obeys: it may add controls, but it never changes
     what the closed state does. Nothing here is remembered - it belongs to
     one download and is cleared the moment the panel closes. */

  var pickedFormat = "";
  var onceDir = "";

  function resetMore() {
    pickedFormat = "";
    onceDir = "";
    $("optName").value = "";
    $("optDir").dataset.dir = "";
    $("optDir").textContent = "Default folder";
    $("optCookies").value = "";
    $("optClient").value = "";
    $("optAudioLang").value = "";
    $("optSubLang").value = "";
    $("fmtPick").textContent = "Using the quality above.";
    document.querySelectorAll("#fmtTable tr.is-on").forEach(function (tr) {
      tr.classList.remove("is-on");
    });
  }

  // Empty whenever the panel is closed, which is what makes "it resets when
  // you close it" true rather than a claim in a comment.
  function moreOpts() {
    if (!$("moreBox").open) return {};
    var o = {};
    if (pickedFormat) o.format_id = pickedFormat;
    if ($("optAudioLang").value) o.audio_lang = $("optAudioLang").value;
    if ($("optSubLang").value) o.sub_langs = $("optSubLang").value;
    if ($("optName").value.trim()) o.outtmpl = $("optName").value.trim();
    if (onceDir) o.dest_dir = onceDir;
    if ($("optClient").value) o.player_client = $("optClient").value;
    if ($("optCookies").value === "off") o.no_cookies = true;
    return o;
  }

  function fillFormats(info) {
    var rows = info.formats || [];
    var table = $("fmtTable");
    if (!rows.length) {
      table.innerHTML = '<tbody><tr><td class="kind">This site does not list ' +
        "separate formats.</td></tr></tbody>";
    } else {
      table.innerHTML =
        "<thead><tr><th>id</th><th>kind</th><th>size</th><th>fps</th>" +
        "<th>video</th><th>audio</th><th>bitrate</th><th>note</th></tr></thead><tbody>" +
        rows.map(function (f) {
          var kind = f.kind === "av" ? f.res
            : f.kind === "video" ? f.res + " (no sound)"
            : "audio" + (f.lang ? " · " + esc(f.lang) : "");
          return '<tr data-fmt="' + esc(f.id) + '">' +
            "<td>" + esc(f.id) + "</td>" +
            '<td class="' + (f.sr ? "sr" : "") + '">' + esc(kind) +
            (f.sr ? " · AI-enlarged" : "") + "</td>" +
            "<td>" + esc(f.size || "—") + "</td>" +
            "<td>" + (f.fps ? esc(f.fps) : "—") + "</td>" +
            '<td class="kind">' + esc(f.vcodec || "—") + "</td>" +
            '<td class="kind">' + esc(f.acodec || "—") + "</td>" +
            "<td>" + (f.tbr ? esc(f.tbr) + "k" : "—") + "</td>" +
            '<td class="kind">' + esc(f.note || "") + "</td></tr>";
        }).join("") + "</tbody>";
    }

    var langs = info.audio_langs || [];
    $("optAudioLang").innerHTML = '<option value="">Default</option>' +
      langs.map(function (l) { return '<option value="' + esc(l) + '">' + esc(l) + "</option>"; }).join("");
    $("audioLangField").hidden = langs.length === 0;

    var subs = info.sub_langs || [];
    $("optSubLang").innerHTML = '<option value="">Off for this download</option>' +
      subs.map(function (s) {
        return '<option value="' + esc(s.code) + '">' + esc(s.code) +
          (s.auto ? " (automatic)" : "") + "</option>";
      }).join("");
    $("optSubField").hidden = subs.length === 0;

    // Shown only when there is genuinely a choice to make.
    $("langSec").hidden = langs.length === 0 && subs.length === 0;

    $("cookieNow").textContent =
      settings.cookies_file ? "Right now: your cookies.txt file."
      : settings.cookies_signin ? "Right now: the saved browser session, if there is one for this site."
      : (settings.cookies_browser && settings.cookies_browser !== "none")
        ? "Right now: cookies read from " + settings.cookies_browser + "."
        : "Right now: no sign-in is sent.";
  }

  $("fmtTable").addEventListener("click", function (e) {
    var tr = e.target.closest("tr[data-fmt]");
    if (!tr) return;
    var same = tr.classList.contains("is-on");
    document.querySelectorAll("#fmtTable tr.is-on").forEach(function (r) {
      r.classList.remove("is-on");
    });
    // Clicking the chosen row again gives the quality chips back.
    pickedFormat = same ? "" : tr.dataset.fmt;
    if (!same) tr.classList.add("is-on");
    $("fmtPick").textContent = pickedFormat
      ? "Format " + pickedFormat + " — the quality above is ignored."
      : "Using the quality above.";
    refreshCommand();
  });

  $("optDir").addEventListener("click", function () {
    api("/api/choose-folder-once", {}).then(function (res) {
      if (res.cancelled) return;
      if (!res.ok) { toast(res.error || "Could not open the picker.", "bad"); return; }
      onceDir = res.dir;
      $("optDir").textContent = res.dir.split("\\").pop() || res.dir;
      $("optDir").title = res.dir;
      refreshCommand();
    });
  });

  var cmdTimer = null;
  function refreshCommand() {
    if (!$("moreBox").open || !current || current.kind === "channel") return;
    clearTimeout(cmdTimer);
    cmdTimer = setTimeout(function () {
      var body = {
        url: current.kind === "playlist" ? (current.entries[0] || {}).url : current.url,
        quality: quality,
        opts: moreOpts()
      };
      if (current.kind !== "playlist" && $("trimOn").checked) {
        var t = trimTimes();
        if (t) { body.start = t.start; body.end = t.end; body.exact = $("exactCut").checked; }
      }
      api("/api/command", body).then(function (res) {
        $("cmdBox").textContent = res.ok ? res.command : (res.error || "—");
      });
    }, 180);
  }

  ["optName", "optCookies", "optClient", "optAudioLang", "optSubLang"].forEach(function (id) {
    $(id).addEventListener("change", refreshCommand);
  });
  $("optName").addEventListener("input", refreshCommand);

  $("moreBox").addEventListener("toggle", function () {
    if ($("moreBox").open) {
      refreshCommand();
    } else {
      resetMore();
    }
  });

  $("cmdCopy").addEventListener("click", function () {
    copyText($("cmdBox").textContent || "");
  });

  $("toEnd").addEventListener("change", function (e) {
    $("endMin").disabled = e.target.checked;
    $("endSec").disabled = e.target.checked;
    updateTrimSummary();
  });

  $("downloadBtn").addEventListener("click", function () {
    if (!current) return;

    var items = current.kind === "playlist" ? selectedItems() : [{
      url: current.url, title: current.title,
      thumbnail: current.thumbnail, uploader: current.uploader
    }];

    if (!items.length) { toast("Nothing selected.", "bad"); return; }

    var body = { items: items, quality: quality, opts: moreOpts() };
    if (current.kind !== "playlist" && $("trimOn").checked) {
      var range = readTrim();
      if (range === null) return;             // readTrim already complained
      body.start = range.start;
      body.end = range.end;
      body.exact = range.exact;
    }

    api("/api/add", body).then(function (res) {
      if (!res.ok) { toast(res.error || "Could not queue that.", "bad"); return; }
      // A low-space warning matters more than the confirmation it replaces.
      toast(res.warning || (res.added > 1 ? res.added + " videos queued" : "Queued"),
            res.warning ? "warn" : "good");
      $("preview").hidden = true;
      $("playlistWrap").hidden = true;
      $("urlInput").value = "";
      current = null;
      selected = null;
      show("queue");
      pollJobs();
    });
  });

  /* --------------------------------------------------------------- queue */

  var STATE_TEXT = {
    queued: "queued", starting: "starting", downloading: "downloading",
    converting: "converting", done: "done", error: "failed",
    cancelled: "cancelled", paused: "waiting"
  };

  function renderJobs(jobs) {
    var box = $("jobs");
    var active = jobs.filter(function (j) {
      return j.status === "downloading" || j.status === "converting" ||
             j.status === "starting" || j.status === "queued" || j.status === "paused";
    }).length;

    // Left over from the last time Riplox was closed. They do not start on
    // their own - the user says when.
    var waiting = jobs.filter(function (j) { return j.status === "paused"; }).length;
    $("resumeBar").hidden = waiting === 0;
    if (waiting) {
      $("resumeText").textContent = waiting === 1
        ? "1 download was still waiting when you last closed Riplox."
        : waiting + " downloads were still waiting when you last closed Riplox.";
    }

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

  /* Drawn rather than typed: the row used to mix text glyphs with colour
     emoji, so ✕ and 🗑 sat at different weights and different sizes on the
     same row and only one of them followed the theme. */
  function ic(body, filled) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true" fill="' +
      (filled ? "currentColor" : "none") + '" stroke="' +
      (filled ? "none" : "currentColor") +
      '" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' +
      body + "</svg>";
  }

  var ICON = {
    play:   ic('<path d="M8 5.1v13.8L19 12z"/>', true),
    folder: ic('<path d="M3 7a2 2 0 0 1 2-2h3.6l2 2.4H19a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
    retry:  ic('<path d="M20.5 12a8.5 8.5 0 1 1-2.7-6.2"/><path d="M20.5 3.8v4.4h-4.4"/>'),
    copy:   ic('<rect x="9" y="9" width="11.5" height="11.5" rx="2.2"/><path d="M5 15.5V5.8A2 2 0 0 1 7 3.8h9"/>'),
    stop:   ic('<path d="M6.5 6.5 17.5 17.5M17.5 6.5 6.5 17.5"/>'),
    pause:  ic('<path d="M9.3 5.2v13.6M14.7 5.2v13.6"/>'),
    fix:    ic('<path d="M12 2.8v3.4M12 17.8v3.4M2.8 12h3.4M17.8 12h3.4M5.5 5.5l2.4 2.4M16.1 16.1l2.4 2.4M18.5 5.5l-2.4 2.4M7.9 16.1l-2.4 2.4"/>'),
    trash:  ic('<path d="M4 7h16M10 7V4.8h4V7M6.4 7l.9 12.3A1.8 1.8 0 0 0 9.1 21h5.8a1.8 1.8 0 0 0 1.8-1.7L17.6 7"/>'),
    note:   ic('<path d="M9.5 17.5V6l10-2v11.5"/><ellipse cx="7" cy="17.5" rx="2.5" ry="2.2"/><ellipse cx="17" cy="15.5" rx="2.5" ry="2.2"/>')
  };

  var ACTIONS = {
    done: [["open", ICON.play, "Play file", "go"],
           ["reveal", ICON.folder, "Show in folder", ""]],
    error: [["retry", ICON.retry, "Try again", "go"],
            ["log", ICON.copy, "Copy error details", ""]],
    cancelled: [["retry", ICON.retry, "Try again", "go"]],
    paused: [["retry", ICON.play, "Resume", "go"]],
    busy: [["cancel", ICON.stop, "Stop", "stop"]]
  };

  // Pausing only means anything while bytes are moving.
  var PAUSE_BTN = ["pause", ICON.pause, "Pause — keeps what has downloaded", ""];

  function actionsFor(j) {
    var set = (ACTIONS[j.status] || ACTIONS.busy).slice();
    if (j.status === "downloading") set.unshift(PAUSE_BTN);
    // Offered only where the helper would actually have made a difference.
    if (j.botcheck && !S.hasPotoken) {
      set.unshift(["fix", ICON.fix, "Fix this and try again", "go"]);
    }
    return set.concat([["remove", ICON.trash, "Remove", ""]]);
  }

  function updateRow(row, j) {
    if (j.title !== row.lastTitle) {
      row.title.textContent = j.title;
      row.title.title = j.title;
      row.lastTitle = j.title;
    }

    var bits = [["state", STATE_TEXT[j.status] || j.status]];
    if (j.status === "downloading") {
      // A trimmed download is cut by ffmpeg, which reports where it has got
      // to rather than a percentage. Show what is true instead of 0.0%.
      if (j.stage) {
        bits.push(["", j.stage]);
        if (j.percent >= 1) bits.push(["", j.percent.toFixed(0) + "%"]);
      } else {
        bits.push(["", j.percent.toFixed(1) + "%"]);
      }
      if (j.speed) bits.push(["", j.speed]);
      if (j.eta) bits.push(["", "ETA " + j.eta]);
      // Worth seeing while it runs, not only once it lands.
      if (j.clip) bits.push(["clip", j.clip]);
    } else {
      bits.push(["", j.qualityLabel]);
      if (j.clip) bits.push(["clip", j.clip]);
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

    // An open-ended clip has no total to measure against, so the bar says
    // "working" rather than sitting at zero as if nothing were happening.
    var unmeasurable = j.status === "downloading" && !!j.stage && j.percent < 1;
    row.fill.classList.toggle("working", unmeasurable);
    row.fill.style.width = unmeasurable
      ? "100%" : ((j.status === "done" ? 100 : j.percent) + "%");

    if (j.error) {
      row.error.textContent = j.error;
      row.error.hidden = false;
    } else if (!row.error.hidden) {
      row.error.hidden = true;
    }

    // The button set depends on more than the status now, so the cache key
    // has to as well - otherwise "Fix this" never appears.
    var shape = j.status + (j.botcheck ? "!" : "");
    if (shape !== row.lastStatus) {
      row.el.className = "job " + j.status;

      row.actions.textContent = "";
      actionsFor(j).forEach(function (a) {
        var btn = document.createElement("button");
        btn.className = "icon-btn " + a[3];
        btn.dataset.act = a[0];
        btn.dataset.id = j.id;
        btn.title = a[2];
        btn.setAttribute("aria-label", a[2]);
        btn.innerHTML = a[1];      // our own icon table, never user text
        row.actions.appendChild(btn);
      });
      row.lastStatus = shape;
    }
  }

  $("jobs").addEventListener("click", function (e) {
    var btn = e.target.closest("[data-act]");
    if (!btn) return;
    var id = btn.dataset.id, act = btn.dataset.act;

    if (act === "log") {
      api("/api/job-log", { id: id }).then(function (r) {
        if (!r || !r.log) { toast("No details were kept for that one.", "bad"); return; }
        copyText(r.log);
      });
      return;
    }

    if (act === "fix") {
      btn.disabled = true;
      toast("Setting up the YouTube helper…");
      api("/api/fix-botcheck", { id: id }).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { toast(r.error || "Could not set that up.", "bad"); return; }
        S.hasPotoken = true;
        toast("Helper ready — trying again", "good");
        pollJobs();
      });
      return;
    }

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
      S.hasPotoken = res.hasPotoken;
      S.hasFfmpeg = res.hasFfmpeg;
      var active = renderJobs(res.jobs);

      if (!analyzing) {
        $("engineStatus").className = "status" + (active ? " busy" : "");
        $("engineLabel").textContent = active ? active + " active" : "ready";
      }

      clearTimeout(pollTimer);
      var visible = $("view-queue").classList.contains("is-active");
      // A heartbeat that never stops. This used to give up entirely whenever
      // nothing was running, so a link arriving from a phone - or a queue
      // restored at startup - sat there unseen until you switched tabs.
      var wait = active > 0 ? (visible ? 700 : 1600) : (visible ? 2500 : 6000);
      pollTimer = setTimeout(pollJobs, wait);
      return active;
    }).catch(function () { return 0; });
  }

  /* ------------------------------------------------------------- library */

  // Kept in memory so filtering and sorting never has to ask the disk again.
  var libraryItems = [];
  var libSource = "all";

  /* Prefer the site recorded at download time. Older entries predate that, so
     the folder is the only clue left - but folder-per-site is off by default,
     so those folders are usually just wherever the file landed.

     Two things came out of that and both showed up on the shelf as junk: a
     folder called "Youtube" sitting next to a recorded "YouTube" as two
     separate shelves, and shelves named "out" or "Desktop" that are not sites
     at all. So a folder name only earns a shelf if some recorded download
     agrees it is a real place; the rest are Other. */
  // Written the same way the engine writes them, so "Youtube" the folder and
  // "YouTube" the recorded site end up on one shelf instead of two.
  var siteNames = {};
  (S.sites || []).forEach(function (name) { siteNames[name.toLowerCase()] = name; });

  function sourceOf(item) {
    if (item.site) return item.site;

    var parts = (item.filepath || "").replace(/\//g, "\\").split("\\");
    var folder = parts.length > 1 ? parts[parts.length - 2] : "";
    if (!folder || /^riplox$/i.test(folder) || /^[a-z]:$/i.test(folder)) return "Other";

    return siteNames[folder.toLowerCase()] || "Other";
  }

  function loadHistory() {
    api("/api/history").then(function (res) {
      libraryItems = (res.history || []).map(function (h) {
        h._source = sourceOf(h);
        h._bytes = bytesOf(h.size);
        return h;
      });
      $("libraryEmpty").hidden = libraryItems.length > 0;
      $("libControls").hidden = libraryItems.length === 0;
      renderChips();
      renderHistory();
    });
  }

  function bytesOf(text) {
    var m = /([\d.]+)\s*(B|KB|MB|GB|TB)/i.exec(text || "");
    if (!m) return 0;
    var mult = { b: 1, kb: 1e3, mb: 1e6, gb: 1e9, tb: 1e12 };
    return parseFloat(m[1]) * (mult[m[2].toLowerCase()] || 1);
  }

  function renderChips() {
    var counts = {};
    libraryItems.forEach(function (h) {
      counts[h._source] = (counts[h._source] || 0) + 1;
    });
    var names = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; });
    if (libSource !== "all" && names.indexOf(libSource) === -1) libSource = "all";

    $("libChips").innerHTML =
      ['<button type="button" class="chip' + (libSource === "all" ? " is-on" : "") +
       '" data-src="all">All · ' + libraryItems.length + "</button>"]
        .concat(names.map(function (n) {
          return '<button type="button" class="chip' +
            (n === libSource ? " is-on" : "") + '" data-src="' + esc(n) + '">' +
            esc(n) + " · " + counts[n] + "</button>";
        })).join("");
  }

  function renderHistory() {
    var term = ($("libSearch").value || "").trim().toLowerCase();
    var sort = $("libSort").value;

    var shown = libraryItems.filter(function (h) {
      if (libSource !== "all" && h._source !== libSource) return false;
      return !term || (h.title || "").toLowerCase().indexOf(term) !== -1;
    });

    shown.sort(function (a, b) {
      if (sort === "name") return (a.title || "").localeCompare(b.title || "");
      if (sort === "big") return b._bytes - a._bytes;
      var cmp = (a.when || "").localeCompare(b.when || "");
      return sort === "old" ? cmp : -cmp;
    });

    $("libCount").textContent = shown.length === libraryItems.length
      ? shown.length + " files"
      : shown.length + " of " + libraryItems.length;

    $("history").innerHTML = shown.map(function (h) {
      var when = (h.when || "").replace("T", " ").slice(0, 16);
      return '<div class="hrow" data-path="' + esc(h.filepath) + '">' +
        (h.thumbnail ? '<img src="' + esc(h.thumbnail) + '" alt="" onerror="this.style.visibility=\'hidden\'">' : "<div></div>") +
        '<div><div class="t">' + esc(h.title) + '</div>' +
        '<div class="m">' + esc(labels[h.quality] || h.quality || "") +
        (h.size ? " · " + esc(h.size) : "") + " · " + esc(when) + "</div></div>" +
        '<button class="icon-btn" data-mp3="1" title="Convert to MP3" aria-label="Convert to MP3">' + ICON.note + "</button>" +
        '<button class="icon-btn go" data-open="1" title="Play file" aria-label="Play file">' + ICON.play + "</button>" +
        "</div>";
    }).join("");
  }

  $("libSearch").addEventListener("input", renderHistory);
  $("libSort").addEventListener("change", renderHistory);
  $("libChips").addEventListener("click", function (e) {
    var chip = e.target.closest("[data-src]");
    if (!chip) return;
    libSource = chip.dataset.src;
    renderChips();
    renderHistory();
  });

  $("history").addEventListener("click", function (e) {
    var row = e.target.closest(".hrow");
    if (!row) return;
    var path = row.dataset.path;
    if (!path) { toast("File path unknown.", "bad"); return; }

    // The deliberate way is the Convert page; this is the in-passing way.
    if (e.target.closest("[data-mp3]")) {
      api("/api/convert", { paths: [path], format: "mp3", quality: "high",
                            beside: true }).then(function (r) {
        if (!r.ok) { toast(r.error || "Could not start that.", "bad"); return; }
        toast("Converting to MP3", "good");
        show("queue");
        pollJobs();
      });
      return;
    }

    api("/api/open", { path: path, reveal: !e.target.closest("[data-open]") }).then(function (r) {
      if (!r.ok) toast(r.error || "Could not open it.", "bad");
    });
  });

  $("openFolder").addEventListener("click", function () {
    api("/api/open", {}).then(function (r) {
      if (!r.ok) toast(r.error || "Could not open the folder.", "bad");
    });
  });

  /* --------------------------------------------------------------- convert */

  var convFiles = [];        // {path, name, size, picked}
  var convReady = false;

  function loadConvert() {
    if (!convReady) {
      api("/api/convert/formats").then(function (res) {
        if (!res.ok) return;
        $("convFormat").innerHTML = res.formats.map(function (f) {
          return '<option value="' + f.id + '">' + esc(f.label) + "</option>";
        }).join("");
        $("convQuality").innerHTML = res.quality.map(function (q) {
          return '<option value="' + q.id + '">' + esc(q.label) + "</option>";
        }).join("");
        syncQualityBox();
      });
      convReady = true;
    }

    api("/api/convert/library").then(function (res) {
      var known = {};
      convFiles.forEach(function (f) { known[f.path] = f.picked; });
      convFiles = (res.files || []).map(function (f) {
        f.picked = !!known[f.path];
        return f;
      }).concat(convFiles.filter(function (f) { return f.fromPC; }));
      renderConvert();
    });
  }

  // FLAC and WAV have no bitrate to choose, so the box would be a lie.
  function syncQualityBox() {
    var fmt = $("convFormat").value;
    $("convQuality").disabled = (fmt === "flac" || fmt === "wav");
  }

  function renderConvert() {
    var list = $("convList");
    $("convEmpty").hidden = convFiles.length > 0;

    list.innerHTML = convFiles.map(function (f, i) {
      return '<li class="conv-row"><label>' +
        '<input type="checkbox" data-i="' + i + '"' + (f.picked ? " checked" : "") + ">" +
        '<span class="conv-name">' + esc(f.name) + "</span>" +
        '<span class="conv-size">' + esc(f.size || "") + "</span>" +
        (f.fromPC ? '<span class="conv-tag">from PC</span>' : "") +
        "</label></li>";
    }).join("");

    var picked = convFiles.filter(function (f) { return f.picked; }).length;
    $("convStart").disabled = picked === 0;
    $("convStart").textContent = picked ? "Convert " + picked : "Convert";
    $("convAll").checked = picked > 0 && picked === convFiles.length;
  }

  $("convList").addEventListener("change", function (e) {
    var box = e.target.closest("[data-i]");
    if (!box) return;
    convFiles[parseInt(box.dataset.i, 10)].picked = box.checked;
    renderConvert();
  });

  $("convAll").addEventListener("change", function (e) {
    convFiles.forEach(function (f) { f.picked = e.target.checked; });
    renderConvert();
  });

  $("convFormat").addEventListener("change", syncQualityBox);

  $("convPick").addEventListener("click", function () {
    api("/api/convert/pick", {}).then(function (res) {
      if (res.cancelled) return;
      if (!res.ok) { toast(res.error || "Could not open the picker.", "bad"); return; }
      var have = {};
      convFiles.forEach(function (f) { have[f.path] = true; });
      (res.files || []).forEach(function (f) {
        if (have[f.path]) return;
        f.picked = true;
        f.fromPC = true;
        convFiles.push(f);
      });
      renderConvert();
    });
  });

  $("convStart").addEventListener("click", function () {
    var paths = convFiles.filter(function (f) { return f.picked; })
      .map(function (f) { return f.path; });
    if (!paths.length) return;

    api("/api/convert", {
      paths: paths,
      format: $("convFormat").value,
      quality: $("convQuality").value,
      beside: $("convBeside").checked
    }).then(function (res) {
      if (!res.ok) { toast(res.error || "Could not start.", "bad"); return; }
      toast(res.added > 1 ? res.added + " files queued" : "Converting", "good");
      convFiles.forEach(function (f) { f.picked = false; });
      renderConvert();
      show("queue");
      pollJobs();
    });
  });

  /* -------------------------------------------------------- settings backup */

  $("settingsExport").addEventListener("click", function () {
    api("/api/settings/export", {}).then(function (r) {
      if (r.cancelled) return;
      toast(r.ok ? "Settings saved" : (r.error || "Could not save."),
            r.ok ? "good" : "bad");
    });
  });

  $("settingsImport").addEventListener("click", function () {
    api("/api/settings/import", {}).then(function (r) {
      if (r.cancelled) return;
      if (!r.ok) { toast(r.error || "Could not restore.", "bad"); return; }
      var extra = (r.remapped && r.remapped.length)
        ? " (download folder reset — the old one is not on this PC)" : "";
      toast("Settings restored" + extra, r.remapped && r.remapped.length ? "warn" : "good");
      setTimeout(function () { location.reload(); }, 1400);
    });
  });

  /* --------------------------------------------------------------- updates */
  // Tells you, and nothing else. Riplox never installs anything by itself.

  var updatePage = "";

  function showUpdate(res) {
    if (!res || !res.newer) return;
    updatePage = res.page || "";
    $("updateText").textContent =
      "Riplox " + res.latest + " is out. You have " + (S.version || "this build") + ".";
    $("updateBar").hidden = false;
  }

  $("updateOpen").addEventListener("click", function () {
    if (updatePage) api("/api/open-url", { url: updatePage });
  });

  if (settings.check_updates !== false) {
    // Quietly, once a day, and never in the way of the first screen.
    setTimeout(function () {
      api("/api/check-update", {}).then(showUpdate);
    }, 4000);
  }

  $("checkUpdateNow").addEventListener("click", function () {
    toast("Checking…");
    api("/api/check-update", { force: true }).then(function (res) {
      if (!res.ok) { toast("Could not reach GitHub.", "bad"); return; }
      if (res.newer) { showUpdate(res); toast("Riplox " + res.latest + " is out", "good"); }
      else toast("You are on the newest version", "good");
    });
  });

  $("resumeAll").addEventListener("click", function () {
    api("/api/resume-all", {}).then(function (res) {
      toast(res.resumed ? "Resumed " + res.resumed : "Nothing to resume",
            res.resumed ? "good" : "");
      pollJobs();
    });
  });

  $("clearHistory").addEventListener("click", function () {
    api("/api/history/clear", {}).then(function () {
      loadHistory();
      toast("History cleared");
    });
  });

  /* --------------------------------------------------------------- watch */

  var watchTimer = null;
  var watchState = null;

  function when(seconds) {
    if (!seconds) return "not yet";
    return ago(seconds);
  }

  function renderWatch(s) {
    watchState = s;
    $("setWatch").classList.toggle("on", s.on);
    $("watchState").textContent = !s.on ? "off"
      : s.sweeping ? "checking all"
      : s.busy ? "checking " + s.busy
      : "on";
    $("watchState").classList.toggle("on", s.on);

    $("watchHours").value = String(s.hours);
    $("watchCount").textContent = s.items.length
      ? s.items.length + " of " + s.max : "";
    $("watchCheckAll").disabled = !s.items.length || s.sweeping;

    $("watchEmpty").hidden = s.items.length > 0;
    $("watchList").innerHTML = s.items.map(function (it) {
      var fresh = it.new || [];

      var videos = fresh.map(function (v) {
        return '<div class="new-row">' +
          (v.thumbnail ? '<img src="' + esc(v.thumbnail) + '" alt="" loading="lazy">'
                       : '<div class="new-thumb"></div>') +
          '<div class="new-what"><b>' + esc(v.title) + "</b>" +
            "<span>" + esc(fmtDuration(v.duration) || "") + "</span></div>" +
          '<button class="primary small" data-get="' + esc(v.url) + '" data-item="' +
            esc(it.id) + '" data-video="' + esc(v.id) + '">Download</button>' +
          '<button class="ghost small" data-skip="' + esc(it.id) + '" data-video="' +
            esc(v.id) + '">Skip</button>' +
          "</div>";
      }).join("");

      var trouble = it.error
        ? '<p class="warn watch-err">' + esc(it.error) +
          (it.botcheck ? " — see the note at the bottom of this page." : "") + "</p>"
        : "";

      return '<div class="watch-item' + (it.paused ? " is-paused" : "") + '">' +
        '<div class="watch-head">' +
          (it.thumbnail ? '<img class="watch-thumb" src="' + esc(it.thumbnail) +
                          '" alt="" loading="lazy">' : '<div class="watch-thumb"></div>') +
          '<div class="who"><b>' + esc(it.title) +
            (it.paused ? ' <em class="tag">paused</em>' : "") + "</b>" +
            '<span>' + esc(it.kind) + " · checked " + when(it.checked) +
            (fresh.length ? " · " + fresh.length + " new" : "") + "</span></div>" +
          '<button class="ghost small" data-check="' + esc(it.id) + '">Check</button>' +
          '<button class="ghost small" data-wpause="' + esc(it.id) + '" data-to="' +
            (it.paused ? "0" : "1") + '">' + (it.paused ? "Resume" : "Pause") + "</button>" +
          '<button class="ghost small danger" data-drop="' + esc(it.id) +
            '">Remove</button>' +
        "</div>" + trouble +
        (fresh.length
          ? '<div class="new-list">' + videos +
            '<button class="ghost small" data-skip="' + esc(it.id) +
            '">Clear all</button></div>'
          : "") +
        "</div>";
    }).join("");

    $("watchBadge").textContent = s.new;
    $("watchBadge").hidden = !s.new;
  }

  function loadWatch() {
    api("/api/watch/state", {}).then(function (res) {
      if (res.ok) renderWatch(res.state);
    });
    clearTimeout(watchTimer);
    watchTimer = setTimeout(function () {
      if ($("view-watch").classList.contains("is-active")) loadWatch();
    }, 4000);
  }

  /* The gate. Watching is the only thing in Riplox that talks to a site on its
     own schedule, and the only thing that can get a signed-in Google account
     limited - so the warning cannot be clicked past while it is still being
     read. Fifteen seconds, then the button works. */
  var warnTimer = null;
  function askFirst(then) {
    if (settings.watch_ack) { then(); return; }

    var left = 15;
    var ok = $("watchWarnOk");
    ok.disabled = true;
    ok.textContent = "I understand (" + left + ")";
    $("watchWarn").hidden = false;

    clearInterval(warnTimer);
    warnTimer = setInterval(function () {
      left -= 1;
      if (left > 0) { ok.textContent = "I understand (" + left + ")"; return; }
      clearInterval(warnTimer);
      ok.disabled = false;
      ok.textContent = "I understand";
    }, 1000);

    $("watchWarn")._then = then;
  }

  $("watchWarnOk").addEventListener("click", function () {
    if ($("watchWarnOk").disabled) return;
    $("watchWarn").hidden = true;
    clearInterval(warnTimer);
    saveSetting({ watch_ack: true });
    var then = $("watchWarn")._then;
    if (then) then();
  });

  $("watchWarnNo").addEventListener("click", function () {
    $("watchWarn").hidden = true;
    clearInterval(warnTimer);
    $("setWatch").classList.toggle("on", !!(watchState && watchState.on));
  });

  $("setWatch").addEventListener("click", function () {
    var on = !$("setWatch").classList.contains("on");
    if (!on) {
      saveSetting({ watch: false }).then(loadWatch);
      return;
    }
    askFirst(function () {
      saveSetting({ watch: true }).then(loadWatch);
    });
  });

  $("watchHours").addEventListener("change", function (e) {
    saveSetting({ watch_hours: parseInt(e.target.value, 10) })
      .then(function () { toast("Saved"); loadWatch(); });
  });

  function watchAdd(url, kind) {
    $("watchError").hidden = true;
    $("watchPick").hidden = true;
    if (!url) { toast("Paste a channel or playlist link first.", "bad"); return; }

    toast("Reading that link…");
    api("/api/watch/add", { url: url, kind: kind }).then(function (res) {
      if (!res.ok) {
        $("watchError").textContent = res.error || "Could not read that link.";
        $("watchError").hidden = false;
        return;
      }
      if (res.result && res.result.choose) {
        // A channel front page answers with its tabs. Watching that would
        // never see a new video, so the sections are offered instead.
        $("watchTabs").innerHTML = (res.result.tabs || []).map(function (t) {
          return '<button type="button" class="chip" data-wtab="' + esc(t.url) + '">' +
            esc(t.title) + (t.count ? " · " + t.count : "") + "</button>";
        }).join("");
        $("watchPick").hidden = false;
        return;
      }
      $("watchUrl").value = "";
      toast("Watching. New videos will show up here.", "good");
      renderWatch(res.state);
    });
  }

  $("addChannel").addEventListener("click", function () {
    askFirst(function () { watchAdd($("watchUrl").value.trim(), "channel"); });
  });

  $("addPlaylist").addEventListener("click", function () {
    askFirst(function () { watchAdd($("watchUrl").value.trim(), "playlist"); });
  });

  $("watchTabs").addEventListener("click", function (e) {
    var chip = e.target.closest("[data-wtab]");
    if (chip) watchAdd(chip.dataset.wtab, "channel");
  });

  $("watchCheckAll").addEventListener("click", function () {
    api("/api/watch/check", {}).then(function (res) {
      toast("Checking, one at a time…");
      if (res.state) renderWatch(res.state);
    });
  });

  $("watchList").addEventListener("click", function (e) {
    var get = e.target.closest("[data-get]");
    if (get) {
      api("/api/add", { items: [{ url: get.dataset.get }], quality: quality })
        .then(function (res) {
          if (!res.ok) { toast(res.error || "Could not queue that.", "bad"); return; }
          toast("Queued", "good");
          pollJobs();
          // Off the new list once it is on its way - it is not new any more.
          api("/api/watch/seen", { id: get.dataset.item, video: get.dataset.video })
            .then(function (r) { if (r.state) renderWatch(r.state); });
        });
      return;
    }

    var skip = e.target.closest("[data-skip]");
    if (skip) {
      api("/api/watch/seen", { id: skip.dataset.skip, video: skip.dataset.video || "" })
        .then(function (res) { if (res.state) renderWatch(res.state); });
      return;
    }

    var one = e.target.closest("[data-check]");
    if (one) {
      one.disabled = true;
      one.textContent = "Checking";
      api("/api/watch/check", { id: one.dataset.check }).then(function (res) {
        if (!res.ok) toast(res.error || "That check failed.", "bad");
        else toast(res.new ? res.new + " new" : "Nothing new", res.new ? "good" : "");
        if (res.state) renderWatch(res.state);
      });
      return;
    }

    var hold = e.target.closest("[data-wpause]");
    if (hold) {
      api("/api/watch/pause", { id: hold.dataset.wpause, paused: hold.dataset.to === "1" })
        .then(function (res) { if (res.state) renderWatch(res.state); });
      return;
    }

    var drop = e.target.closest("[data-drop]");
    if (drop) {
      ask("Stop watching this? What it has already found is forgotten too.",
          { ok: "Stop watching", danger: true }).then(function (yes) {
        if (!yes) return;
        api("/api/watch/remove", { id: drop.dataset.drop }).then(function (res) {
          if (res.state) renderWatch(res.state);
        });
      });
    }
  });

  /* ------------------------------------------------------------- sharing */

  var shareTimer = null;

  function ago(seconds) {
    if (!seconds) return "never";
    var d = Math.max(0, Math.round(Date.now() / 1000 - seconds));
    if (d < 60) return "just now";
    if (d < 3600) return Math.round(d / 60) + " min ago";
    if (d < 86400) return Math.round(d / 3600) + " h ago";
    return Math.round(d / 86400) + " d ago";
  }

  var rulesOpen = "";        // device id whose rules panel is being edited
  var shareState = null;

  function gb(bytes) {
    if (!bytes) return "0";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    if (bytes < 1024 * 1024 * 1024) return Math.round(bytes / 1048576) + " MB";
    return (bytes / 1073741824).toFixed(1) + " GB";
  }

  function deviceRules(d, s) {
    var L = d.limits || {};
    var picked = L.sites || [];

    var qualities = (s.qualities || []).map(function (q) {
      return '<option value="' + esc(q.id) + '"' +
        (L.quality_cap === q.id ? " selected" : "") + ">" + esc(q.label) + "</option>";
    }).join("");

    var sites = (s.sites || []).map(function (name) {
      return '<button type="button" class="chip' +
        (picked.indexOf(name) >= 0 ? " is-on" : "") +
        '" data-site="' + esc(name) + '">' + esc(name) + "</button>";
    }).join("");

    return '<div class="dev-rules" data-rules-for="' + esc(d.id) + '">' +
      '<div class="rule-grid">' +
        '<label class="rule"><span>Name</span>' +
          '<input type="text" data-f="name" maxlength="24" value="' + esc(d.name) + '"></label>' +
        '<label class="rule"><span>Links per day</span>' +
          '<input type="number" min="0" data-f="per_day" value="' + (L.per_day || 0) + '"></label>' +
        '<label class="rule"><span>Largest file (MB)</span>' +
          '<input type="number" min="0" data-f="max_mb" value="' + (L.max_mb || 0) + '"></label>' +
        '<label class="rule"><span>Total allowance (GB)</span>' +
          '<input type="number" min="0" data-f="total_gb" value="' + (L.total_gb || 0) + '"></label>' +
        '<label class="rule"><span>Highest quality</span>' +
          '<select data-f="quality_cap"><option value="">No limit</option>' + qualities +
          "</select></label>" +
      "</div>" +
      '<p class="rule-note">0 means no limit.</p>' +
      '<div class="rule-sites"><span class="rule-label">Sites allowed</span>' +
        '<div class="chips">' + sites + "</div>" +
        '<p class="rule-note">Nothing picked means anywhere.</p></div>' +
      '<label class="rule-check"><input type="checkbox" data-f="own_folder"' +
        (L.own_folder ? " checked" : "") + '><span>Save into <b>From ' +
        esc(d.name) + "</b></span></label>" +
      '<label class="rule-check"><input type="checkbox" data-f="approve"' +
        (L.approve ? " checked" : "") + "><span>Ask before starting</span></label>" +
      '<div class="btn-row rule-foot">' +
        '<button class="primary small" data-save="' + esc(d.id) + '">Save rules</button>' +
        '<button class="ghost small" data-close="' + esc(d.id) + '">Close</button>' +
      "</div></div>";
  }

  function renderSharing(s) {
    shareState = s;
    $("setSharing").classList.toggle("on", s.on);

    // Says what is actually true rather than repeating the switch back. Just
    // "ready": the LAN line used to be appended here and read "ready ·
    // listening", which is the listener's status, not an address, and told
    // nobody anything.
    var where = !s.on ? "off"
      : s.lan_only ? "home network only"
      : s.relay === "connected" ? "ready"
      : s.relay;
    $("shareState").textContent = where;
    $("shareState").classList.toggle("on", s.on && (s.relay === "connected" || s.lan_only));

    $("shareInvite").disabled = !s.on;

    $("devicesEmpty").hidden = s.devices.length > 0;
    $("revokeAll").hidden = s.devices.length < 2;
    $("devicesList").innerHTML = s.devices.map(function (d) {
      var used = d.used || {};
      var facts = [d.count + " sent", "last " + ago(d.last)];
      if (used.today) facts.push(used.today + " today");
      if (used.bytes) facts.push(gb(used.bytes));

      return '<div class="dev' + (d.paused ? " is-paused" : "") + '">' +
        '<div class="dev-row"><div class="who"><b>' + esc(d.name) +
          (d.paused ? ' <em class="tag">paused</em>' : "") + "</b>" +
          "<span>" + esc(facts.join(" · ")) + "</span></div>" +
          '<button class="ghost small" data-pause="' + esc(d.id) + '" data-to="' +
            (d.paused ? "0" : "1") + '">' + (d.paused ? "Resume" : "Pause") + "</button>" +
          '<button class="ghost small" data-rules="' + esc(d.id) + '">Rules</button>' +
          '<button class="ghost small danger" data-revoke="' + esc(d.id) +
          '">Remove</button></div>' +
        (rulesOpen === d.id ? deviceRules(d, s) : "") +
        "</div>";
    }).join("");

    var log = s.log || [];
    $("incomingEmpty").hidden = log.length > 0;
    $("incomingList").innerHTML = log.map(function (e) {
      var waiting = e.state === "waiting";
      return '<div class="in-row' + (waiting ? " waiting" : "") + '">' +
        '<div class="what"><b>' + esc(e.from || "A device") + " · " +
        esc(e.quality || "default") + "</b><span>" + esc(e.url) + "</span></div>" +
        (waiting
          ? '<button class="primary small" data-ok="' + esc(e.id) + '">Approve</button>' +
            '<button class="ghost small" data-no="' + esc(e.id) + '">No</button>'
          : '<span class="in-state">' + esc(e.state) + " · " + ago(e.at) + "</span>") +
        "</div>";
    }).join("");

    var waitingCount = log.filter(function (e) { return e.state === "waiting"; }).length;
    $("shareBadge").textContent = waitingCount;
    $("shareBadge").hidden = waitingCount === 0;

    if (!s.crypto) {
      $("shareState").textContent = "unavailable in this build";
      $("shareInvite").disabled = true;
    }
  }

  function loadSharing() {
    api("/api/share/state", {}).then(function (res) {
      if (res.ok) renderSharing(res.state);
    });
    // A held link and a device that has just paired both have to appear on
    // their own - nobody is going to press refresh. The one exception is an
    // open rules panel: redrawing it under the user's hands would throw away
    // what they were typing.
    clearTimeout(shareTimer);
    shareTimer = setTimeout(function tick() {
      if (!$("view-sharing").classList.contains("is-active")) return;
      if (rulesOpen) { shareTimer = setTimeout(tick, 3000); return; }
      loadSharing();
    }, 3000);
  }

  $("setSharing").addEventListener("click", function () {
    var on = !$("setSharing").classList.contains("on");
    $("setSharing").classList.toggle("on", on);
    saveSetting({ sharing: on }).then(function () {
      if (!on) { $("qrBox").hidden = true; }
      loadSharing();
    });
  });

  $("revokeAll").addEventListener("click", function () {
    ask("Remove every paired device? Each one has to be paired again from a "
        + "new code.", { ok: "Remove all", danger: true }).then(function (yes) {
      if (!yes) return;
      rulesOpen = "";
      api("/api/share/revoke-all", {}).then(function (res) {
        toast(res.gone ? "Removed " + res.gone : "Nothing to remove", "good");
        if (res.state) renderSharing(res.state);
      });
    });
  });

  $("shareInvite").addEventListener("click", function () {
    api("/api/share/invite", { name: ($("shareName").value || "").trim() }).then(function (res) {
      if (!res.ok) { toast(res.error || "Could not make a code.", "bad"); return; }
      $("qrImage").innerHTML = res.invite.svg;
      $("qrLink").textContent = res.invite.url;
      $("qrCode").textContent = res.invite.code || "";
      $("qrBox").hidden = false;
      $("shareInvite").textContent = "New code";
      countdown(res.invite.expires);
      loadSharing();
    });
  });

  var qrTimer = null;
  function countdown(expires) {
    clearInterval(qrTimer);
    qrTimer = setInterval(function () {
      var left = Math.round(expires - Date.now() / 1000);
      if (left <= 0) {
        clearInterval(qrTimer);
        $("qrExpiry").textContent = "This code has expired. Make a new one.";
        $("qrImage").innerHTML = "";
        return;
      }
      $("qrExpiry").textContent = "Works once, for another " +
        Math.floor(left / 60) + ":" + ("0" + (left % 60)).slice(-2) + ".";
    }, 500);
  }

  $("qrCopy").addEventListener("click", function () {
    copyText($("qrCode").textContent || "");
  });

  $("qrCopyLink").addEventListener("click", function () {
    copyText($("qrLink").textContent || "");
  });

  $("shareClearLog").addEventListener("click", function () {
    api("/api/share/clear", {}).then(function (res) {
      if (res.ok) renderSharing(res.state);
    });
  });

  $("devicesList").addEventListener("click", function (e) {
    var gone = e.target.closest("[data-revoke]");
    if (gone) {
      // Removing is the one thing here that cannot be undone from this screen:
      // the phone has to be paired again from scratch. Pause is right beside
      // it and is what most people actually mean, so the box says so.
      ask("Remove this device? It has to be paired again from a new code.\n\n"
          + "Pause stops it sending without breaking the pairing.",
          { ok: "Remove", danger: true }).then(function (yes) {
        if (!yes) return;
        rulesOpen = "";
        api("/api/share/revoke", { id: gone.dataset.revoke }).then(function (res) {
          toast(res.ok ? "Device removed" : "Already gone", res.ok ? "good" : "");
          if (res.state) renderSharing(res.state);
        });
      });
      return;
    }

    var hold = e.target.closest("[data-pause]");
    if (hold) {
      api("/api/share/pause", {
        id: hold.dataset.pause, paused: hold.dataset.to === "1"
      }).then(function (res) {
        if (res.state) renderSharing(res.state);
      });
      return;
    }

    var open = e.target.closest("[data-rules]");
    if (open) {
      rulesOpen = rulesOpen === open.dataset.rules ? "" : open.dataset.rules;
      renderSharing(shareState);
      if (!rulesOpen) loadSharing();
      return;
    }

    var shut = e.target.closest("[data-close]");
    if (shut) {
      rulesOpen = "";
      loadSharing();
      return;
    }

    var site = e.target.closest("[data-site]");
    if (site) { site.classList.toggle("is-on"); return; }

    var save = e.target.closest("[data-save]");
    if (!save) return;

    var panel = save.closest(".dev-rules");
    var field = function (name) { return panel.querySelector('[data-f="' + name + '"]'); };
    var num = function (name) { return parseInt(field(name).value, 10) || 0; };

    var sites = Array.prototype.slice
      .call(panel.querySelectorAll(".chip.is-on"))
      .map(function (c) { return c.dataset.site; });

    var id = save.dataset.save;
    var name = field("name").value.trim();

    api("/api/share/limits", {
      id: id,
      limits: {
        per_day: num("per_day"),
        max_mb: num("max_mb"),
        total_gb: num("total_gb"),
        quality_cap: field("quality_cap").value,
        sites: sites,
        own_folder: field("own_folder").checked,
        approve: field("approve").checked
      }
    }).then(function (res) {
      if (!res.ok) { toast("Could not save those rules.", "bad"); return res; }
      return name ? api("/api/share/rename", { id: id, name: name }) : res;
    }).then(function () {
      rulesOpen = "";
      toast("Saved", "good");
      loadSharing();
    });
  });

  $("incomingList").addEventListener("click", function (e) {
    var yes = e.target.closest("[data-ok]");
    var no = e.target.closest("[data-no]");
    if (!yes && !no) return;
    api("/api/share/approve", {
      id: yes ? yes.dataset.ok : no.dataset.no,
      ok: !!yes
    }).then(function (res) {
      if (res.state) renderSharing(res.state);
      if (yes) { toast("Queued", "good"); pollJobs(); }
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
    var value = e.target.value;
    saveSetting({ cookies_browser: value }).then(function () {
      toast(value === "none" ? "Cookies off" : "Using " + value + " cookies", "good");
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
  bindToggle("setPolite", "polite_mode");
  bindToggle("setUpscale", "allow_ai_upscale");
  bindToggle("setSponsor", "sponsorblock");
  bindToggle("setSubs", "write_subs");
  bindToggle("setEmbedSubs", "embed_subs");
  bindToggle("setChapters", "embed_chapters");
  bindToggle("setSkipExisting", "skip_existing");
  bindToggle("setShareApprove", "share_approve");

  $("setRelay").addEventListener("change", function (e) {
    var value = (e.target.value || "").trim();
    if (value && !/^wss?:\/\//i.test(value)) {
      toast("A relay address starts with wss://", "bad");
      e.target.value = settings.share_relay || "";
      return;
    }
    saveSetting({ share_relay: value }).then(function (res) {
      e.target.value = (res.settings || settings).share_relay || "";
      toast("Saved");
    });
  });

  // The two subtitle details are meaningless with subtitles off.
  function syncSubFields() {
    var on = $("setSubs").classList.contains("on");
    $("subLangField").hidden = !on;
    $("embedSubField").hidden = !on;
  }
  $("setSubs").addEventListener("click", syncSubFields);
  syncSubFields();

  $("setSubLangs").addEventListener("change", function (e) {
    var value = (e.target.value || "").trim() || "en";
    e.target.value = value;
    saveSetting({ sub_langs: value }).then(function () { toast("Saved"); });
  });

  bindToggle("setCheckUpdates", "check_updates");

  $("setSpeedLimit").addEventListener("change", function (e) {
    var kb = parseInt(e.target.value, 10) || 0;
    saveSetting({ speed_limit: kb }).then(function () {
      toast(kb ? "Limited to " + e.target.selectedOptions[0].text : "No limit", "good");
    });
  });

  document.querySelectorAll("[data-export]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      api("/api/export-links", { format: btn.dataset.export }).then(function (r) {
        if (r.cancelled) return;
        if (!r.ok) { toast(r.error || "Could not export.", "bad"); return; }
        toast(r.count + " links saved", "good");
      });
    });
  });

  $("setFragments").addEventListener("change", function (e) {
    saveSetting({ fragments: parseInt(e.target.value, 10) })
      .then(function () { toast("Saved"); });
  });

  /* ------------------------------------------------------- browser sign-in */

  var cookieTimer = null;

  function renderCookies(c) {
    var state = $("cookieState");
    if (!state) return;

    if ($("signinBrowser") && c.browser) $("signinBrowser").textContent = c.browser;

    if (c.busy) {
      state.textContent = c.step === "reading"
        ? "Reading the session…"
        : "Waiting — sign in, then close the browser window.";
    } else if (c.step === "failed" && c.error) {
      state.textContent = "Last attempt failed: " + c.error;
    } else if (c.haveCookies) {
      state.textContent = "Signed in · " + c.count + " cookies" +
        (c.sites && c.sites.length ? " · " + c.sites.join(", ") : "");
    } else if (!c.browser) {
      state.textContent = "No Chrome, Edge or Brave found on this PC.";
    } else {
      state.textContent = "Not signed in";
    }

    $("signIn").disabled = !!c.busy || !c.browser;
    $("signIn").textContent = c.haveCookies ? "Sign in again" : "Sign in";
    $("refreshCookies").hidden = !c.haveCookies;
    $("forgetCookies").hidden = !c.haveCookies;

    // Only poll while something is actually happening.
    if (c.busy && !cookieTimer) {
      cookieTimer = setInterval(loadCookies, 1200);
    } else if (!c.busy && cookieTimer) {
      clearInterval(cookieTimer);
      cookieTimer = null;
      if (c.step === "done") toast("Signed in", "good");
      if (c.step === "failed") toast(c.error || "Sign-in failed", "bad");
    }
  }

  function loadCookies() {
    api("/api/cookies/status").then(function (res) {
      if (res.ok) renderCookies(res.cookies);
    });
  }

  $("signIn").addEventListener("click", function () {
    api("/api/cookies/signin", {}).then(function (res) {
      if (!res.ok) { toast(res.error || "Could not open the browser.", "bad"); return; }
      toast("Sign in, then close that window", "good");
      loadCookies();
    });
  });

  $("refreshCookies").addEventListener("click", function () {
    api("/api/cookies/refresh", {}).then(function (res) {
      if (!res.ok) { toast(res.error || "Could not refresh.", "bad"); return; }
      loadCookies();
    });
  });

  $("forgetCookies").addEventListener("click", function () {
    api("/api/cookies/forget", {}).then(function (res) {
      if (res.ok) renderCookies(res.cookies);
      toast("Sign-in deleted");
    });
  });

  /* -------------------------------------------------------- YouTube helper */

  var potTimer = null;

  function renderPot(p) {
    var state = $("potState");
    if (!state) return;

    if (p.busy) {
      state.textContent = (p.message || "Downloading") + " · " + p.percent + "%";
    } else if (p.error) {
      state.textContent = "Failed: " + p.error;
    } else if (p.installed) {
      state.textContent = "Installed " + p.release + (p.running ? " · running" : "");
    } else {
      state.textContent = "Not installed";
    }

    if ($("potSize")) $("potSize").textContent = p.sizeMb + " MB";
    $("removePotoken").hidden = !p.installed;

    if (p.busy && !potTimer) {
      potTimer = setInterval(loadPot, 900);
    } else if (!p.busy && potTimer) {
      clearInterval(potTimer);
      potTimer = null;
      if (p.installed) toast("YouTube helper ready", "good");
      if (p.error) toast(p.error, "bad");
    }
  }

  function loadPot() {
    api("/api/potoken/status").then(function (res) {
      if (res.ok) renderPot(res.potoken);
    });
  }

  $("setPotoken").addEventListener("click", function () {
    var btn = $("setPotoken");
    var on = !btn.classList.contains("on");
    btn.classList.toggle("on", on);
    saveSetting({ potoken: on }).then(function () {
      if (!on) { toast("YouTube helper off"); return; }
      api("/api/potoken/install", {}).then(function (res) {
        if (!res.ok) { toast(res.error || "Could not start the download.", "bad"); return; }
        if (res.already) { toast("Already installed", "good"); }
        loadPot();
      });
    });
  });

  $("removePotoken").addEventListener("click", function () {
    api("/api/potoken/remove", {}).then(function () {
      $("setPotoken").classList.remove("on");
      loadPot();
      toast("Helper removed");
    });
  });

  $("setChannel").addEventListener("change", function (e) {
    saveSetting({ engine_channel: e.target.value }).then(function () {
      toast("Press Update to switch to " + e.target.value, "good");
    });
  });

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
      var env = res.environment || {};
      $("engineVersion").textContent = res.engineVersion && res.engineVersion !== "missing"
        ? "Version " + res.engineVersion + " (" + (env.channel || "stable") + ")"
        : "Not installed";

      // The three things that decide whether YouTube behaves, in one line.
      var line = $("envLine");
      if (line) {
        line.textContent = " Video tools: " + (env.ffmpeg ? "yes" : "missing") +
          " · JavaScript helper: " + (env.js ? "yes" : "missing") +
          " · YouTube helper: " + (env.potoken ? "installed" : "off") + ".";
      }
    });
  }

  $("updateEngine").addEventListener("click", function () {
    var btn = $("updateEngine");
    btn.disabled = true;
    btn.textContent = "Checking…";
    api("/api/update-engine", { channel: $("setChannel").value }).then(function (res) {
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
    if (e.key === "Escape") {
      $("clipHint").hidden = true;
      dismissedClip = lastClip;
      // The warning is the one dialog Escape must not dismiss - closing it
      // this way would be exactly the click-past it exists to prevent.
    }
    if ((e.ctrlKey || e.metaKey) && e.key === "l") { e.preventDefault(); show("capture"); $("urlInput").focus(); $("urlInput").select(); }
  });

  $("brandLink").addEventListener("click", function (e) {
    // Inside a webview an ordinary link would replace the whole app with a
    // web page and leave no way back.
    e.preventDefault();
    api("/api/open-url", { url: "https://xniperbuilds.com" });
  });

  $("urlInput").focus();
  pollJobs();

  // The Watch badge has to be right on every screen, not only its own. A check
  // happens at most once every ninety seconds, so once a minute is plenty.
  function watchBadge() {
    if ($("view-watch").classList.contains("is-active")) return;
    api("/api/watch/state", {}).then(function (res) {
      if (!res.ok) return;
      $("watchBadge").textContent = res.state.new;
      $("watchBadge").hidden = !res.state.new;
    });
  }
  watchBadge();
  setInterval(watchBadge, 60000);
  // Always polling: even with clipboard watching off, this is how the window
  // hears about downloads the global shortcut started.
  startClipboardWatch();
})();
