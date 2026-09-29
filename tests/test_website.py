"""Guards for the static website in docs/ (served by GitHub Pages).

There is no HTML test runner here, and the site is hand-written — which means
the failure modes are silent and embarrassing rather than loud:

* a page gets renamed and every navbar on the site keeps pointing at the old
  name, so a visitor lands on a 404 that nothing in CI noticed;
* a section anchor is renamed and the table of contents scrolls nowhere;
* the "latest release" fallback is baked into the markup (correct: it must
  work with JavaScript off) and then quietly goes stale for three releases
  while the page keeps claiming the old version;
* the test count creeps into a fifth page and starts disagreeing with itself;
* docs/demo.html is generated from ui_web/ by make_bundle.py, and someone
  edits the real UI without regenerating it, so "this is the real interface"
  becomes a lie on the landing page;
* worst of all, the demo starts referencing a remote asset and quietly turns
  a no-network-needed page into a page that phones out.

So these tests assert the invariants rather than the design: every link and
anchor resolves, every version number on the site equals VERSION, the demo is
byte-identical to the bundle and loads nothing over the network, and the
sitemap / robots / canonical URLs agree with each other.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"
BUNDLE = ROOT / "preview_bundle.html"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

# demo.html is generated, not authored: it is the app itself, and its links
# belong to the app. Every other page is ours to keep honest.
PAGES = sorted(p for p in DOCS.glob("*.html") if p.name != "demo.html")
SITE = "https://idkunknown657-cell.github.io/ICE-JARVIS/"

# 404 is deliberately unindexed and absent from the sitemap.
INDEXED = [p for p in PAGES if p.name != "404.html"]
REQUIRED = ["index.html", "install.html", "download.html", "faq.html",
            "404.html", "style.css", "site.js", "robots.txt", "sitemap.xml",
            "og.png"]


def read(path):
    return path.read_text(encoding="utf-8")


def meta(html, pattern):
    m = re.search(pattern, html, re.I | re.S)
    return m.group(1) if m else None


class SiteFilesTest(unittest.TestCase):

    def test_every_page_of_the_site_ships(self):
        missing = [name for name in REQUIRED if not (DOCS / name).exists()]
        self.assertEqual(missing, [], "the website is missing: %s" % missing)

    def test_demo_is_present_when_the_bundle_was_built(self):
        if not BUNDLE.exists():
            self.skipTest("preview_bundle.html not built in this checkout")
        self.assertTrue((DOCS / "demo.html").exists(),
                        "make_bundle.py must write docs/demo.html alongside the "
                        "bundle — the landing page embeds it as the live demo")


class PageMetaTest(unittest.TestCase):
    """Search engines and link previews only see what is in the markup."""

    def test_every_page_carries_the_basics(self):
        for page in PAGES:  # including 404: it still has to look like the site
            html = read(page)
            with self.subTest(page=page.name):
                self.assertIsNotNone(meta(html, r"<title>(.*?)</title>"),
                                     "missing <title>")
                self.assertTrue(meta(html, r'<meta name="description" content="([^"]+)">'),
                                "missing meta description")
                self.assertIn('name="viewport"', html)
                self.assertIn('charset="utf-8"', html.replace("UTF-8", "utf-8"))
                self.assertIn('<html lang="en">', html)
                self.assertIn('href="style.css"', html)
                self.assertIn('src="site.js"', html)

    def test_pages_share_one_social_card(self):
        for page in INDEXED:
            html = read(page)
            with self.subTest(page=page.name):
                self.assertIn('<meta property="og:image" content="%sog.png">' % SITE,
                              html, "Open Graph image must be the real card, "
                                    "served from the site itself")
                self.assertTrue(meta(html, r'<meta property="og:title" content="([^"]+)">'))
        self.assertFalse((DOCS / "og.png").stat().st_size < 10_000,
                         "og.png looks empty — rerun .freebuff/make_og.py")

    def test_canonical_urls_are_absolute_and_unique(self):
        seen = {}
        for page in INDEXED:
            html = read(page)
            canonical = meta(html, r'<link rel="canonical" href="([^"]+)">')
            with self.subTest(page=page.name):
                self.assertIsNotNone(canonical, "missing canonical link")
                self.assertTrue(canonical.startswith(SITE))
                self.assertNotIn(canonical, seen,
                                 "already claimed by %s" % seen.get(canonical))
            seen[canonical] = page.name


class StructureTest(unittest.TestCase):

    def test_every_page_has_exactly_one_h1(self):
        for page in PAGES:
            with self.subTest(page=page.name):
                self.assertEqual(len(re.findall(r"<h1[ >]", read(page))), 1,
                                 "%s should have one <h1> — the heading search "
                                 "engines and screen readers announce" % page.name)

    def test_every_page_links_back_to_the_home_page(self):
        for page in PAGES:
            html = read(page)
            with self.subTest(page=page.name):
                self.assertRegex(html, r'href="(?:\./|index\.html)"')


class LinkTest(unittest.TestCase):

    def test_every_relative_link_resolves_to_a_real_file(self):
        for page in PAGES:
            html = read(page)
            for href in re.findall(r'(?:href|src)="([^"]+)"', html):
                if re.match(r"^(https?:|mailto:|data:|#|//)", href):
                    continue
                target = href.split("#")[0].split("?")[0]
                if not target or target == "./":
                    target = "index.html"
                with self.subTest(page=page.name, href=href):
                    self.assertTrue((DOCS / target).exists(),
                                    "%s links to %s, which is not in docs/" % (page.name, href))

    def test_every_in_page_anchor_points_at_a_real_id(self):
        for page in PAGES:
            html = read(page)
            ids = set(re.findall(r'id="([^"]+)"', html))
            for anchor in re.findall(r'href="#([^"]+)"', html):
                with self.subTest(page=page.name, anchor=anchor):
                    self.assertIn(anchor, ids,
                                  "%s#%s scrolls nowhere" % (page.name, anchor))

    def test_the_navbar_offers_the_same_way_round_on_every_page(self):
        expected = {"install.html", "download.html", "faq.html"}
        for page in PAGES:
            html = read(page)
            nav = html[html.index("<nav>"):html.index("</nav>")]
            files = {h.split("#")[0] for h in re.findall(r'href="([^"]+)"', nav)}
            with self.subTest(page=page.name):
                self.assertTrue(expected.issubset(files),
                                "the navbar on %s is missing %s"
                                % (page.name, sorted(expected - files)))
                self.assertTrue(files & {"index.html", "./"},
                                "the navbar on %s has no way back to the home page" % page.name)
                self.assertIn("nav-cta", nav, "no download button in the navbar")


class NoStaleNumbersTest(unittest.TestCase):
    """The site falls back to baked-in numbers when the GitHub API is
    unreachable. That fallback is allowed to be static — it is not allowed to
    be wrong, because a visitor cannot tell which number they are reading."""

    def test_every_baked_in_version_is_the_real_one(self):
        # the `v?` matters: `\b\d` never matches the `v` in "v1.1.2", and every
        # version on this site is written with one — a guard that skipped them
        # would pass on a page advertising a release that does not exist.
        allowed = {VERSION}
        for page in INDEXED:
            for token in re.findall(r"\bv?(\d+\.\d+\.\d+)\b", read(page)):
                with self.subTest(page=page.name, token=token):
                    self.assertIn(token, allowed,
                                  "%s still advertises %s while VERSION says %s — "
                                  "update the page's static fallback"
                                  % (page.name, token, VERSION))

    def test_the_test_count_agrees_with_itself(self):
        found = {}
        for page in PAGES:
            for count in re.findall(r"\b(\d[\d,]*)\s+tests\b", read(page)):
                found.setdefault(count.replace(",", ""), []).append(page.name)
        self.assertTrue(found, "no page states a test count any more")
        self.assertEqual(len(found), 1,
                         "pages disagree about how many tests exist: %s" % found)

    def test_no_page_hard_codes_a_checksum(self):
        # A hash baked into the markup is a promise the site cannot keep: the
        # moment the release changes it is wrong, and it looks authoritative.
        # Checksums belong in SHA256SUMS.txt, which is published per release.
        for page in PAGES:
            hits = re.findall(r"\b[0-9a-fA-F]{64}\b", read(page))
            with self.subTest(page=page.name):
                self.assertEqual(hits, [],
                                 "a checksum is hard-coded — link SHA256SUMS.txt instead")

    def test_assets_the_pages_point_at_really_exist(self):
        html = read(DOCS / "download.html")
        for name in ("ICE-Setup.exe", "ICE-JARVIS-Windows-x64.zip",
                     "SHA256SUMS.txt", "update.json"):
            self.assertIn("releases/latest/download/%s" % name, html,
                          "%s is not offered with its permanent link" % name)


class SitemapTest(unittest.TestCase):

    def test_sitemap_lists_every_indexable_page(self):
        sitemap = read(DOCS / "sitemap.xml")
        for page in INDEXED:
            url = SITE if page.name == "index.html" else SITE + page.name
            with self.subTest(page=page.name):
                self.assertIn("<loc>%s</loc>" % url, sitemap)

    def test_sitemap_has_no_dead_urls(self):
        sitemap = read(DOCS / "sitemap.xml")
        for loc in re.findall(r"<loc>([^<]+)</loc>", sitemap):
            name = loc[len(SITE):] or "index.html"
            with self.subTest(loc=loc):
                self.assertTrue((DOCS / name).exists(), "%s does not exist" % loc)

    def test_robots_points_search_engines_at_the_sitemap(self):
        robots = read(DOCS / "robots.txt")
        self.assertIn("Sitemap: %ssitemap.xml" % SITE, robots)
        self.assertIn("Allow: /", robots)


class DeployWorkflowTest(unittest.TestCase):
    """The site only exists if Pages keeps being handed the right folder."""

    def setUp(self):
        self.path = ROOT / ".github" / "workflows" / "pages.yml"
        if not self.path.exists():
            self.skipTest("no pages workflow in this checkout")
        self.yml = read(self.path)

    def test_pages_publishes_the_docs_folder(self):
        self.assertIn("path: docs", self.yml,
                      "the workflow no longer uploads docs/ — the site would "
                      "deploy empty, or publish the repository root")
        self.assertIn("actions/deploy-pages", self.yml)
        self.assertIn("pages: write", self.yml)
        self.assertIn("id-token: write", self.yml)

    def test_pages_redeploys_when_the_site_changes(self):
        for trigger in ("docs/**", "ui_web/**", "make_bundle.py"):
            with self.subTest(trigger=trigger):
                self.assertIn(trigger, self.yml,
                              "%s no longer redeploys the site, so the live "
                              "pages can silently go stale" % trigger)


class DemoIsGeneratedTest(unittest.TestCase):

    def test_demo_is_the_bundle_with_the_embed_flag(self):
        if not BUNDLE.exists():
            self.skipTest("preview_bundle.html not built in this checkout")
        demo = read(DOCS / "demo.html")
        flag = '<script>document.documentElement.classList.add("embedded-demo");</script>'
        self.assertIn(flag, demo,
                      "the site's demo must announce that it is embedded — the "
                      "bundle's CSS keys off that class to hide the copy of the "
                      "avatar sitting behind the landing page")
        self.assertEqual(demo.replace(flag + "\n", ""), read(BUNDLE),
                         "docs/demo.html is stale — rerun make_bundle.py")

    def test_the_demo_runs_on_the_simulated_backend(self):
        """The demo is hosted on a phone-friendly page with no server behind
        it, so it must be driven by the app's own simulated backend — the demo
        would otherwise sit there asking for a microphone it cannot use."""
        if not BUNDLE.exists():
            self.skipTest("preview_bundle.html not built in this checkout")
        mock = read(ROOT / "ui_web" / "js" / "mock.js")
        app = read(ROOT / "ui_web" / "js" / "app.js")
        demo = read(DOCS / "demo.html")

        self.assertIn("window.__installMock = function", mock,
                      "the simulated backend changed shape — this test needs "
                      "a new fingerprint")
        self.assertIn("window.__installMock = function", demo,
                      "the demo no longer inlines the simulated backend")
        self.assertIn("__installMock", app,
                      "app.js must fall back to the simulated backend when the "
                      "real bridge never arrives")
        self.assertNotIn('src="js/mock.js"', demo,
                         "the mock backend is linked instead of inlined — it "
                         "will 404 on the static host")

    def test_demo_loads_nothing_from_the_network(self):
        """The demo is the real UI, so it must stay a single self-contained
        file: no stylesheet, no script, no font, no image fetched from a host.
        A stray ``src="https://…"`` would turn a private page into a tracker."""
        if not BUNDLE.exists():
            self.skipTest("preview_bundle.html not built in this checkout")
        demo = read(DOCS / "demo.html")
        for attr in ("src", "href"):
            for value in re.findall(r'%s="([^"]+)"' % attr, demo):
                with self.subTest(attr=attr, value=value[:80]):
                    self.assertFalse(
                        re.match(r"^(https?:)?//", value),
                        "the demo now loads %s over the network" % value)
                    self.assertFalse(value.startswith("data:text/html"))
        self.assertNotIn("@import", demo)
        self.assertIn("<style>", demo, "the CSS was not inlined into the demo")

    def test_landing_page_embeds_the_demo_it_claims_to_embed(self):
        html = read(DOCS / "index.html")
        self.assertIn('<iframe src="demo.html"', html)
        self.assertIn("real interface", html)


if __name__ == "__main__":
    unittest.main()
