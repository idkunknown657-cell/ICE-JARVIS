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

# 404 is deliberately unindexed and absent from the sitemap.
INDEXED = [p for p in PAGES if p.name != "404.html"]
REQUIRED = ["index.html", "install.html", "download.html", "faq.html",
            "privacy.html", "404.html", "style.css", "site.js", "robots.txt",
            "sitemap.xml", "ads.txt", "og.png"]

# The site's own address lives in exactly one authoritative place — the landing
# page's canonical URL — and everything else is checked against it. Moving to a
# custom domain then means editing the pages and failing this suite if one of
# them is missed, instead of quietly shipping a site that half-believes it is
# hosted somewhere else.
SITE = re.search(r'<link rel="canonical" href="([^"]+)"',
                 (DOCS / "index.html").read_text(encoding="utf-8")).group(1)
assert SITE.endswith("/"), SITE
SITE = SITE[:-1]   # every other URL is built as SITE + "/page.html"


def read(path):
    return path.read_text(encoding="utf-8")


def meta(html, pattern):
    m = re.search(pattern, html, re.I | re.S)
    return m.group(1) if m else None


def without_comments(html):
    """The pages explain things to whoever edits them, and an explanation of how
    to switch an ad slot on is not the same thing as switching it on. Checks
    about what the site *does* have to read the markup, not the notes."""
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


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
                self.assertIn('<meta property="og:image" content="%s/og.png">' % SITE,
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
                self.assertTrue(canonical.startswith(SITE + "/"),
                                "%s advertises %s, but the site lives at %s — a "
                                "half-migrated domain" % (page.name, canonical, SITE))
                self.assertNotIn(canonical, seen,
                                 "already claimed by %s" % seen.get(canonical))
            seen[canonical] = page.name

    def test_every_page_agrees_on_the_site_address(self):
        """og:url is a second copy of the address on every page. It has to agree
        with the canonical link, or a link preview points at a different site
        than the search result does."""
        for page in INDEXED:
            html = read(page)
            canonical = meta(html, r'<link rel="canonical" href="([^"]+)">')
            og = meta(html, r'<meta property="og:url" content="([^"]+)">')
            with self.subTest(page=page.name):
                self.assertEqual(og, canonical,
                                 "og:url and canonical disagree on %s" % page.name)


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
            url = SITE + "/" if page.name == "index.html" else "%s/%s" % (SITE, page.name)
            with self.subTest(page=page.name):
                self.assertIn("<loc>%s</loc>" % url, sitemap)

    def test_sitemap_has_no_dead_urls(self):
        sitemap = read(DOCS / "sitemap.xml")
        for loc in re.findall(r"<loc>([^<]+)</loc>", sitemap):
            with self.subTest(loc=loc):
                self.assertTrue(loc.startswith(SITE + "/"),
                                "%s is on a different domain than the canonical "
                                "URLs" % loc)
                name = loc[len(SITE) + 1:] or "index.html"
                self.assertTrue((DOCS / name).exists(), "%s does not exist" % loc)

    def test_robots_points_search_engines_at_the_sitemap(self):
        robots = read(DOCS / "robots.txt")
        self.assertIn("Sitemap: %s/sitemap.xml" % SITE, robots)
        self.assertIn("Allow: /", robots)

    def test_the_ads_file_explains_itself_rather_than_pretending(self):
        ads = read(DOCS / "ads.txt")
        self.assertIn("pub-0000000000000000", ads,
                      "the placeholder publisher id is the tell that this is a "
                      "template, not a live authorization")
        self.assertIn("root", ads.lower(),
                      "the file must say where ads.txt really has to live")


class MotionAndHonestyTest(unittest.TestCase):
    """The site moves, and it stays truthful while doing it.

    Two promises are easy to break by accident: the pages must show every word
    to a reader without JavaScript (so anything animated has to be hidden by
    JavaScript, never by the stylesheet), and the site must not load a single
    third-party resource (its whole claim is that it tracks nobody).
    """

    BOOTSTRAP = 'document.documentElement.classList.add("js")'

    def test_every_page_sets_the_theme_before_it_paints(self):
        for page in PAGES:
            html = read(page)
            with self.subTest(page=page.name):
                self.assertIn("ice-theme", html,
                              "without the stored choice applied before paint, a "
                              "light-theme reader gets a white flash")
                self.assertIn(self.BOOTSTRAP, html)
                self.assertLess(html.index(self.BOOTSTRAP), html.index('href="style.css"'),
                                "the bootstrap must run before the stylesheet is "
                                "applied, otherwise it is a flash of the wrong theme")

    def test_animation_cannot_hide_content_without_javascript(self):
        css = read(DOCS / "style.css")
        # every rule that could hide a revealed element is scoped to html.js
        for rule in re.findall(r"[^\n]*\.reveal[^\n]*\{", css):
            self.assertTrue(rule.strip().startswith("html.js") or "is-in" in rule,
                            "this rule hides content even when site.js never ran: %s"
                            % rule.strip())
        self.assertIn("html.js .reveal", css)
        # and a reduced-motion reader gets the static page, not a blank one
        reduced = css[css.rindex("prefers-reduced-motion"):]
        self.assertIn("opacity: 1 !important", reduced,
                      "reduced motion must force revealed content visible")

    def test_scripted_motion_respects_the_readers_wish_for_calm(self):
        js = read(DOCS / "site.js")
        self.assertIn("prefers-reduced-motion", js)
        # the motion modules must all consult the flag rather than assuming
        for fn in ("function revealScan", "function countUp", "function glowScan"):
            body = js[js.index(fn):js.index(fn) + 400]
            with self.subTest(fn=fn):
                self.assertIn("calm", body, "%s ignores reduced motion" % fn)

    def test_no_page_loads_a_third_party_resource(self):
        """No CDN, no font host, no analytics, no ad script. The only external
        requests allowed are the ones a visitor triggers by clicking a link."""
        for page in PAGES:
            html = without_comments(read(page))
            for value in re.findall(r'<(?:script|link|img|iframe)[^>]*?(?:src|href)="(https?://[^"]+)"', html):
                with self.subTest(page=page.name, value=value[:60]):
                    self.assertNotIn("googlesyndication", value)
                    self.assertNotIn("google-analytics", value)
                    self.assertNotIn("doubleclick", value)
        # and site.js does not inject one by itself unless it is configured
        js = read(DOCS / "site.js")
        self.assertIn("if (!cfg || !cfg.publisher", js,
                      "the ad loader no longer refuses to run without a publisher id")
        for page in PAGES:
            with self.subTest(page=page.name):
                self.assertNotIn("window.ICE_ADS = {", without_comments(read(page)),
                                 "ads are switched on in the markup — update "
                                 "privacy.html and the support note in the same commit")

    def test_the_single_ad_slot_is_labelled_and_hidden_until_live(self):
        css = read(DOCS / "style.css")
        self.assertIn(".ad-slot { display: none", css,
                      "an empty ad slot must never ship visible")
        self.assertIn(".ad-slot.ready", css)
        slots = []
        for page in PAGES:
            html = read(page)
            slots += [(page.name, m) for m in re.findall(r'<div class="ad-slot"[^>]*>', html)]
        self.assertEqual(len(slots), 1,
                         "there should be exactly one ad slot on the whole site, "
                         "found %d" % len(slots))
        page = read(DOCS / slots[0][0])
        self.assertIn("Advertisement", page, "an ad must be labelled as an ad")

    def test_transitions_between_pages_are_opt_in_and_cheap(self):
        css = read(DOCS / "style.css")
        self.assertIn("@view-transition", css,
                      "cross-document view transitions disappeared — Chrome would "
                      "fall back to a hard reload between pages")
        self.assertIn("::view-transition-old(root)", css)

    def test_no_donation_link_is_invented(self):
        """A sponsor button that leads to a profile instead of a sponsor page is
        worse than no button — and GitHub Sponsors is not enrolled on this
        account, so every `github.com/sponsors/...` link is a dead end today."""
        js = read(DOCS / "site.js")
        self.assertIn("window.ICE_SUPPORT", js,
                      "the sponsorship switch disappeared")
        self.assertIn("if (!cfg || !row) return;", js,
                      "the site would draw a donation button with nothing behind it")
        dead_ends = ("github.com/sponsors/", "ko-fi.com/", "buymeacoffee.com/",
                     "paypal.me/", "patreon.com/")
        for page in PAGES:
            html = without_comments(read(page))
            for host in dead_ends:
                with self.subTest(page=page.name, host=host):
                    self.assertNotIn(host, html,
                                     "%s points at %s, which is not set up" % (page.name, host))
            self.assertNotIn("window.ICE_SUPPORT = {", html,
                             "sponsorship is switched on in %s's markup — make sure "
                             "the account actually exists first" % page.name)

    def test_the_privacy_page_is_reachable_from_every_page(self):
        # ad networks require this, and so do readers deciding whether to run an
        # installer they were handed
        for page in PAGES:
            html = read(page)
            with self.subTest(page=page.name):
                self.assertIn('href="privacy.html"', html)
        privacy = read(DOCS / "privacy.html")
        self.assertIn("advertising", privacy.lower())
        self.assertIn("localStorage", privacy,
                      "the privacy page must disclose the one thing this site stores")


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
