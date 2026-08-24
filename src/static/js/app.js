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

  var views = ["capture", "queue", "library", "failed", "convert", "watch",
               "sharing", "settings"];

  function show(view) {
    views.forEach(function (v) {
      $("view-" + v).classList.toggle("is-active", v === view);
    });
    document.querySelectorAll(".tab").forEach(function (b) {
      b.classList.toggle("is-active", b.dataset.view === view);
    });

    if (view === "queue") pollJobs();
    if (view === "library") loadHistory();
    if (view === "failed") loadFailed();
    if (view === "convert") loadConvert();
    if (view === "watch") loadWatch();
    if (view === "sharing") loadSharing();
    if (view === "settings") {
      loadEngineVersion(); loadCookies(); loadPot(); checkEngineUpdate(false);
    }
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

  $("grabBtn").addEventListener("click", function () {
    grabPage($("urlInput").value.trim());
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

  /* Read a whole page and list what is on it, for the pages an extractor does
     not know: a blog post with three embedded videos, a links page, a forum
     thread. It comes back shaped like a playlist on purpose, so the picking,
     sorting and first-N screen below needs no second version of itself. */
  function grabPage(url) {
    if (!url) { toast("Paste a page link first.", "bad"); return; }

    $("analyzeError").hidden = true;
    $("preview").hidden = true;
    $("playlistWrap").hidden = true;
    setBusy(true);

    api("/api/grab", { url: url }).then(function (res) {
      setBusy(false);
      if (!res.ok) {
        var box = $("analyzeError");
        box.textContent = res.error || "Could not read that page.";
        box.hidden = false;
        return;
      }
      current = res.info;
      renderPreview(res.info);
      toast(res.info.count + " found on that page", "good");
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

    // A page that was read for its links is not a playlist, and saying so
    // would be the app claiming to know more about it than it does.
    $("pvKind").textContent = info.grabbed ? "ON THIS PAGE"
      : (isList ? "PLAYLIST" : "VIDEO");
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
    // Shown before anything is pressed. "Highest" on an 8K video is 3.4 GB,
    // and finding that out from the progress bar is finding out too late.
    var sizes = (info && info.sizes) || {};

    $("qualityChips").innerHTML = options.map(function (q) {
      var from = upscaled[q];
      return '<button type="button" class="chip' +
        (q === "mp3" ? " audio" : "") +
        (from ? " upscaled" : "") +
        (q === quality ? " is-on" : "") +
        '" data-q="' + q + '"' +
        (from ? ' title="YouTube made this with AI from a ' + from + 'p original"' : "") +
        ">" + esc(labels[q] || q) +
        (sizes[q] ? '<em class="chip-size"> · ' + esc(sizes[q]) + "</em>" : "") +
        (from ? '<em> · AI-upscaled from ' + from + "p</em>" : "") +
        "</button>";
    }).join("");

    $("preview").hidden = false;

    // Trimming needs ffmpeg, and only makes sense for one video at a time.
    $("trimBlock").hidden = isList || !S.hasFfmpeg;
    resetTrim();
    $("channelWrap").hidden = true;

    // Closed and cleared for every new link, on purpose: it is not a mode,
    // and nothing chosen FOR THE LAST VIDEO should carry into this one - a
    // format id or a file name means something different here.
    //
    // A preference is not the same thing. Picking the same audio language or
    // player client before every single download is what people are asking to
    // stop doing, so those - and only those - come back afterwards.
    $("moreBox").open = false;
    resetMore();
    // Cleared for every new link: wanting the audio of one video says nothing
    // about wanting the audio of the next.
    $("alsoAudio").checked = false;
    syncAlsoAudio();
    fillFormats(info);
    restoreOpts();
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

  /* Offering to also make an MP3 of an MP3 is nonsense, and without the media
     tool there is no MP3 to make at all - so the choice is hidden rather than
     shown as something that would quietly do nothing. */
  function syncAlsoAudio() {
    var wrap = $("alsoAudioWrap");
    if (!wrap) return;
    var possible = quality !== "mp3" && S.hasFfmpeg;
    wrap.hidden = !possible;
    if (!possible) $("alsoAudio").checked = false;
  }

  $("qualityChips").addEventListener("click", function (e) {
    var chip = e.target.closest(".chip");
    if (!chip) return;
    quality = chip.dataset.q;
    document.querySelectorAll("#qualityChips .chip").forEach(function (c) {
      c.classList.toggle("is-on", c === chip);
    });

    /* The re-upload rung says what it is before it is used. It is not "better"
     * for watching - it is a bigger file chosen to survive being uploaded
     * again, and it is honest about usually being the same file, because a
     * warning that overstates its case is one people learn to click past. */
    var note = $("qualityNote");
    if (note) {
      note.hidden = quality !== "max";
      if (quality === "max") {
        note.textContent = "Biggest file, chosen to survive being uploaded "
          + "again rather than to play everywhere - it may need a codec your "
          + "player does not have. Most of the time it is the same file as "
          + "Best available, and it ignores “Skip files I already have” "
          + "so a copy you saved at a lower quality is not mistaken for this one.";
      }
    }

    syncAlsoAudio();
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
  /* The dubs the video that is open actually has. Kept because "all of them"
     has to send the list, and the dropdown's own options are the only other
     place it exists. */
  var audioLangs = [];
  var thumbChoices = [];
  var pickedThumb = "";

  function markThumb() {
    document.querySelectorAll("#thumbPick .thumb-opt").forEach(function (b) {
      var t = thumbChoices[+b.dataset.thumb];
      b.classList.toggle("is-on", !!t && t.url === pickedThumb);
    });
    var chosen = thumbChoices.filter(function (t) { return t.url === pickedThumb; })[0];
    $("thumbNote").textContent = chosen
      ? "Kept beside the video, and put inside it where the file format allows."
      : "";
  }

  $("thumbPick").addEventListener("click", function (e) {
    var btn = e.target.closest("[data-thumb]");
    if (!btn) return;
    var t = thumbChoices[+btn.dataset.thumb];
    // Clicking the one already chosen turns it off, which is the only way
    // back to the default without hunting for the button that says so.
    pickedThumb = (t && t.url === pickedThumb) ? "" : (t ? t.url : "");
    markThumb();
    refreshCommand();
  });

  $("thumbClear").addEventListener("click", function () {
    pickedThumb = "";
    markThumb();
    refreshCommand();
  });

  function resetMore() {
    pickedFormat = "";
    onceDir = "";
    pickedThumb = "";
    markThumb();
    $("optName").value = "";
    $("optDir").dataset.dir = "";
    $("optDir").textContent = "Default folder";
    $("optCookies").value = "";
    $("optClient").value = "";
    $("optAudioLang").value = "";
    $("optSubLang").value = "";
    $("optSubsOnly").checked = false;
    $("optThumbAll").checked = false;
    $("optLiveFromStart").checked = false;
    // Only a stream that is live right now can be joined from its beginning.
    // Hidden otherwise rather than shown and ignored.
    $("optLiveWrap").hidden = !(current && current.is_live);
    syncSubsOnly();
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
    /* The star is not a language, so it never goes out as one - it becomes the
       list of every language, which the server turns into one job apiece. */
    if ($("optAudioLang").value === "*") o.audio_langs = audioLangs.slice();
    else if ($("optAudioLang").value) o.audio_lang = $("optAudioLang").value;
    if ($("optSubLang").value) o.sub_langs = $("optSubLang").value;
    if ($("optName").value.trim()) o.outtmpl = $("optName").value.trim();
    if (onceDir) o.dest_dir = onceDir;
    if ($("optClient").value) o.player_client = $("optClient").value;
    if ($("optCookies").value === "off") o.no_cookies = true;
    if ($("optSubsOnly").checked) o.subs_only = true;
    if ($("optThumbAll").checked) o.thumb_all = true;
    if (pickedThumb) o.thumb_url = pickedThumb;
    if ($("optLiveFromStart").checked) o.live_from_start = true;
    return o;
  }

  /* Asking for subtitles only and then picking a quality is a contradiction -
     one of the two has to be ignored, and saying which beats silently
     dropping the other. */
  function syncSubsOnly() {
    var only = $("optSubsOnly").checked;
    var note = $("subsOnlyNote");
    if (note) {
      note.hidden = !only;
      note.textContent = "No video will be downloaded, so the quality above "
                       + "does not apply. The subtitle language is the one in "
                       + "Settings unless you set it here.";
    }
    var thumb = $("optThumbAll");
    if (thumb) thumb.closest(".more-check").classList.toggle("is-inert", only);
    if (only && thumb) thumb.checked = false;
  }

  $("optSubsOnly").addEventListener("change", function () {
    syncSubsOnly();
    refreshCommand();
  });
  ["optThumbAll", "optLiveFromStart"].forEach(function (id) {
    $(id).addEventListener("change", refreshCommand);
  });

  /* Setting the same three things before every download is the complaint
     behind "remember the last selected download options". Only the choices
     that mean the same thing on the next link are kept:

       audio language, subtitle language, player client, cookies off

     A format id is deliberately NOT remembered - id 137 is a different thing
     on a different video, and silently reusing it would pick something nobody
     asked for. Nor is the file name, which is per-file by definition. */

  var REMEMBERED = ["audio_lang", "sub_langs", "player_client", "no_cookies"];

  function rememberOpts(opts) {
    var keep = {};
    REMEMBERED.forEach(function (key) {
      if (opts && opts[key]) keep[key] = opts[key];
    });
    // Written even when empty: clearing the boxes has to stick too, or the
    // old values come back on the next link and look like a bug.
    saveSetting({ last_opts: keep });
  }

  function restoreOpts() {
    var last = settings.last_opts || {};
    if (!Object.keys(last).length) return;

    if (last.audio_lang) $("optAudioLang").value = last.audio_lang;
    if (last.sub_langs) $("optSubLang").value = last.sub_langs;
    if (last.player_client) $("optClient").value = last.player_client;
    $("optCookies").value = last.no_cookies ? "off" : "on";

    // Opened, because a remembered choice that is not visible is a setting
    // acting on the download while hidden - which is the thing this app is
    // trying not to do anywhere.
    $("moreBox").open = true;
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

    /* Cover pictures. Offered only when there is a real choice: one picture is
       not a choice, and none means the site published nothing to choose. */
    var thumbs = info.thumbs || [];
    pickedThumb = "";
    $("thumbSec").hidden = thumbs.length < 2;
    $("thumbNote").textContent = "";
    $("thumbPick").innerHTML = thumbs.map(function (t, i) {
      return '<button type="button" class="thumb-opt" data-thumb="' + i + '">' +
        '<img loading="lazy" src="' + esc(t.url) + '" alt="">' +
        "<span>" + esc(t.label) + "</span></button>";
    }).join("");
    thumbChoices = thumbs;

    /* "All of them" is offered only when there is more than one, because on a
       video with a single audio track it would queue one job and call it a
       set. It carries a star rather than a language code so nothing downstream
       can mistake it for one. */
    var langs = info.audio_langs || [];
    audioLangs = langs;
    $("optAudioLang").innerHTML = '<option value="">Default</option>' +
      (langs.length > 1
        ? '<option value="*">All ' + langs.length + " — one file each</option>"
        : "") +
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
    /* Every-language belongs to the request, not to one job's options: the
       server turns it into a job per language, each with its own audio_lang.
       Moved out here rather than left in opts, where it would be dropped as
       an option nobody recognises. */
    if (body.opts.audio_langs) {
      body.audio_langs = body.opts.audio_langs;
      delete body.opts.audio_langs;
    }
    // The extras ride along with the main choice rather than replacing it,
    // so the video is still what the row says it is.
    if (quality !== "mp3" && $("alsoAudio").checked) body.also = ["mp3"];
    if (current.kind !== "playlist" && $("trimOn").checked) {
      var range = readTrim();
      if (range === null) return;             // readTrim already complained
      body.start = range.start;
      body.end = range.end;
      body.exact = range.exact;
    }

    api("/api/add", body).then(function (res) {
      if (!res.ok) { toast(res.error || "Could not queue that.", "bad"); return; }
      rememberOpts(body.opts);
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

  // What the engine says is holding the queue back, if anything.
  var holdNote = "";

  /* Searching the queue. Rows are hidden rather than removed: they are still
     downloading, and their progress has to keep arriving whether or not the
     current search happens to match them. */

  var QUEUE_FIND_FROM = 6;          // below this, a search box is just clutter

  function applyQueueFilter(jobs) {
    var wrap = $("queueFind");
    if (!wrap) return;

    var term = $("queueSearch").value.trim().toLowerCase();
    wrap.hidden = jobs.length < QUEUE_FIND_FROM && !term;
    if (wrap.hidden) { $("queueSearch").value = ""; term = ""; }

    var hits = 0;
    jobs.forEach(function (j) {
      var row = rows[j.id];
      if (!row) return;
      // The address counts as well as the title: a row that has not been read
      // yet is still only a URL, and that is exactly when it is hard to find.
      var hay = ((j.title || "") + " " + (j.url || "") + " " +
                 (j.uploader || "") + " " + (j.status || "")).toLowerCase();
      var show = !term || hay.indexOf(term) >= 0;
      row.el.hidden = !show;
      if (show) hits++;
    });

    $("queueFound").textContent = term
      ? (hits ? hits + " of " + jobs.length : "Nothing matches — " +
         jobs.length + " still here")
      : "";
  }

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

    // A queue that sits there with nothing starting looks broken. When the
    // schedule is what is holding it, say so where the waiting is happening.
    var hold = $("holdBar");
    if (hold) {
      hold.hidden = !(holdNote && active > 0);
      if (!hold.hidden) hold.textContent = holdNote;
    }

    applyQueueFilter(jobs);

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
      // "45.2 MB of 342.0 MB" answers "how much longer on this line" in a way
      // a percentage never does - and on an 8K file that is the whole question.
      if (j.got && j.size) bits.push(["", j.got + " of " + j.size]);
      else if (j.size) bits.push(["", j.size]);
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
      // A countdown rather than a fixed sentence: the message was written
      // when the job failed, and "in 5 minutes" is wrong four minutes later.
      var soon = j.retryIn
        ? "\n\nTrying again on its own in " +
          (j.retryIn >= 60 ? Math.round(j.retryIn / 60) + " min"
                           : j.retryIn + "s") + "."
        : "";
      row.error.textContent = j.error.split("\n\nRiplox will try this one")[0] + soon;
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

  $("queueSearch").addEventListener("input", function () {
    applyQueueFilter(window._jobs || []);
  });

  /* Whole-queue buttons. Each carries its own count and hides when that count
     is zero, so the row says what pressing it would actually do. */
  var BULK = {
    pauseAll: { path: "/api/pause-all", label: "Pause all", key: "paused",
                done: "Paused ", none: "Nothing to pause" },
    resumeAllTop: { path: "/api/resume-all", label: "Resume all", key: "resumed",
                    done: "Resumed ", none: "Nothing to resume" },
    retryAll: { path: "/api/retry-all", label: "Retry all", key: "retried",
                done: "Queued ", none: "Nothing to retry" }
  };

  Object.keys(BULK).forEach(function (id) {
    var spec = BULK[id];
    $(id).addEventListener("click", function () {
      var btn = $(id);
      btn.disabled = true;                 // a second press mid-flight is a mess
      api(spec.path, {}).then(function (res) {
        var n = (res && res[spec.key]) || 0;
        toast(n ? spec.done + n : spec.none, n ? "good" : "");
        pollJobs();
      }).catch(function () {
        toast("That did not go through.", "bad");
      }).then(function () { btn.disabled = false; });
    });
  });

  var RUNNING = ["queued", "starting", "downloading", "converting"];

  function renderBulkButtons(jobs) {
    var counts = { pauseAll: 0, resumeAllTop: 0, retryAll: 0 };
    (jobs || []).forEach(function (job) {
      if (RUNNING.indexOf(job.status) >= 0) counts.pauseAll++;
      else if (job.status === "paused") counts.resumeAllTop++;
      else if (job.status === "error" || job.status === "cancelled") counts.retryAll++;
    });
    Object.keys(BULK).forEach(function (id) {
      var btn = $(id);
      if (!btn) return;
      btn.hidden = !counts[id];
      btn.textContent = BULK[id].label + (counts[id] ? " (" + counts[id] + ")" : "");
    });
  }

  /* A site being left alone after it asked Riplox to slow down.

     Written out rather than hidden: this is the one time a queue that is not
     moving is doing the right thing, and the only way anyone can tell that
     apart from a broken app is being told. The way out sits next to it. */
  function renderCooling(cooling) {
    var bar = $("coolBar");
    if (!bar) return;
    bar.hidden = cooling.length === 0;
    if (bar.hidden) { bar.innerHTML = ""; return; }

    bar.innerHTML = cooling.map(function (c) {
      var mins = Math.max(1, Math.round(c.left / 60));
      // Which account, when it was an account that was refused: with a spare
      // signed in, "Instagram is waiting" would be wrong - the other one is
      // still working.
      var who = c.account
        ? esc(c.site) + " (account " + c.account + ")"
        : esc(c.site);
      return '<div class="cool-row"><span>' + who +
        " asked Riplox to slow down. Waiting about " + mins +
        (mins === 1 ? " minute" : " minutes") +
        " — everything else carries on.</span>" +
        '<button class="ghost small" data-gonow="' + esc(c.site) +
        '" data-n="' + (c.account || 0) + '">Go now anyway</button></div>';
    }).join("");
  }

  $("coolBar").addEventListener("click", function (e) {
    var go = e.target.closest("[data-gonow]");
    if (!go) return;
    api("/api/pace/resume", { site: go.dataset.gonow,
                              account: parseInt(go.dataset.n, 10) || 0 })
      .then(function () { pollJobs(); });
  });

  function pollJobs() {
    return api("/api/jobs").then(function (res) {
      if (!res.ok) return 0;
      window._jobs = res.jobs;
      S.hasPotoken = res.hasPotoken;
      S.hasFfmpeg = res.hasFfmpeg;
      holdNote = res.holdNote || "";
      // Comes back with the queue rather than from a poll of its own: the
      // number is the only part of that page anything else needs.
      setFailedBadge(res.failedCount || 0);
      renderCooling(res.cooling || []);
      var active = renderJobs(res.jobs);
      renderBulkButtons(res.jobs);

      if (!analyzing) {
        // A stopped queue with no explanation reads as a broken app. This is
        // the commonest reason for it and the one the user can act on.
        var offline = res.network === false;
        $("engineStatus").className = "status"
          + (offline ? " warn" : (active ? " busy" : ""));
        $("engineLabel").textContent = offline
          ? "waiting for the network"
          : (active ? active + " active" : "ready");
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
      if (!term) return true;
      // The uploader counts as well as the title. Without it, searching for
      // a name found nothing even though every file was by that person -
      // and the Accounts chips, which put a name in this box, would have
      // been a button that quietly did nothing.
      return ((h.title || "") + " " + (h.uploader || ""))
        .toLowerCase().indexOf(term) !== -1;
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

  /* ---------------------------------------------------------------- failed */
  /* The list nothing tidies up. Every button here that removes a row is one
     the user pressed: there is no age limit, no cap, and no quiet clean-up
     when something eventually works - a row that downloaded later says so and
     stays until it is deleted. */

  var failedItems = [];
  var failedOpen = "";       // whose details are showing

  function setFailedBadge(count) {
    var badge = $("failedBadge");
    if (!badge) return;
    badge.textContent = count;
    badge.hidden = !count;
  }

  function loadFailed() {
    return api("/api/failed").then(function (res) {
      failedItems = res.failed || [];
      renderFailed();
      setFailedBadge(failedItems.filter(function (f) { return !f.fixed; }).length);
    });
  }

  // Local time, not UTC: everything else on this screen is what the clock on
  // the wall said, and a row five minutes old reading as yesterday evening is
  // worse than no time at all.
  function whenText(seconds) {
    if (!seconds) return "";
    var d = new Date(seconds * 1000);
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function renderFailed() {
    $("failedEmpty").hidden = failedItems.length > 0;
    $("failedRetryAll").hidden = failedItems.length === 0;
    $("failedClear").hidden = failedItems.length === 0;

    $("failedList").innerHTML = failedItems.map(function (f) {
      var facts = [labels[f.quality] || f.quality || "", f.site || "",
                   whenText(f.last || f.when)];
      if (f.tries > 1) facts.push(f.tries + " tries");
      var open = failedOpen === f.id;

      return '<div class="hrow fail-row' + (f.fixed ? " is-fixed" : "") + '">' +
        (f.thumbnail
          ? '<img src="' + esc(f.thumbnail) + '" alt="" onerror="this.style.visibility=\'hidden\'">'
          : "<div></div>") +
        '<div><div class="t">' + esc(f.title || f.url) +
          (f.fixed ? ' <em class="tag">downloaded later</em>' : "") + "</div>" +
        '<div class="m">' + esc(facts.filter(Boolean).join(" · ")) + "</div>" +
        '<div class="m fail-why">' + esc(f.error || "No reason was recorded.") + "</div>" +
        (open ? '<pre class="fail-log">' + esc(f.log || "Nothing was logged.") + "</pre>" : "") +
        "</div>" +
        '<button class="icon-btn" data-details="' + esc(f.id) + '" title="' +
          (open ? "Hide details" : "Show details") + '">' + ICON.copy + "</button>" +
        '<button class="icon-btn go" data-refail="' + esc(f.id) +
          '" title="Try again">' + ICON.retry + "</button>" +
        '<button class="icon-btn" data-forget="' + esc(f.id) +
          '" title="Delete this row">' + ICON.trash + "</button>" +
        "</div>";
    }).join("");
  }

  $("failedList").addEventListener("click", function (e) {
    var details = e.target.closest("[data-details]");
    if (details) {
      failedOpen = failedOpen === details.dataset.details ? "" : details.dataset.details;
      renderFailed();
      return;
    }

    var retry = e.target.closest("[data-refail]");
    if (retry) {
      api("/api/failed/retry", { id: retry.dataset.refail }).then(function (r) {
        if (!r.ok) { toast(r.error || "Could not queue that again.", "bad"); return; }
        toast("Back in the queue", "good");
        show("queue");
        pollJobs();
      });
      return;
    }

    var forget = e.target.closest("[data-forget]");
    if (forget) {
      api("/api/failed/forget", { id: forget.dataset.forget }).then(function () {
        loadFailed();
      });
    }
  });

  $("failedRetryAll").addEventListener("click", function () {
    var waiting = failedItems.filter(function (f) { return !f.fixed; });
    if (!waiting.length) { toast("Nothing waiting to retry.", "bad"); return; }

    // One after another rather than all at once: the queue decides how many
    // run together, and a hundred requests fired in a breath is a way to be
    // rate-limited by every site at the same time.
    var index = 0;
    (function next() {
      if (index >= waiting.length) {
        toast(waiting.length + " back in the queue", "good");
        show("queue");
        pollJobs();
        return;
      }
      api("/api/failed/retry", { id: waiting[index++].id }).then(next);
    })();
  });

  $("failedClear").addEventListener("click", function () {
    ask("Delete every row on this page? The files are not touched - only "
        + "this list.", { ok: "Delete all", danger: true }).then(function (yes) {
      if (!yes) return;
      api("/api/failed/clear", {}).then(function () {
        failedOpen = "";
        loadFailed();
        toast("Failed list emptied", "good");
      });
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

  // The engine is checked whatever that setting says: an engine two months old
  // is why a site stops working, and this only ever writes a line in Settings.
  setTimeout(function () { checkEngineUpdate(false); }, 5000);

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

  /* ------------------------------------------------------------- browser
   *
   * The extension has been copied onto every PC that installed Riplox since
   * v1.3.0, and until now nothing in the app mentioned it. This shows where it
   * is and opens the folder, so "Load unpacked" is a paste rather than a hunt.
   */
  api("/api/extension").then(function (res) {
    if (!res || !res.ok) return;
    var where = $("extPath");
    if (where) {
      where.textContent = res.there
        ? res.path
        : "Not found on this PC - reinstall Riplox to get it back.";
    }
    showPortable(res);
  });

  /*
   * Portable copies.
   *
   * Two things go unsaid without this. A copy that asked to keep everything
   * in its own folder and could not - a read-only stick - carries on working
   * while writing to this PC instead, and nobody would ever know. And the
   * browser extension, which needs the installer to introduce it to Chrome,
   * simply does nothing on a copy that was never installed.
   */
  function showPortable(res) {
    var row = $("portableRow");
    var note = $("portableWhere");
    if (row && note && (res.portable === "on" || res.portable === "read-only")) {
      row.hidden = false;
      note.className = res.portable === "read-only" ? "warn" : "";
      note.textContent = res.portable === "read-only"
        ? "This copy asked to keep everything in its own folder, but that "
          + "folder cannot be written to. Your settings and history are on "
          + "this PC instead, in the usual place."
        : "In its own folder, beside Riplox. Nothing is written anywhere "
          + "else on this PC.";
    }

    var connectRow = $("extConnectRow");
    if (!connectRow || !res.canConnect) return;
    connectRow.hidden = false;
    paintConnect(res.connected);
  }

  function paintConnect(connected) {
    var btn = $("connectBrowser");
    var state = $("extConnectState");
    if (btn) {
      btn.textContent = connected ? "Remove" : "Connect";
      // The button's own label used to decide the next action, which breaks
      // the moment the wording changes. This does not.
      btn.dataset.on = connected ? "1" : "";
    }
    if (state) {
      state.textContent = connected
        ? "Connected - your browser can reach this copy."
        : "Not connected - the extension's button will do nothing yet.";
    }
  }

  var connectBtn = $("connectBrowser");
  if (connectBtn) {
    connectBtn.addEventListener("click", function () {
      api("/api/extension/connect", { on: connectBtn.dataset.on !== "1" })
        .then(function (res) {
          if (res && res.ok) {
            paintConnect(res.connected);
            toast(res.message || "Done", "good");
          } else {
            toast((res && (res.message || res.error)) || "Could not do that",
                  "bad");
          }
        });
    });
  }

  var extBtn = $("openExtFolder");
  if (extBtn) {
    extBtn.addEventListener("click", function () {
      api("/api/extension/open", {}).then(function (res) {
        if (res && res.ok) toast("Opened the extension folder", "good");
        else toast((res && res.error) || "Could not open it", "bad");
      });
    });
  }

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
      .then(function (res) { if (!res.ok) return; toast("Saved"); loadWatch(); });
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

  // Why a link is being held. The rule's own name means nothing to whoever is
  // looking at the screen; what they need is the sentence that tells them
  // whether pressing Approve is the right thing to do.
  var WHY = {
    "day-limit": "past this device's daily limit",
    "total-limit": "past this device's total allowance",
    "paused": "this device is paused",
    "site": "this site is not on this device's list"
  };

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
      // A link a rule stopped is waiting too, and it gets the same buttons -
      // but "waiting" on its own would look like Riplox simply had not got to
      // it. The reason is what turns the Approve button into a choice.
      var why = waiting && e.why ? " · " + esc(WHY[e.why] || e.why) : "";

      /* Which way it came in.
       *
       * "Nothing left the building" is something Riplox says about itself, and
       * until this there was no way to see it: a link that stayed on the home
       * network and one that went out to a relay and back arrived looking
       * exactly the same. Shown rather than claimed.
       *
       * Only the local one is called out. Saying "relay" on every other row
       * would be noise about the ordinary case - and older entries, logged
       * before this existed, have no via at all and must not be labelled
       * wrongly for it. */
      var road = e.via === "lan"
        ? '<span class="in-road" title="Sent straight to this PC over your own '
          + 'network. It never went to the internet.">on your network</span>'
        : "";

      // Sent text is shown as dots and a length, never as itself. What
      // arrives this way is usually a key or a password, and a window is a
      // thing other people can see over your shoulder, screen-share, or
      // screenshot. Copy is the only way it comes out, and it is gone after.
      if (e.kind === "text") {
        return '<div class="in-row' + (waiting ? " waiting" : "") + '">' +
          '<div class="what"><b>' + esc(e.from || "A device") + " · text" +
          why + road + "</b><span>" + "•".repeat(Math.min(e.chars || 8, 24)) +
          " · " + (e.chars || 0) + " characters</span></div>" +
          (waiting
            ? '<button class="primary small" data-ok="' + esc(e.id) + '">Approve</button>' +
              '<button class="ghost small" data-no="' + esc(e.id) + '">No</button>'
            : '<button class="primary small" data-copytext="' + esc(e.id) + '">Copy</button>' +
              '<span class="in-state">' + ago(e.at) + "</span>") +
          "</div>";
      }

      return '<div class="in-row' + (waiting ? " waiting" : "") + '">' +
        '<div class="what"><b>' + esc(e.from || "A device") + " · " +
        esc(e.quality || "default") + why + road + "</b><span>" + esc(e.url) + "</span></div>" +
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
    saveSetting({ sharing: on }).then(function (res) {
      if (!res.ok) { loadSharing(); return; }   // put the switch back where it really is
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
    // Sent text: fetched only now, put straight on the clipboard, and gone
    // from the PC afterwards. It is never in the page before this press, so
    // there is nothing to read off the screen and nothing left to press twice.
    var copy = e.target.closest("[data-copytext]");
    if (copy) {
      api("/api/share/take-text", { id: copy.dataset.copytext })
        .then(function (res) {
          if (res.state) renderSharing(res.state);
          if (!res.ok || !res.text) {
            toast("That text is gone - it expires, and copying takes it", "bad");
            return;
          }
          navigator.clipboard.writeText(res.text).then(function () {
            toast("Copied - and removed from this PC", "good");
          }, function () {
            // Clipboard refused (no focus, or the browser said no). The text
            // has already been taken, so saying "copied" would be a lie and
            // losing it silently would be worse.
            toast("Could not reach the clipboard. Send it again.", "bad");
          });
        });
      return;
    }

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

  /* Save, and say so honestly when it did not happen.

     This used to return the answer and leave it at that, and most callers
     said "Saved" from a .then that never looked at it - so a refused save was
     reported to the user as a successful one, and the setting was back to its
     old value at the next launch with nothing having said a word. A message
     that tells you the opposite of what happened is worse than no message.

     Reported here, once, so every caller is covered whether or not it checks;
     callers must still not claim success themselves - `res.ok` decides that. */
  function saveSetting(patch) {
    return api("/api/settings", patch).then(function (res) {
      if (res && res.ok) {
        settings = res.settings;
        return res;
      }
      toast((res && res.error) || "That could not be saved", "bad");
      return res || { ok: false };
    }, function () {
      // The request itself did not complete - no answer at all is still a
      // failure, and silence here would be the same lie by another route.
      toast("That could not be saved", "bad");
      return { ok: false };
    });
  }

  $("setQuality").addEventListener("change", function (e) {
    quality = e.target.value;
    saveSetting({ default_quality: e.target.value })
      .then(function (res) { if (res.ok) toast("Saved"); });
  });

  $("setParallel").addEventListener("change", function (e) {
    saveSetting({ max_parallel: parseInt(e.target.value, 10) })
      .then(function (res) { if (res.ok) toast("Saved"); });
  });

  $("setCookies").addEventListener("change", function (e) {
    var value = e.target.value;
    saveSetting({ cookies_browser: value }).then(function (res) {
      if (!res.ok) return;
      toast(value === "none" ? "Cookies off" : "Using " + value + " cookies", "good");
    });
  });

  /* Listed one per row so a single file can be taken back out. With several
     of them, "Clear" as the only way out means redoing the whole set. */
  function renderCookieFiles(list) {
    var box = $("cookieFileList");
    if (!box) return;
    box.innerHTML = "";

    if (!list || !list.length) {
      var none = document.createElement("code");
      none.textContent = "None added";
      box.appendChild(none);
      return;
    }

    list.forEach(function (path) {
      var row = document.createElement("div");
      row.className = "cookie-file";

      var name = document.createElement("code");
      name.textContent = path;
      name.title = path;

      var drop = document.createElement("button");
      drop.className = "ghost small";
      drop.type = "button";
      drop.textContent = "Remove";
      drop.addEventListener("click", function () {
        api("/api/cookies/remove-file", { path: path }).then(function (res) {
          if (!res.ok) { toast("Could not remove that one.", "bad"); return; }
          renderCookieFiles(res.settings.cookies_files);
          toast("Removed");
        });
      });

      row.appendChild(name);
      row.appendChild(drop);
      box.appendChild(row);
    });
  }

  $("chooseCookies").addEventListener("click", function () {
    api("/api/choose-cookies", {}).then(function (res) {
      if (res.ok) {
        renderCookieFiles(res.settings.cookies_files);
        toast("Cookie files added", "good");
      } else if (!res.cancelled) {
        toast(res.error || "Could not open the picker.", "bad");
      }
    });
  });

  $("clearCookies").addEventListener("click", function () {
    saveSetting({ cookies_files: [], cookies_file: "" }).then(function (res) {
      if (!res.ok) return;             // the files are still there; do not redraw as if they went
      renderCookieFiles([]);
      toast("Cookie files cleared");
    });
  });

  // Switching one of these on when the media tool is missing has to say so
  // immediately - the whole point is that it is never a silent no.
  var NEEDS_TOOLS = ["embed_subs", "embed_chapters", "sponsorblock"];

  function bindToggle(id, key) {
    $(id).addEventListener("click", function () {
      var on = !$(id).classList.contains("on");
      $(id).classList.toggle("on", on);
      var patch = {}; patch[key] = on;
      saveSetting(patch).then(function (res) {
        if (!res.ok) return;
        if (NEEDS_TOOLS.indexOf(key) >= 0) loadEngineVersion();
      });
      if (key === "auto_paste" && !on) $("clipHint").hidden = true;
      if (key === "hotkey") toast("Restart Riplox for this to take effect");
    });
  }
  bindToggle("setH264", "prefer_h264");
  bindToggle("setSubfolder", "subfolder_per_site");
  bindToggle("setAutoDownload", "auto_download");
  bindToggle("setHotkey", "hotkey");

  /* Choosing the shortcut.
   *
   * ⚠️ The key is read from event.code, never event.key. Windows registers a
   * VIRTUAL-KEY code, which follows the physical key; event.key is the letter
   * the layout produces. They agree on a US keyboard and disagree on a French
   * or German one, so reading the letter would register a key nobody pressed -
   * and it would test perfectly here and fail abroad.
   */
  var comboKeys = null;

  function codeToKey(code) {
    if (/^Key[A-Z]$/.test(code)) return code.slice(3);
    if (/^Digit[0-9]$/.test(code)) return code.slice(5);
    if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) return code;
    return "";
  }

  function showCombo(text, kind) {
    var note = $("comboNote");
    if (!note) return;
    note.className = "hint" + (kind ? " " + kind : "");
    note.textContent = text;
  }

  function stopPicking() {
    if (!comboKeys) return;
    document.removeEventListener("keydown", comboKeys, true);
    comboKeys = null;
    var btn = $("pickCombo");
    if (btn) btn.textContent = "Pick keys";
  }

  var pickBtn = $("pickCombo");
  if (pickBtn) {
    pickBtn.addEventListener("click", function () {
      if (comboKeys) { stopPicking(); showCombo("", ""); return; }
      pickBtn.textContent = "Press keys…";
      showCombo("Hold Ctrl, Alt or Shift and press a letter, a number or an F key. Esc to stop.", "");

      comboKeys = function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (e.key === "Escape") { stopPicking(); showCombo("", ""); return; }

        var key = codeToKey(e.code);
        if (!key) return;                       // a modifier on its own: wait

        var parts = [];
        if (e.ctrlKey) parts.push("Ctrl");
        if (e.altKey) parts.push("Alt");
        if (e.shiftKey) parts.push("Shift");
        if (!parts.length) {
          showCombo("Hold Ctrl, Alt or Shift as well - on its own that key "
                    + "would stop working everywhere else.", "warn");
          return;
        }
        parts.push(key);
        stopPicking();

        var combo = parts.join("+");
        api("/api/settings", { hotkey_combo: combo }).then(function (res) {
          if (!res || !res.ok) {
            showCombo((res && res.error) || "Could not use that one.", "warn");
            return;
          }
          var saved = (res.settings && res.settings.hotkey_combo) || combo;
          showCombo(saved + " — restart Riplox for this to take effect.", "");
          toast("Restart Riplox for this to take effect");
        });
      };
      document.addEventListener("keydown", comboKeys, true);
    });
  }
  bindToggle("setAutoPaste", "auto_paste");
  bindToggle("setPace", "pace_sites");
  // On the Failed page rather than in Settings - it is about that page, and
  // this is where someone is standing when they decide they want it.
  bindToggle("setFailedTidy", "failed_clear_on_success");
  bindToggle("setThumb", "write_thumbnail");
  bindToggle("setPolite", "polite_mode");
  bindToggle("setUpscale", "allow_ai_upscale");
  bindToggle("setSponsor", "sponsorblock");
  bindToggle("setSubs", "write_subs");
  bindToggle("setEmbedSubs", "embed_subs");
  bindToggle("setChapters", "embed_chapters");
  bindToggle("setSkipExisting", "skip_existing");
  bindToggle("setShareApprove", "share_approve");
  bindToggle("setNotify", "notify");
  bindToggle("setNotifySent", "notify_sent");
  bindToggle("setNotifyDone", "notify_done");
  bindToggle("setNotifyFailed", "notify_failed");
  bindToggle("setNotifyWatch", "notify_watch");

  /* With the master off, the four below decide nothing. Left switchable but
     visibly inert, so turning the master back on restores exactly what was
     chosen before rather than resetting it. */
  var NOTIFY_KINDS = ["setNotifySent", "setNotifyDone",
                      "setNotifyFailed", "setNotifyWatch"];

  function syncNotifyGroup() {
    var on = $("setNotify").classList.contains("on");
    NOTIFY_KINDS.forEach(function (id) {
      var row = $(id).closest(".field");
      if (row) row.classList.toggle("is-inert", !on);
      $(id).disabled = !on;
    });
  }

  $("setNotify").addEventListener("click", function () {
    // After bindToggle's own handler has flipped the class.
    setTimeout(syncNotifyGroup, 0);
  });
  syncNotifyGroup();

  $("setRelay").addEventListener("change", function (e) {
    var value = (e.target.value || "").trim();
    if (value && !/^wss?:\/\//i.test(value)) {
      toast("A relay address starts with wss://", "bad");
      e.target.value = settings.share_relay || "";
      return;
    }
    saveSetting({ share_relay: value }).then(function (res) {
      e.target.value = (res.settings || settings).share_relay || "";
      if (res.ok) toast("Saved");
    });
  });

  // The two subtitle details are meaningless with subtitles off.
  function syncSubFields() {
    var on = $("setSubs").classList.contains("on");
    $("subKindField").hidden = !on;
    $("subLangField").hidden = !on;
    $("embedSubField").hidden = !on;
  }
  $("setSubs").addEventListener("click", syncSubFields);
  syncSubFields();

  $("setSubKind").addEventListener("change", function (e) {
    saveSetting({ sub_kind: e.target.value }).then(function (res) {
      if (!res.ok) return;
      toast(e.target.selectedOptions[0].text, "good");
    });
  });

  $("setSubLangs").addEventListener("change", function (e) {
    var value = (e.target.value || "").trim() || "en";
    e.target.value = value;
    saveSetting({ sub_langs: value }).then(function (res) { if (res.ok) toast("Saved"); });
  });

  /* A proxy is the one setting here that can be typed wrong in a way nothing
     else notices: everything keeps working, slower, out of the connection the
     user meant to hide. So it is checked on the way in, and the one thing it
     does not cover - the fallback route, which cannot speak SOCKS - is said
     on screen rather than discovered later. */
  function syncProxyNote(value) {
    var note = $("proxyNote");
    var scheme = (value || "").split("://")[0].toLowerCase();
    if (value && scheme.indexOf("socks") === 0) {
      note.textContent = "Downloads will go through this proxy. Riplox's own "
        + "route — the one that runs when the engine is refused — cannot use "
        + "a SOCKS proxy, and will not go around it, so it stays off while "
        + "this is set. An http:// proxy works for both.";
      note.hidden = false;
    } else {
      note.hidden = true;
    }
  }
  syncProxyNote($("setProxy").value);

  $("setProxy").addEventListener("change", function (e) {
    var value = (e.target.value || "").trim();
    e.target.value = value;
    saveSetting({ proxy: value }).then(function (res) {
      if (res && res.ok === false) {
        toast(res.error || "That proxy address cannot be used.", "bad");
        return;
      }
      syncProxyNote(value);
      toast(value ? "Going out through " + value : "Connecting directly", "good");
    });
  });

  bindToggle("setCheckUpdates", "check_updates");

  /* Start with Windows is not a setting in settings.json - the registry is the
     only truthful answer, so the toggle reads back from it and shows what
     actually happened rather than what was clicked. */
  $("setAutostart").addEventListener("click", function () {
    var btn = $("setAutostart");
    var want = !btn.classList.contains("on");
    btn.disabled = true;
    api("/api/autostart", { on: want }).then(function (res) {
      btn.disabled = false;
      btn.classList.toggle("on", !!res.on);
      toast(res.message || (res.ok ? "Saved" : "Could not change that."),
            res.ok ? "good" : "bad");
    });
  });

  $("setSpeedLimit").addEventListener("change", function (e) {
    var kb = parseInt(e.target.value, 10) || 0;
    saveSetting({ speed_limit: kb }).then(function (res) {
      if (!res.ok) return;
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
      .then(function (res) { if (res.ok) toast("Saved"); });
  });

  /* ------------------------------------------------------------- schedule */

  function saveSchedule(on) {
    var from = $("setScheduleFrom").value || "01:00";
    var to = $("setScheduleTo").value || "08:00";
    return saveSetting({ schedule_on: on, schedule_from: from, schedule_to: to });
  }

  $("setSchedule").addEventListener("click", function () {
    var on = !$("setSchedule").classList.contains("on");
    $("setSchedule").classList.toggle("on", on);
    saveSchedule(on).then(function () {
      toast(on ? "Downloads will run " + $("setScheduleFrom").value
                 + "–" + $("setScheduleTo").value
               : "Downloads run at any time", "good");
      pollJobs();          // so the queue says why it is waiting, right away
    });
  });

  ["setScheduleFrom", "setScheduleTo"].forEach(function (id) {
    $(id).addEventListener("change", function () {
      // Changing the hours while it is off is just setting them up; it should
      // not quietly switch the thing on.
      saveSchedule($("setSchedule").classList.contains("on"))
        .then(function () { toast("Saved"); pollJobs(); });
    });
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

    renderSites(c);
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
    // The files live in settings rather than in the cookie store, so they are
    // read from there - and only when this screen is actually opened.
    api("/api/settings").then(function (res) {
      if (res.ok) renderCookieFiles(res.settings.cookies_files);
    });
  }

  /* One row per site, built from what the engine reports rather than from a
     list kept here as well - a second copy is a second thing to forget. */
  function renderSites(c) {
    var box = $("siteSignins");
    if (!box) return;
    var sites = c.known || [];

    if (box.childElementCount !== sites.length) {
      box.innerHTML = "";
      sites.forEach(function (site) {
        var row = document.createElement("div");
        row.className = "site-row";
        row.dataset.site = site.key;

        var name = document.createElement("span");
        name.className = "site-name";
        name.textContent = site.label;

        var mark = document.createElement("span");
        mark.className = "site-mark";

        var go = document.createElement("button");
        go.className = "ghost small";
        go.type = "button";
        go.addEventListener("click", function () { signIn(site.key, site.label); });

        /* Read from the row rather than from `site`, which is the snapshot
           this row was built with and goes stale on the next poll. */
        var hold = document.createElement("button");
        hold.className = "ghost small";
        hold.type = "button";
        hold.addEventListener("click", function () {
          pauseSite(site.key, site.label, row.dataset.paused !== "1");
        });

        var out = document.createElement("button");
        out.className = "ghost small";
        out.type = "button";
        out.textContent = "Forget";
        out.addEventListener("click", function () { forgetSite(site.key, site.label); });

        // A spare account for the same site. Offered on every row rather
        // than hidden behind a menu, but only useful once the first one is
        // signed in - which is what the update below decides.
        var more = document.createElement("button");
        more.className = "ghost small";
        more.type = "button";
        more.textContent = "+ account";
        more.title = "Sign in a second account for this site";
        more.addEventListener("click", function () { addAccount(site.key, site.label); });

        row.appendChild(name);
        row.appendChild(mark);
        row.appendChild(go);
        row.appendChild(hold);
        row.appendChild(out);
        row.appendChild(more);
        box.appendChild(row);
      });
    }

    sites.forEach(function (site, i) {
      var row = box.children[i];
      if (!row) return;
      var held = !!site.paused;
      row.dataset.paused = held ? "1" : "";
      /* Paused is its own state, not a shade of signed in: the session is
         still there, it is just not being sent. */
      row.classList.toggle("in", !!site.signedIn && !held);
      row.classList.toggle("held", held);
      row.children[1].textContent = held ? "Paused"
        : (site.signedIn ? "Signed in" : "Not signed in");
      row.children[2].textContent = site.signedIn ? "Sign in again" : "Sign in";
      row.children[2].disabled = !!c.busy || !c.browser;
      row.children[3].hidden = !site.signedIn;
      row.children[3].textContent = held ? "Use again" : "Pause";
      row.children[3].disabled = !!c.busy;
      row.children[4].hidden = !site.signedIn;
      row.children[4].disabled = !!c.busy;
      row.children[5].hidden = !site.signedIn;
      row.children[5].disabled = !!c.busy;
    });

    renderExtraAccounts(c);
  }

  /* The extra accounts, in a list of their own rather than folded into the
     rows above. The site rows are patched by position on every poll, and
     growing them by a variable number of children is how that quietly breaks. */
  function renderExtraAccounts(c) {
    var box = $("extraAccounts");
    if (!box) return;

    var rows = [];
    (c.known || []).forEach(function (site) {
      (site.accounts || []).forEach(function (acct) {
        if (acct.n >= 2) rows.push({ site: site, acct: acct });
      });
    });

    box.hidden = rows.length === 0;
    if (box.hidden) { box.innerHTML = ""; return; }

    box.innerHTML =
      '<p class="conv-note">A spare for when a session stops working, or an ' +
      'account that can see something the other cannot. Riplox uses whichever ' +
      'has gone longest without a turn — one at a time, never both at once. ' +
      'They all go out from this PC, so the sites can still tell they belong ' +
      'to the same person.</p>' +
      rows.map(function (r) {
        var state = r.acct.paused ? "Paused"
          : (r.acct.signedIn ? "Signed in" : "Not signed in");
        return '<div class="site-row' +
          (r.acct.signedIn && !r.acct.paused ? " in" : "") +
          (r.acct.paused ? " held" : "") + '">' +
          '<span class="site-name">' + esc(r.site.label) + " · " +
            esc(r.acct.label) + "</span>" +
          '<span class="site-mark">' + state + "</span>" +
          '<button class="ghost small" data-asignin="' + esc(r.site.key) +
            '" data-n="' + r.acct.n + '">' +
            (r.acct.signedIn ? "Sign in again" : "Sign in") + "</button>" +
          (r.acct.signedIn
            ? '<button class="ghost small" data-apause="' + esc(r.site.key) +
              '" data-n="' + r.acct.n + '" data-to="' + (r.acct.paused ? "0" : "1") +
              '">' + (r.acct.paused ? "Use again" : "Pause") + "</button>"
            : "") +
          '<button class="ghost small danger" data-aremove="' + esc(r.site.key) +
            '" data-n="' + r.acct.n + '">Remove</button>' +
          "</div>";
      }).join("");
  }

  function addAccount(site, label) {
    api("/api/cookies/account/add", { site: site }).then(function (res) {
      if (!res.ok) { toast(res.error || "Could not add that.", "bad"); return; }
      // Added and then signed in, because an account with no session is a row
      // that does nothing - the browser window is the point of pressing this.
      signIn(site, label + " (account " + res.n + ")", res.n);
    });
  }

  $("extraAccounts").addEventListener("click", function (e) {
    var go = e.target.closest("[data-asignin]");
    if (go) {
      signIn(go.dataset.asignin, go.dataset.asignin + " (account " +
             go.dataset.n + ")", parseInt(go.dataset.n, 10));
      return;
    }

    var hold = e.target.closest("[data-apause]");
    if (hold) {
      api("/api/cookies/pause", { site: hold.dataset.apause,
                                  account: parseInt(hold.dataset.n, 10),
                                  on: hold.dataset.to === "1" }).then(function (res) {
        if (!res.ok) { toast(res.error || "Could not change that.", "bad"); return; }
        loadCookies();
      });
      return;
    }

    var drop = e.target.closest("[data-aremove]");
    if (drop) {
      ask("Remove this account? Its sign-in is deleted from this PC. The "
          + "other accounts are not touched.",
          { ok: "Remove", danger: true }).then(function (yes) {
        if (!yes) return;
        api("/api/cookies/account/remove", { site: drop.dataset.aremove,
                                             account: parseInt(drop.dataset.n, 10) })
          .then(function (res) {
            if (!res.ok) { toast(res.error || "Could not remove it.", "bad"); return; }
            toast("Account removed");
            loadCookies();
          });
      });
    }
  });

  function signIn(site, label, account) {
    api("/api/cookies/signin", { site: site, account: account || 1 }).then(function (res) {
      if (!res.ok) { toast(res.error || "Could not open the browser.", "bad"); return; }
      toast("Sign in to " + label + ", then close that window", "good");
      loadCookies();
    });
  }

  function pauseSite(site, label, on) {
    api("/api/cookies/pause", { site: site, on: on }).then(function (res) {
      if (!res.ok) { toast(res.error || "Could not change that.", "bad"); return; }
      renderCookies(res.cookies);
      toast(on ? label + " paused — downloads will run signed out"
               : label + " session is back on", on ? "" : "good");
    });
  }

  function forgetSite(site, label) {
    api("/api/cookies/forget", { site: site }).then(function (res) {
      if (res.ok) renderCookies(res.cookies);
      toast(label + " signed out");
    });
  }

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

  /* --------------------------------------------------------- stuck check */

  /* "Did anything I sent get lost?" - asked of the relay rather than of the
     phone, because a PC cannot reliably wake a phone and the relay already
     keeps undelivered links for a week. Counts only: the relay cannot read
     them and is not asked to hand them over. */

  $("checkStuck").addEventListener("click", function () {
    var btn = $("checkStuck");
    var note = $("stuckNote");
    btn.disabled = true;
    note.textContent = "Asking…";

    api("/api/share/pending").then(function (res) {
      if (!res.ok) {
        note.textContent = res.error
          ? "Could not ask: " + res.error
          : "Could not reach the relay.";
        return;
      }
      if (!res.stuck) {
        note.textContent = "Nothing waiting — everything sent has arrived.";
        return;
      }
      // Two different things, and the difference is what to do next.
      var bits = [];
      if (res.waiting) bits.push(res.waiting + " never delivered");
      if (res.held) bits.push(res.held + " delivered but not finished here");
      note.textContent = bits.join(" · ")
        + (res.since ? " · oldest " + res.since : "")
        + ". They arrive on their own while Sharing is on.";
      pollJobs();
    }).catch(function () {
      note.textContent = "Could not reach the relay.";
    }).then(function () { btn.disabled = false; });
  });

  /* ---------------------------------------------------------- accounts */

  /* Who you download from, out of your own history. No new request anywhere:
     the uploader has been recorded with every finished download for a while,
     so this is reading what is already there. Clicking a name puts it in the
     library search rather than making a second kind of filter. */

  var accountsShown = false;

  $("showAccounts").addEventListener("click", function () {
    accountsShown = !accountsShown;
    var box = $("accountsBox");
    box.hidden = !accountsShown;
    $("showAccounts").classList.toggle("on", accountsShown);
    if (!accountsShown) return;

    api("/api/accounts").then(function (res) {
      box.innerHTML = "";
      var list = (res.ok && res.accounts) || [];
      if (!list.length) {
        var none = document.createElement("p");
        none.className = "accounts-none";
        none.textContent = "No uploader has been recorded yet. This fills in "
                         + "as you download.";
        box.appendChild(none);
        return;
      }
      list.forEach(function (row) {
        var chip = document.createElement("button");
        chip.type = "button";
        chip.className = "chip account-chip";
        chip.title = row.count + (row.count === 1 ? " download" : " downloads")
                   + (row.site ? " · " + row.site : "");
        chip.textContent = row.name;

        var tally = document.createElement("em");
        tally.textContent = row.count;
        chip.appendChild(tally);

        chip.addEventListener("click", function () {
          $("libSearch").value = row.name;
          $("libSearch").dispatchEvent(new Event("input"));
        });
        box.appendChild(chip);
      });
    });
  });

  /* ------------------------------------------------------- how sites are */

  /* "Is it broken, or is it me" is the first question anybody has, and until
     now nothing in the app answered it. Three states, because Riplox has two
     ways in and the middle one is the interesting one: the usual route has
     gone and the download only happened because of the fallback. That is the
     early warning, and it is worth more than the green tick either side. */

  var HEALTH = {
    ok:   { mark: "●", word: "Working" },
    door: { mark: "◐", word: "Working — the usual route was refused" },
    down: { mark: "○", word: "Failed here" }
  };

  function renderHealth(rows) {
    var box = $("healthList");
    if (!box) return;
    box.innerHTML = "";

    if (!rows || !rows.length) {
      var none = document.createElement("p");
      none.className = "health-none";
      none.textContent = "Nothing downloaded yet, so there is nothing to "
                       + "report. This fills in as you use it.";
      box.appendChild(none);
      return;
    }

    rows.forEach(function (row) {
      var spec = HEALTH[row.state] || HEALTH.ok;
      var line = document.createElement("div");
      line.className = "health-row is-" + row.state;

      // Shape as well as colour: the three states have to be tellable apart
      // without relying on being able to see the difference between them.
      var dot = document.createElement("span");
      dot.className = "health-dot";
      dot.textContent = spec.mark;

      var name = document.createElement("span");
      name.className = "health-site";
      name.textContent = row.site;

      var what = document.createElement("span");
      what.className = "health-what";
      what.textContent = spec.word + (row.why ? " · " + row.why : "");

      var when = document.createElement("span");
      when.className = "health-when";
      when.textContent = row.ago;

      line.appendChild(dot);
      line.appendChild(name);
      line.appendChild(what);
      line.appendChild(when);
      box.appendChild(line);
    });
  }

  function loadHealth() {
    api("/api/health").then(function (res) {
      if (res.ok) renderHealth(res.sites);
    });
  }

  /* A report worth pasting. Shown as well as copied: a button that claims to
     have copied something invisible is asking to be trusted for no reason,
     and this way the contents can be read before they go anywhere. */
  $("copyDiag").addEventListener("click", function () {
    api("/api/diagnostics").then(function (res) {
      if (!res.ok) { toast("Could not build the report.", "bad"); return; }
      var box = $("diagBox");
      box.textContent = res.report;
      box.hidden = false;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(res.report).then(function () {
          toast("Report copied", "good");
        }, function () {
          toast("Shown below — select it and copy");
        });
      } else {
        toast("Shown below — select it and copy");
      }
    });
  });

  /* ------------------------------------------- options that are not applying */

  /* The worst thing this app can do is drop something quietly. Without the
     media tool, "put subtitles inside the video", "chapter marks" and
     "skip sponsor segments" are accepted, switched on, shown as on - and
     never happen. The engine now leaves those flags out rather than sending
     them to be ignored, and says which ones, so the screen can say it where
     the switch is instead of leaving the user to find out from a file. */

  var DROP_ROWS = {
    "subtitles inside the video": "setEmbedSubs",
    "chapter marks": "setChapters",
    "skipping sponsor segments": "setSponsor"
  };

  function markDropped(names) {
    Object.keys(DROP_ROWS).forEach(function (label) {
      var toggle = document.getElementById(DROP_ROWS[label]);
      if (!toggle) return;
      var text = toggle.closest(".field") &&
                 toggle.closest(".field").querySelector(".field-text");
      if (!text) return;

      var note = text.querySelector(".drop-note");
      var lost = (names || []).indexOf(label) >= 0;
      if (!lost) { if (note) note.remove(); return; }
      if (!note) {
        note = document.createElement("span");
        note.className = "note drop-note";
        text.appendChild(note);
      }
      note.textContent = "Switched on, but not happening: this needs the "
                       + "media tool, which is missing from this install. "
                       + "Reinstall Riplox to get it back.";
    });
  }

  /* ------------------------------------------------------ settings groups */

  /* A list of categories on the left, one category on the right.

     This was an accordion of shut rows. That shape answers "what can this app
     do" with a click per group, and at thirteen groups the screen had become a
     wall of closed doors - which is exactly the complaint that produced this
     rewrite. A list costs nothing to read, shows every category at once, and
     keeps the panel no longer than a single group.

     Four of those thirteen were never settings at all - the changelog, the
     roadmap, site health and reporting a bug. They are marked data-info in the
     template and live under About, so the list is only things you can change.
     Marked there rather than matched by name here, because renaming one should
     not quietly move it back in among the switches. */

  var SETTINGS_OPEN_BY_DEFAULT = "Downloads";
  var groups = [];          // every group, in template order
  var currentGroup = null;       // the one on show while not searching

  function buildSettingsNav() {
    var view = $("view-settings");
    var heads = Array.prototype.slice.call(view.querySelectorAll(".group-head"));
    if (!heads.length) return;

    var cols  = mk("div", "set-cols");
    var nav   = mk("nav", "set-nav");
    var body  = mk("div", "set-body");
    var about = mk("div", "set-about");
    nav.setAttribute("aria-label", "Settings categories");
    cols.appendChild(nav);
    cols.appendChild(body);
    heads[0].parentNode.insertBefore(cols, heads[0]);

    heads.forEach(function (head) {
      var panel = head.nextElementSibling;
      if (!panel || panel.className.indexOf("panel") < 0) return;

      var g = {
        head:  head,
        panel: panel,
        name:  head.textContent.trim(),
        what:  head.dataset.what || "",
        count: panel.querySelectorAll(".field").length,
        info:  head.dataset.info === "1"
      };

      // Head and panel stay siblings inside the wrapper, because the search
      // reaches a row's heading through panel.previousElementSibling.
      g.box = mk("div", "set-group");
      g.box.appendChild(head);
      g.box.appendChild(panel);
      body.appendChild(g.box);
      panel.hidden = false;              // the wrapper does the hiding now

      var target = g.info ? about : nav;
      target.appendChild(g.info ? aboutCard(g) : navButton(g));
      groups.push(g);
    });

    if (about.childNodes.length) {
      var label = mk("div", "set-about-label");
      label.textContent = "About Riplox";
      about.insertBefore(label, about.firstChild);
      cols.parentNode.insertBefore(about, cols.nextSibling);
    }

    showGroup(byName(SETTINGS_OPEN_BY_DEFAULT) || groups[0]);
  }

  function mk(tag, cls) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    return n;
  }

  function byName(name) {
    for (var i = 0; i < groups.length; i++) {
      if (groups[i].name === name) return groups[i];
    }
    return null;
  }

  function navButton(g) {
    var btn = mk("button", "set-cat");
    btn.type = "button";

    var name = mk("span", "set-cat-name");
    name.textContent = g.name;

    // The count is what makes the list worth reading rather than just
    // clickable: it says how much is behind each name before you go there.
    var tally = mk("span", "set-cat-count");
    tally.textContent = g.count ? g.count : "";

    btn.appendChild(name);
    btn.appendChild(tally);
    btn.addEventListener("click", function () {
      if ($("setSearch").value) {          // leaving a search by picking a category
        $("setSearch").value = "";
      }
      showGroup(g);
      applySettingsFilter();
    });
    g.btn = btn;
    return btn;
  }

  function aboutCard(g) {
    var card = mk("button", "set-info");
    card.type = "button";

    var text = mk("span", "set-info-text");
    var b = mk("b");  b.textContent = g.head.dataset.short || g.name;
    var s = mk("small"); s.textContent = g.head.dataset.sub || "";
    text.appendChild(b);
    text.appendChild(s);
    card.appendChild(text);

    card.addEventListener("click", function () {
      if ($("setSearch").value) $("setSearch").value = "";
      showGroup(g);
      applySettingsFilter();
      g.box.scrollIntoView({ block: "nearest" });
    });
    g.btn = card;
    return card;
  }

  /* Show exactly one group. Used while nothing is being searched for; a
     search takes over and shows every match instead, across all of them. */
  function showGroup(g) {
    if (!g) return;
    currentGroup = g;
    groups.forEach(function (o) {
      o.box.hidden = o !== g;
      if (o.btn) {
        o.btn.classList.toggle("is-on", o === g);
        o.btn.setAttribute("aria-current", o === g ? "true" : "false");
      }
    });
  }

  buildSettingsNav();

  /* ------------------------------------------------- searching settings */

  /* The rows most people never need. Not hidden because they are dangerous -
     hidden because a first-time reader counting thirty switches stops reading.
     Every one of them stays one tick away, and a search finds them even while
     they are hidden, so nothing becomes unreachable. */
  var ADVANCED = ["setSpeedLimit", "setParallel", "setFragments", "setPotoken",
                  "setSkipExisting", "setChannel", "setCheckUpdates",
                  "setUpscale", "chooseCookies", "setCookies",
                  "settingsExport", "settingsImport"];

  function fieldOf(id) {
    var el = document.getElementById(id);
    return el ? el.closest(".field") : null;
  }

  ADVANCED.forEach(function (id) {
    var field = fieldOf(id);
    if (field) field.dataset.adv = "1";
  });

  function settingRows() {
    return Array.prototype.slice.call(
      document.querySelectorAll("#view-settings .panel .field"));
  }

  function applySettingsFilter() {
    var term = ($("setSearch").value || "").trim().toLowerCase();
    var advanced = $("setShowAdvanced").checked;
    var hits = 0;

    settingRows().forEach(function (row) {
      // Name AND description AND the group it sits in - so a word like
      // "cookie" finds rows that never use it in their label.
      var head = row.closest(".panel").previousElementSibling;
      var hay = (row.textContent + " " +
                 (head ? head.textContent + " " + (head.dataset.what || "") : ""))
                .toLowerCase();
      var matches = !term || hay.indexOf(term) >= 0;
      // While searching, an advanced row still shows: hiding a thing someone
      // just typed the name of would be the app arguing with them.
      var allowed = advanced || term || !row.dataset.adv;
      row.hidden = !(matches && allowed);
      if (matches && allowed) hits++;
    });

    // While a search is running the categories step aside and every group
    // holding a hit is shown at once. There is nothing to "open" in a list, so
    // without this a match sitting in a category you are not looking at would
    // simply be invisible - and a search that cannot reach two thirds of the
    // screen is worse than no search. With the box empty, one category shows
    // and the rest wait.
    groups.forEach(function (g) {
      if (!term) {
        g.box.hidden = g !== currentGroup;
      } else if (g.count) {
        g.box.hidden = !g.panel.querySelectorAll(".field:not([hidden])").length;
      } else {
        // Nothing to filter inside these - the changelog and the rest are
        // prose, so they answer to their own name and description only.
        var label = (g.name + " " + g.what).toLowerCase();
        g.box.hidden = label.indexOf(term) < 0;
      }
      if (g.btn) {
        g.btn.classList.toggle("is-on", !term && g === currentGroup);
        // A category still holding matches is worth pointing at, so the list
        // stays useful during a search instead of going inert.
        g.btn.classList.toggle("has-hit", !!term && !g.box.hidden);
      }

      // The number has to be the number you will actually see.
      //
      // It was the total, which is only right while advanced is on - and
      // advanced is off by default. So the list said "Sign-in 3" and the panel
      // gave you one row, with nothing on screen to explain the other two.
      // That is the exact shape of a bug that already cost two days here: a
      // hidden row and a removed feature look identical, so a count that
      // over-promises is not a cosmetic problem, it is the app telling you
      // something untrue. The toggle still says how many are hidden overall.
      var tally = g.btn && g.btn.querySelector(".set-cat-count");
      if (tally) {
        var shown = g.panel.querySelectorAll(".field:not([hidden])").length;
        tally.textContent = g.count ? shown : "";
      }
    });

    // The toggle carries its own count, because "Show advanced" on its own
    // never told anyone that twelve settings were sitting behind it - and a
    // row nobody knows exists is indistinguishable from a row that was taken
    // out of the program. While a search is running these are on show anyway,
    // so it says that instead of offering to reveal what is already revealed.
    var advTotal = document.querySelectorAll(
      "#view-settings .field[data-adv]").length;
    $("setAdvChip").classList.toggle("is-on", advanced);
    $("setAdvText").textContent =
      advanced ? "Hide advanced"
               : (term ? "Show advanced" : "Show " + advTotal + " advanced");

    $("setFound").textContent = term
      ? (hits ? hits + (hits === 1 ? " setting" : " settings") + " match"
              : "Nothing matches that")
      : "";
  }

  $("setSearch").addEventListener("input", applySettingsFilter);
  $("setShowAdvanced").addEventListener("change", function () {
    var wanted = $("setShowAdvanced").checked;
    applySettingsFilter();                     // answer the click at once
    saveSetting({ show_advanced: wanted }).then(function (res) {
      // If it was not saved, the switch has to go back. Left where the user
      // put it, the screen would show one thing now and the opposite at the
      // next launch, with nothing in between to explain it.
      if (res.ok || $("setShowAdvanced").checked !== wanted) return;
      $("setShowAdvanced").checked = !wanted;
      applySettingsFilter();
    });
  });

  $("setShowAdvanced").checked = !!settings.show_advanced;
  applySettingsFilter();

  /* ---------------------------------------------------------- site picker */

  /* Two jobs, one component: choosing the sites a rule applies to, and
     answering "what does this actually work with". Deliberately two lists -
     rules can only be written against the names site_of() produces, so
     offering the engine's 1,750 extractor names as choices would let someone
     pick one that can never match anything and never be told. */

  var siteData = null;                    // fetched once, then kept
  var pickerState = { open: false, mode: "pick", picked: [], onSave: null };
  // Long lists are capped rather than rendered whole; the cap is stated in
  // the header, never applied silently.
  var SITE_ROW_CAP = 250;

  function loadSites() {
    if (siteData) return Promise.resolve(siteData);
    return api("/api/sites").then(function (res) {
      siteData = res && res.ok ? res : { pickable: [], all: [], count: 0 };
      return siteData;
    });
  }

  function renderPickable() {
    var box = $("sitePickList");
    box.innerHTML = "";
    if (pickerState.mode !== "pick") return;

    var term = $("siteSearch").value.trim().toLowerCase();
    (siteData.pickable || []).forEach(function (name) {
      if (term && name.toLowerCase().indexOf(term) < 0) return;
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip" + (pickerState.picked.indexOf(name) >= 0 ? " is-on" : "");
      chip.textContent = name;
      chip.setAttribute("aria-pressed", pickerState.picked.indexOf(name) >= 0);
      chip.addEventListener("click", function () {
        var at = pickerState.picked.indexOf(name);
        if (at >= 0) pickerState.picked.splice(at, 1);
        else pickerState.picked.push(name);
        renderPickable();
      });
      box.appendChild(chip);
    });
  }

  function renderAll() {
    var head = $("siteAllHead"), list = $("siteAllList");
    var term = $("siteSearch").value.trim().toLowerCase();
    var names = siteData.all || [];
    var hits = term
      ? names.filter(function (n) { return n.toLowerCase().indexOf(term) >= 0; })
      : names;

    list.innerHTML = "";
    if (!hits.length) {
      head.textContent = term ? "" : "The engine listed nothing.";
      var none = document.createElement("div");
      none.className = "site-all-none";
      none.textContent = term
        ? "Nothing matches “" + term + "”. It may still work — try the link."
        : "Could not read the list from the engine.";
      list.appendChild(none);
      return;
    }

    var shown = hits.slice(0, SITE_ROW_CAP);
    head.textContent = shown.length < hits.length
      ? "Showing " + shown.length + " of " + hits.length + " — keep typing to narrow it"
      : hits.length + (term ? " matching" : "") + " site" + (hits.length === 1 ? "" : "s");

    shown.forEach(function (name) {
      var row = document.createElement("span");
      if (term) {
        var at = name.toLowerCase().indexOf(term);
        row.appendChild(document.createTextNode(name.slice(0, at)));
        var hit = document.createElement("mark");
        hit.textContent = name.slice(at, at + term.length);
        row.appendChild(hit);
        row.appendChild(document.createTextNode(name.slice(at + term.length)));
      } else {
        row.textContent = name;
      }
      list.appendChild(row);
    });
  }

  function renderPicker() {
    renderPickable();
    renderAll();
  }

  function openSitePicker(opts) {
    loadSites().then(function (data) {
      pickerState = {
        open: true,
        mode: opts.mode || "pick",
        picked: (opts.picked || []).slice(),
        onSave: opts.onSave || null
      };

      $("sitePickerTitle").textContent = opts.title || "Sites";
      $("sitePickerMsg").textContent = opts.message ||
        (pickerState.mode === "pick"
          ? "Nothing picked means every site."
          : "Every site the installed engine can read.");
      $("siteSearch").value = "";
      $("siteAllWrap").hidden = pickerState.mode === "pick" && !data.all.length;
      // Browse mode has nothing to save, so it gets one way out, not two.
      $("sitePickerSave").hidden = pickerState.mode !== "pick";
      $("sitePickerCancel").textContent =
        pickerState.mode === "pick" ? "Cancel" : "Close";

      renderPicker();
      $("sitePicker").hidden = false;
      $("siteSearch").focus();
    });
  }

  function closeSitePicker() {
    pickerState.open = false;
    $("sitePicker").hidden = true;
  }

  $("siteSearch").addEventListener("input", function () {
    if (pickerState.open) renderPicker();
  });

  $("sitePickerCancel").addEventListener("click", closeSitePicker);

  $("sitePickerSave").addEventListener("click", function () {
    var chosen = pickerState.picked.slice();
    var done = pickerState.onSave;
    closeSitePicker();
    if (done) done(chosen);
  });

  // Escape closes it, and clicking the scrim behind it does too - the two
  // ways out anyone tries first.
  $("sitePicker").addEventListener("click", function (e) {
    if (e.target === $("sitePicker")) closeSitePicker();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && pickerState.open) closeSitePicker();
  });

  $("browseSites").addEventListener("click", function () {
    openSitePicker({
      mode: "browse",
      title: "What Riplox can download",
      message: "Read from the installed engine, so this list is what this "
             + "copy can actually reach — not a claim written once."
    });
  });

  /* The clipboard filter. Its own note has to state the current rule, because
     "instant download is on" and "instant download is on for two sites" are
     very different things to leave running. */
  function clipSitesNote(list) {
    var note = $("clipSitesNote");
    if (!note) return;
    note.textContent = (list && list.length)
      ? "Instant download only acts on: " + list.join(", ")
      : "Instant download acts on any site.";
  }

  $("clipSitesBtn").addEventListener("click", function () {
    openSitePicker({
      mode: "pick",
      picked: settings.clipboard_sites || [],
      title: "Instant download — which sites",
      message: "With nothing picked, every link you copy is downloaded. "
             + "Pick sites to narrow that to the ones you meant.",
      onSave: function (chosen) {
        saveSetting({ clipboard_sites: chosen }).then(function (res) {
          if (!res.ok) { toast("Could not save that.", "bad"); return; }
          clipSitesNote(chosen);
          toast(chosen.length
            ? "Instant download limited to " + chosen.length + " site"
              + (chosen.length === 1 ? "" : "s")
            : "Instant download acts on any site", "good");
        });
      }
    });
  });

  clipSitesNote(settings.clipboard_sites);

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
    saveSetting({ potoken: on }).then(function (res) {
      if (!res.ok) return;
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
    saveSetting({ engine_channel: e.target.value }).then(function (res) {
      if (!res.ok) return;
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
      $("setAutostart").classList.toggle("on", !!res.autostart);
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

      // Anything switched on that is not actually being applied gets said
      // next to its own switch, not left for the user to discover in a file.
      markDropped(res.dropped);
    });
    loadHealth();
  }

  /* Is a newer engine published? A line of text, nothing else - the zip is
     only ever fetched by pressing Update. Asked when the window opens and at
     most once a day after that; the throttle lives on the Python side. */
  function checkEngineUpdate(force) {
    var line = $("engineUpdate");
    if (!line) return;
    api("/api/check-engine", { force: !!force }).then(function (res) {
      if (!res || !res.newer) { line.hidden = true; return; }
      line.textContent = "A newer engine is published (" + res.latest +
        "). Press Update when you have a moment.";
      line.hidden = false;
    });
  }

  /* The update holds its own request open for as long as the download takes,
     so the percentage comes from a second endpoint. Two minutes of a silent
     button is exactly what "stuck" meant. */
  var enginePoll = null;

  function watchEngineProgress(btn) {
    clearInterval(enginePoll);
    enginePoll = setInterval(function () {
      api("/api/engine-progress").then(function (res) {
        var p = (res && res.progress) || {};
        if (!p.busy) return;
        btn.textContent = p.message || "Downloading…";
      });
    }, 700);
  }

  $("updateEngine").addEventListener("click", function () {
    var btn = $("updateEngine");
    btn.disabled = true;
    btn.textContent = "Checking…";
    watchEngineProgress(btn);

    api("/api/update-engine", { channel: $("setChannel").value }).then(function (res) {
      clearInterval(enginePoll);
      btn.disabled = false;
      btn.textContent = "Update";
      toast(res.message || (res.ok ? "Up to date" : "Update failed"), res.ok ? "good" : "bad");
      loadEngineVersion();
      checkEngineUpdate(true);
    }).catch(function () {
      clearInterval(enginePoll);
      btn.disabled = false;
      btn.textContent = "Update";
      toast("The update could not be started.", "bad");
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
          // Asked for one, given another. Saying nothing would leave somebody
          // pressing the keys they chose, wondering why nothing happens.
          if (res.hotkey === "fallback" && res.hotkeyWanted && note) {
            note.innerHTML = '<b class="warn">' + res.hotkeyWanted
              + ' already belongs to another program.</b> Riplox is using '
              + res.hotkeyLabel + ' instead. Pick different keys below, or '
              + 'close whatever owns those.';
          }
        }

        /* Settings names this shortcut too, and it is not always Ctrl+Shift+D:
           if another program owns those keys Riplox takes a different pair, and
           printing the wrong one would send somebody pressing keys that do
           nothing. */
        var browserKeys = $("browserHotkey");
        if (browserKeys) {
          if (res.hotkey === "taken") browserKeys.textContent = "unavailable";
          else if (res.hotkeyLabel) browserKeys.textContent = res.hotkeyLabel;
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

  $("reportIssue").addEventListener("click", function () {
    api("/api/open-url", {
      url: "https://github.com/xniperbuilds/riplox-desktop/issues/new/choose"
    }).then(function (res) {
      if (!res.ok) toast("Could not open the browser.", "bad");
    });
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
