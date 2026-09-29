/* ICE JARVIS — site.js
 *
 * Small, dependency-free helpers shared by every page. Everything here is
 * progressive: with JavaScript off the pages still read correctly, they just
 * keep the version numbers that were baked in at build time, lose the copy
 * buttons, and show every word instead of fading it in.
 *
 * Honesty rules this file follows:
 *   1. Nothing invents a number. Version badges and the download table are read
 *      from the GitHub releases API; if that call fails, the baked-in fallback
 *      stays instead of a guess.
 *   2. Nothing loads a third-party script on its own. The ad loader below is a
 *      no-op unless a publisher id is deliberately configured, so the site can
 *      honestly claim it runs no trackers.
 *   3. Nothing hides content from a reader who has no JavaScript, or who asked
 *      their operating system for reduced motion.
 */
(function () {
  "use strict";

  var REPO = "idkunknown657-cell/ICE-JARVIS";
  var API = "https://api.github.com/repos/" + REPO;
  var html = document.documentElement;
  var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ── theme switch ──────────────────────────────────────────────────
     The choice itself is applied by an inline bootstrap in <head> so there is
     no flash of the wrong theme; this only draws the button and flips it. */
  function themeButton() {
    var links = document.querySelector("nav .links");
    if (!links) return;
    var btn = document.createElement("button");
    btn.className = "theme-btn";
    btn.type = "button";
    var paint = function () {
      var light = html.getAttribute("data-theme") === "light";
      btn.textContent = light ? "\u263E" : "\u2600";      // moon / sun
      btn.setAttribute("aria-label", light ? "Switch to the dark theme" : "Switch to the light theme");
      btn.title = btn.getAttribute("aria-label");
    };
    btn.addEventListener("click", function () {
      var next = html.getAttribute("data-theme") === "light" ? "dark" : "light";
      html.setAttribute("data-theme", next);
      try { localStorage.setItem("ice-theme", next); } catch (e) { /* private mode */ }
      paint();
    });
    var cta = links.querySelector(".nav-cta");
    links.insertBefore(btn, cta || null);
    paint();
  }

  /* ── mark the page you are actually on ───────────────────────────── */
  var here = location.pathname.replace(/\/index\.html$/, "/").split("/").pop() || "index.html";
  if (here === "") here = "index.html";
  document.querySelectorAll("nav .links a").forEach(function (a) {
    var target = a.getAttribute("href") || "";
    // compare only the file part so "./#features" and "install.html" both work
    var file = target.split("#")[0].split("/").pop() || "";
    if (file && file === here && target.indexOf("://") === -1) {
      a.setAttribute("aria-current", "page");
      a.style.color = "var(--text)";
    }
  });

  themeButton();

  /* ── copy-to-clipboard on any .copybox ───────────────────────────── */
  document.querySelectorAll(".copybox").forEach(function (box) {
    var pre = box.querySelector("pre");
    if (!pre || !navigator.clipboard) return;
    var btn = document.createElement("button");
    btn.className = "copy";
    btn.type = "button";
    btn.textContent = "Copy";
    btn.addEventListener("click", function () {
      navigator.clipboard.writeText(pre.innerText.replace(/\s+$/, "")).then(function () {
        btn.textContent = "Copied";
        btn.setAttribute("data-done", "1");
        setTimeout(function () {
          btn.textContent = "Copy";
          btn.removeAttribute("data-done");
        }, 1600);
      }).catch(function () { /* clipboard blocked — the text is still selectable */ });
    });
    box.appendChild(btn);
  });

  /* ── live version badges ─────────────────────────────────────────── */
  function fillRelease(rel) {
    var tag = rel.tag_name || "";
    var version = tag.replace(/^v/, "");
    if (!tag) return;
    document.querySelectorAll("[data-latest-tag]").forEach(function (el) { el.textContent = tag; });
    document.querySelectorAll("[data-latest-version]").forEach(function (el) { el.textContent = version; });
    document.querySelectorAll("[data-latest-name]").forEach(function (el) { el.textContent = rel.name || tag; });
    if (rel.published_at) {
      var when = new Date(rel.published_at);
      if (!isNaN(when)) {
        document.querySelectorAll("[data-latest-date]").forEach(function (el) {
          el.textContent = when.toISOString().slice(0, 10);
          el.setAttribute("datetime", when.toISOString().slice(0, 10));
        });
      }
    }
  }

  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/vnd.github+json" } }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function humanSize(bytes) {
    if (!bytes && bytes !== 0) return "";
    var units = ["B", "KB", "MB", "GB"];
    var i = 0;
    var n = bytes;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n : n.toFixed(1)) + " " + units[i];
  }

  /* ── downloads page: the real asset list ─────────────────────────── */
  var shelf = document.getElementById("releases");
  if (shelf) {
    getJSON(API + "/releases?per_page=10").then(function (list) {
      if (!list.length) return;
      fillRelease(list[0]);
      shelf.innerHTML = "";
      list.forEach(function (rel, index) {
        var head = document.createElement("div");
        head.className = "rel-head";
        var h3 = document.createElement("h3");
        h3.textContent = (rel.tag_name || "release") + (rel.name && rel.name !== rel.tag_name ? " — " + rel.name : "");
        head.appendChild(h3);
        if (index === 0) {
          var live = document.createElement("span");
          live.className = "pill live";
          live.textContent = "latest";
          head.appendChild(live);
        }
        if (rel.published_at) {
          var when = document.createElement("span");
          when.className = "pill";
          when.textContent = rel.published_at.slice(0, 10);
          head.appendChild(when);
        }
        var link = document.createElement("span");
        link.className = "pill";
        link.appendChild(Object.assign(document.createElement("a"), {
          href: rel.html_url, textContent: "release notes"
        }));
        head.appendChild(link);
        shelf.appendChild(head);

        if (!rel.assets || !rel.assets.length) {
          var empty = document.createElement("p");
          empty.className = "smallnote";
          empty.textContent = "This tag has no published files — the build for it did not finish. Nothing to download.";
          shelf.appendChild(empty);
          return;
        }
        rel.assets.forEach(function (asset) {
          var row = document.createElement("div");
          row.className = "asset" + (asset.name === "ICE-Setup.exe" && index === 0 ? " hero-asset" : "");
          var name = document.createElement("span");
          name.className = "name";
          var a = document.createElement("a");
          a.href = asset.browser_download_url;
          a.textContent = asset.name;
          name.appendChild(a);
          row.appendChild(name);
          var meta = document.createElement("span");
          meta.className = "meta";
          meta.textContent = humanSize(asset.size) + " · " + (asset.download_count || 0) + " downloads";
          row.appendChild(meta);
          shelf.appendChild(row);
        });
      });
      var stamp = document.getElementById("releases-stamp");
      if (stamp) stamp.hidden = false;
      revealScan(shelf);
      glowScan(shelf);
    }).catch(function () {
      var fallback = document.getElementById("releases-fallback");
      if (fallback) fallback.hidden = false;
    });
  } else {
    getJSON(API + "/releases/latest").then(fillRelease).catch(function () {
      /* static fallback stays — never show a version we cannot prove */
    });
  }

  /* ══ motion layer ═══════════════════════════════════════════════════
     Reveal-on-scroll, a reading-progress hairline, a condensing navbar and a
     back-to-top button. All of it is skipped when the reader has asked for
     reduced motion, and all of it is additive: with scripting off, nothing
     here has hidden a single word. */

  function revealScan(scope) {
    if (calm) return;
    var targets = (scope || document).querySelectorAll(
      "section > h2, section > .sub, section > .grid > *, section > .steps > *, " +
      "section > .tbl-wrap, section > details.qa, .support .grid > *, .asset, .rel-head");
    targets.forEach(function (el, i) {
      if (el.classList.contains("reveal")) return;
      el.classList.add("reveal");
      el.style.transitionDelay = Math.min(i % 6, 5) * 45 + "ms";
      io.observe(el);
    });
  }

  var io = null;
  if (!calm && "IntersectionObserver" in window) {
    io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-in");
        io.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.05 });
  }

  function glowScan(scope) {
    // a pointer that follows a card's surface — pointless on touch, so skip it
    if (calm || !window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    (scope || document).querySelectorAll(".card:not(.glow)").forEach(function (card) {
      card.classList.add("glow");
      card.addEventListener("pointermove", function (e) {
        var box = card.getBoundingClientRect();
        card.style.setProperty("--mx", (e.clientX - box.left) + "px");
        card.style.setProperty("--my", (e.clientY - box.top) + "px");
      });
    });
  }

  function progressBar() {
    var bar = document.createElement("div");
    bar.id = "progress";
    bar.setAttribute("aria-hidden", "true");
    document.body.appendChild(bar);

    var top = document.createElement("button");
    top.id = "to-top";
    top.type = "button";
    top.textContent = "\u2191";
    top.title = "Back to the top";
    top.setAttribute("aria-label", "Back to the top");
    top.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: calm ? "auto" : "smooth" });
    });
    document.body.appendChild(top);

    var nav = document.querySelector("nav");
    var ticking = false;
    var paint = function () {
      ticking = false;
      var doc = document.documentElement;
      var max = doc.scrollHeight - window.innerHeight;
      var y = window.scrollY || doc.scrollTop || 0;
      if (!calm) bar.style.width = (max > 0 ? Math.min(100, (y / max) * 100) : 0) + "%";
      if (nav) nav.classList.toggle("compact", y > 60);
      top.classList.toggle("show", y > 600);
    };
    window.addEventListener("scroll", function () {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(paint);
    }, { passive: true });
    paint();
  }

  /* stat numbers tick up the first time they are on screen */
  function countUp() {
    var strip = document.querySelector(".stats");
    if (!strip || calm || !("IntersectionObserver" in window)) return;
    var numbers = [].slice.call(strip.querySelectorAll("b")).filter(function (el) {
      // never animate the version badge: the API can rewrite it mid-count
      return !el.hasAttribute("data-latest-version") && /^\d[\d,]*$/.test(el.textContent.trim());
    });
    if (!numbers.length) return;
    var seen = new WeakSet();
    var io2 = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting || seen.has(entry.target)) return;
        seen.add(entry.target);
        io2.unobserve(entry.target);
        var el = entry.target;
        var end = parseInt(el.textContent.replace(/,/g, ""), 10);
        if (!isFinite(end) || end < 2) return;
        strip.classList.add("counting");
        var start = performance.now();
        var step = function (now) {
          var t = Math.min(1, (now - start) / 900);
          var eased = 1 - Math.pow(1 - t, 3);
          el.textContent = Math.round(end * eased).toLocaleString("en-US");
          if (t < 1) window.requestAnimationFrame(step);
          else { el.textContent = end.toLocaleString("en-US"); strip.classList.remove("counting"); }
        };
        el.textContent = "0";
        window.requestAnimationFrame(step);
      });
    }, { threshold: 0.4 });
    numbers.forEach(function (el) { io2.observe(el); });
  }

  /* ══ demo viewport switcher ═════════════════════════════════════════
     The landing page hosts the real interface in an iframe. On a desktop that
     iframe is much wider than the window a phone gives it, so let the reader
     look at it the way it will actually be seen — no second copy of the app,
     just a different width for the same frame. */
  function demoTools() {
    var frame = document.querySelector(".demo-frame");
    var iframe = frame && frame.querySelector("iframe");
    if (!frame || !iframe) return;

    var sizes = [["desktop", "1280px", "Desktop"], ["tablet", "820px", "Tablet"], ["phone", "420px", "Phone"]];
    var bar = document.createElement("div");
    bar.className = "demo-tools";
    sizes.forEach(function (spec) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = spec[2];
      btn.setAttribute("aria-pressed", spec[0] === "desktop" ? "true" : "false");
      btn.addEventListener("click", function () {
        frame.style.maxWidth = spec[1];
        bar.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", "false"); });
        btn.setAttribute("aria-pressed", "true");
      });
      bar.appendChild(btn);
    });

    var reload = document.createElement("button");
    reload.type = "button";
    reload.textContent = "Restart demo";
    reload.addEventListener("click", function () {
      iframe.src = iframe.src;   // reload keeps the frame's own scroll position out of it
    });
    bar.appendChild(reload);

    var open = document.createElement("a");
    open.href = "demo.html";
    open.textContent = "Open full size";
    bar.appendChild(open);

    frame.parentNode.insertBefore(bar, frame);
  }

  /* ══ sponsorship — one line away, never invented ════════════════════
     The project has no donation link yet, and this site will not point at one
     that does not exist (a sponsor button that 404s is worse than no button).
     When an account exists, set window.ICE_SUPPORT in the page's <head> and
     the button appears — the account itself has to be created by a human,
     because it involves a real identity and a real payout method. */
  function support() {
    var cfg = window.ICE_SUPPORT;
    var row = document.querySelector(".support-cta");
    if (!cfg || !row) return;
    var links = [
      [cfg.sponsors, "♥ Sponsor on GitHub", true],
      [cfg.kofi, "♥ Buy me a Ko-fi", true],
      [cfg.bmc, "♥ Buy me a coffee", true],
      [cfg.paypal, "♥ Donate with PayPal", true]
    ];
    var added = 0;
    links.forEach(function (spec) {
      if (!spec[0]) return;
      if (row.querySelector('a[href="' + spec[0] + '"]')) return;
      var a = document.createElement("a");
      a.href = spec[0];
      a.textContent = spec[1];
      a.rel = "noopener";
      row.insertBefore(a, row.firstChild);
      added++;
    });
    if (!added) return;
    // the donation becomes the strongest ask, so the star button stops
    // shouting for attention in the same row
    var first = row.querySelector("a");
    row.querySelectorAll("a").forEach(function (a) {
      if (a !== first) a.classList.remove("primary");
    });
    first.classList.add("primary");
    // the paragraph that explains "donations are not set up yet" stops being
    // true the moment a button exists, so its whole final sentence goes —
    // including the trailing link, which the button now replaces
    var note = row.parentNode.querySelector(".no-ads-note");
    if (note) {
      note.innerHTML = note.innerHTML.replace(
        / Donations are not set up yet[\s\S]*$/,
        " Sponsorship goes directly to the person who wrote the code, and funds " +
        "the same work a paid build would — nothing is unlocked by it, because " +
        "nothing here is behind a paywall.");
    }
  }

  /* ══ ads — off unless deliberately switched on ══════════════════════
     A site that promises "no telemetry, no accounts, no cloud" cannot quietly
     load an ad network. So: nothing here runs at all until window.ICE_ADS
     exists with a publisher id, and when it does the slots are labelled. See
     docs/README.md for what switching this on actually commits you to. */
  function ads() {
    var cfg = window.ICE_ADS;
    var slots = document.querySelectorAll(".ad-slot");
    if (!cfg || !cfg.publisher || !slots.length) return;

    var script = document.createElement("script");
    script.async = true;
    script.src = "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=" + cfg.publisher;
    script.crossOrigin = "anonymous";
    document.head.appendChild(script);

    slots.forEach(function (slot) {
      var ins = slot.querySelector("ins");
      if (!ins) return;
      ins.setAttribute("data-ad-client", cfg.publisher);
      if (cfg.slot) ins.setAttribute("data-ad-slot", cfg.slot);
      slot.classList.add("ready");
      try { (window.adsbygoogle = window.adsbygoogle || []).push({}); } catch (e) { /* blocked */ }
    });
  }

  /* ── boot ────────────────────────────────────────────────────────── */
  function boot() {
    revealScan(document);
    glowScan(document);
    progressBar();
    countUp();
    demoTools();
    support();
    ads();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
