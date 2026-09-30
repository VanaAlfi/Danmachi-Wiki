/* Orario Ledger — progressive enhancements. Every page works without this file. */
(function () {
  "use strict";

  var doc = document.documentElement;
  var INDEX = window.OL_INDEX || [];
  var form = document.querySelector("form.search");
  var ROOT = (form && form.getAttribute("data-root")) || "";
  var finePointer = window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  /* ------------------------------------------------------------ theme */
  function currentTheme() {
    var set = doc.getAttribute("data-theme");
    if (set) return set;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  var themeBtn = document.querySelector(".theme-toggle");
  function labelTheme() {
    if (themeBtn) themeBtn.setAttribute("aria-label", currentTheme() === "dark" ? "Switch to light theme" : "Switch to dark theme");
  }
  if (themeBtn) {
    labelTheme();
    themeBtn.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      doc.setAttribute("data-theme", next);
      try { localStorage.setItem("ol-theme", next); } catch (e) {}
      labelTheme();
    });
  }

  /* -------------------------------------------------------- nav drawer */
  var navBtn = document.querySelector(".nav-toggle");
  var scrim = document.querySelector(".scrim");
  function setNav(open) {
    document.body.classList.toggle("nav-open", open);
    if (navBtn) navBtn.setAttribute("aria-expanded", String(open));
    if (scrim) scrim.hidden = !open;
  }
  if (navBtn) navBtn.addEventListener("click", function () { setNav(!document.body.classList.contains("nav-open")); });
  if (scrim) scrim.addEventListener("click", function () { setNav(false); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { setNav(false); hideSuggest(); hidePop(); }
  });

  /* ------------------------------------------------------------ search */
  function norm(s) {
    return (s || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
  }
  function tokens(s) { return norm(s).split(/[^a-z0-9]+/).filter(Boolean); }
  var prepared = null;
  function prepare() {
    if (prepared) return prepared;
    prepared = INDEX.map(function (d) {
      return { d: d, t: norm(d.t), a: norm((d.a || []).join(" ")), h: norm((d.h || []).join(" ")), s: norm(d.s), x: norm(d.x) };
    });
    return prepared;
  }
  function count(hay, term) {
    var n = 0, i = hay.indexOf(term);
    while (i !== -1 && n < 8) { n++; i = hay.indexOf(term, i + term.length); }
    return n;
  }
  function search(q) {
    var terms = tokens(q);
    if (!terms.length) return [];
    var out = [];
    prepare().forEach(function (p) {
      var score = 0;
      for (var i = 0; i < terms.length; i++) {
        var t = terms[i], s = 0;
        if (p.t.indexOf(t) !== -1) s += p.t.indexOf(t) === 0 || p.t.indexOf(" " + t) !== -1 ? 14 : 9;
        if (p.a.indexOf(t) !== -1) s += 8;
        if (p.h.indexOf(t) !== -1) s += 4;
        if (p.s.indexOf(t) !== -1) s += 3;
        s += count(p.x, t);
        if (!s) return; // every term must match somewhere
        score += s;
      }
      if (p.t === norm(q).trim()) score += 25;
      out.push({ doc: p.d, score: score });
    });
    out.sort(function (a, b) { return b.score - a.score || a.doc.t.localeCompare(b.doc.t); });
    return out;
  }
  function escHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }
  function highlight(text, terms) {
    var safe = escHtml(text);
    terms.forEach(function (t) {
      if (t.length < 2) return;
      safe = safe.replace(new RegExp("(" + t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi"), "<mark>$1</mark>");
    });
    return safe;
  }
  function snippet(d, q, len) {
    var terms = tokens(q);
    var src = d.x || d.s || "";
    var low = norm(src), at = -1;
    for (var i = 0; i < terms.length && at === -1; i++) at = low.indexOf(terms[i]);
    if (at === -1 || (d.s && norm(d.s).indexOf(terms[0]) !== -1)) return highlight(d.s || src.slice(0, len), terms);
    var start = Math.max(0, at - Math.floor(len / 3));
    var piece = (start ? "…" : "") + src.slice(start, start + len).trim() + (start + len < src.length ? "…" : "");
    return highlight(piece, terms);
  }

  var input = form && form.querySelector(".search__input");
  var box = form && form.querySelector(".search__suggest");
  var active = -1;
  function hideSuggest() { if (box) { box.hidden = true; active = -1; if (input) input.setAttribute("aria-expanded", "false"); } }
  function renderSuggest() {
    var q = input.value.trim();
    if (!q) { hideSuggest(); return; }
    var res = search(q).slice(0, 6);
    active = -1;
    if (!res.length) {
      box.innerHTML = '<div class="suggest__empty">No articles match “' + escHtml(q) + "”.</div>";
    } else {
      box.innerHTML = res.map(function (r, i) {
        return '<a class="suggest__item" role="option" id="sg-' + i + '" href="' + ROOT + r.doc.u + '">' +
          '<span class="suggest__title">' + highlight(r.doc.t, tokens(q)) + '</span><span class="suggest__cat">' + escHtml(r.doc.c) + "</span>" +
          '<span class="suggest__snip">' + snippet(r.doc, q, 110) + "</span></a>";
      }).join("") + '<a class="suggest__all" href="' + ROOT + "search.html?q=" + encodeURIComponent(q) + '">See all results for “' + escHtml(q) + "”</a>";
    }
    box.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }
  function moveActive(delta) {
    var items = box.querySelectorAll(".suggest__item");
    if (!items.length) return;
    active = (active + delta + items.length) % items.length;
    items.forEach(function (el, i) { el.setAttribute("aria-selected", String(i === active)); });
    input.setAttribute("aria-activedescendant", "sg-" + active);
  }
  if (input && box) {
    var timer;
    input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(renderSuggest, 60); });
    input.addEventListener("focus", function () { if (input.value.trim()) renderSuggest(); });
    input.addEventListener("keydown", function (e) {
      if (box.hidden) return;
      if (e.key === "ArrowDown") { e.preventDefault(); moveActive(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); moveActive(-1); }
      else if (e.key === "Enter" && active >= 0) {
        e.preventDefault();
        var el = box.querySelectorAll(".suggest__item")[active];
        if (el) window.location.href = el.getAttribute("href");
      }
    });
    document.addEventListener("click", function (e) { if (!form.contains(e.target)) hideSuggest(); });
  }
  document.addEventListener("keydown", function (e) {
    var tag = (e.target && e.target.tagName) || "";
    if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(tag) && input) { e.preventDefault(); input.focus(); }
  });

  // Full results page
  var results = document.getElementById("search-results");
  if (results) {
    var params = new URLSearchParams(window.location.search);
    var q = (params.get("q") || "").trim();
    var pageInput = document.getElementById("search-page-input");
    var status = document.querySelector(".search-page__status");
    if (pageInput) { pageInput.value = q; if (!q) pageInput.focus(); }
    if (input) input.value = q;
    if (q) {
      var found = search(q);
      status.textContent = found.length ? found.length + " result" + (found.length === 1 ? "" : "s") + " for “" + q + "”" : "No results for “" + q + "”. Try a shorter or different term.";
      results.innerHTML = found.map(function (r) {
        return '<li><a class="result__title" href="' + r.doc.u + '">' + highlight(r.doc.t, tokens(q)) + '</a><span class="result__cat">' + escHtml(r.doc.c) +
          '</span><p class="result__snip">' + snippet(r.doc, q, 220) + "</p></li>";
      }).join("");
    } else {
      status.textContent = "Type a name, place, spell or term.";
    }
  }

  /* ------------------------------------------------ popovers & previews */
  var pop = null, popTimer = null;
  function hidePop() { clearTimeout(popTimer); if (pop) { pop.remove(); pop = null; } }
  function placePop(el, target) {
    document.body.appendChild(el);
    var r = target.getBoundingClientRect();
    var w = el.offsetWidth, h = el.offsetHeight;
    var left = Math.min(Math.max(8, r.left + window.scrollX + r.width / 2 - w / 2), window.scrollX + document.documentElement.clientWidth - w - 8);
    var top = r.top + window.scrollY - h - 10;
    if (r.top - h - 10 < 70) top = r.bottom + window.scrollY + 10;
    el.style.left = left + "px";
    el.style.top = top + "px";
  }
  function showCite(a) {
    hidePop();
    var li = document.getElementById("ref-" + a.getAttribute("data-ref"));
    var body = li && li.querySelector(".ref-body");
    if (!body) return;
    pop = document.createElement("div");
    pop.className = "popover";
    pop.setAttribute("role", "tooltip");
    pop.innerHTML = body.innerHTML;
    placePop(pop, a);
  }
  document.querySelectorAll("sup.cite a").forEach(function (a) {
    a.addEventListener("mouseenter", function () { showCite(a); });
    a.addEventListener("focus", function () { showCite(a); });
    a.addEventListener("mouseleave", hidePop);
    a.addEventListener("blur", hidePop);
  });

  if (finePointer && INDEX.length) {
    var byUrl = {};
    INDEX.forEach(function (d) { byUrl[d.u] = d; });
    document.querySelectorAll("a.wikilink[data-preview]").forEach(function (a) {
      a.addEventListener("mouseenter", function () {
        clearTimeout(popTimer);
        popTimer = setTimeout(function () {
          var d = byUrl[a.getAttribute("data-preview")];
          if (!d || !d.s) return;
          hidePop();
          pop = document.createElement("div");
          pop.className = "popover popover--preview";
          pop.innerHTML = '<div class="popover__kicker">' + escHtml(d.c) + '</div><div class="popover__title">' + escHtml(d.t) +
            '</div><p class="popover__text">' + escHtml(d.s) + "</p>";
          placePop(pop, a);
        }, 320);
      });
      a.addEventListener("mouseleave", hidePop);
    });
  }
  window.addEventListener("scroll", function () { if (pop && !finePointer) hidePop(); }, { passive: true });

  /* ------------------------------------------------------ timeline filter */
  var tlForm = document.getElementById("tl-filter");
  if (tlForm) {
    var tlRows = Array.prototype.slice.call(document.querySelectorAll(".tl-table tbody tr"));
    var tlText = document.getElementById("tl-text");
    var tlCount = document.getElementById("tl-count");
    var applyTl = function () {
      var timings = {}, series = {};
      tlForm.querySelectorAll('input[name="timing"]').forEach(function (b) { timings[b.value] = b.checked; });
      tlForm.querySelectorAll('input[name="series"]').forEach(function (b) { series[b.value] = b.checked; });
      var q = norm(tlText.value).trim();
      var shown = 0;
      tlRows.forEach(function (tr) {
        var ser = (tr.getAttribute("data-series") || "").split(" ").filter(Boolean);
        var ok = timings[tr.getAttribute("data-timing")] !== false &&
          (!ser.length || ser.some(function (s) { return series[s] !== false; })) &&
          (!q || norm(tr.getAttribute("data-text")).indexOf(q) !== -1);
        tr.hidden = !ok;
        if (ok) shown++;
      });
      tlCount.textContent = shown + " of " + tlRows.length + " events shown";
    };
    tlForm.addEventListener("change", applyTl);
    tlText.addEventListener("input", applyTl);
  }

  /* ------------------------------------------------- sidebar page tools */
  document.querySelectorAll("a[data-random]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      var pool = INDEX.filter(function (d) { return d.u.indexOf("wiki/") === 0; });
      if (!pool.length) return;
      e.preventDefault();
      window.location.href = a.getAttribute("data-random") + pool[Math.floor(Math.random() * pool.length)].u;
    });
  });
  document.querySelectorAll("a[data-print]").forEach(function (a) {
    a.addEventListener("click", function (e) { e.preventDefault(); window.print(); });
  });
  // On phones the contents box starts collapsed, as on MediaWiki's mobile view.
  if (window.innerWidth > 0 && window.innerWidth <= 760) {
    document.querySelectorAll(".skin-classic details.toc").forEach(function (d) { d.open = false; });
  }
})();
