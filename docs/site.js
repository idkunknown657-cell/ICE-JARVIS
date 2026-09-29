/* ICE JARVIS — site.js
 *
 * Small, dependency-free helpers shared by every page. Everything here is
 * progressive: with JavaScript off the pages still read correctly, they just
 * keep the version numbers that were baked in at build time and lose the
 * copy buttons.
 *
 * Honesty rule: nothing on this site invents a number. The version badges and
 * the download table are read live from the GitHub releases API, and if that
 * call fails the page keeps the baked-in fallback instead of guessing.
 */
(function () {
  "use strict";

  var REPO = "idkunknown657-cell/ICE-JARVIS";
  var API = "https://api.github.com/repos/" + REPO;

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
        var version = (rel.tag_name || "").replace(/^v/, "");
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
    }).catch(function () {
      var fallback = document.getElementById("releases-fallback");
      if (fallback) fallback.hidden = false;
    });
  } else {
    getJSON(API + "/releases/latest").then(fillRelease).catch(function () {
      /* static fallback stays — never show a version we cannot prove */
    });
  }
})();
