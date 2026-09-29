# The ICE JARVIS website — operator's notes

This folder is the whole website. It is deployed as-is by
[`.github/workflows/pages.yml`](../.github/workflows/pages.yml) with
`actions/upload-pages-artifact` → `actions/deploy-pages`, which means **the
files are served byte-for-byte**: no Jekyll, no build step, no server. Editing a
file here and pushing to `main` is the entire deploy process (about a minute).

| File | What it is |
|---|---|
| `index.html` | Landing page, including the live demo iframe and the one (hidden) ad slot |
| `install.html` | The long install guide — requirements, SmartScreen, first run, updates, uninstall, source |
| `download.html` | Every published file, read live from the GitHub releases API, plus how to verify a download |
| `faq.html` | 22 answers, including the limitations |
| `privacy.html` | Privacy policy for the site *and* the app. Required by ad networks; useful anyway |
| `404.html` | Served by GitHub Pages for any missing path |
| `demo.html` | **Generated** — `python make_bundle.py` writes it, never edit it by hand |
| `style.css`, `site.js` | The whole design and behaviour layer, no dependencies |
| `og.png` | The 1200×630 social card. Regenerate with `python .freebuff/make_og.py` |
| `robots.txt`, `sitemap.xml`, `ads.txt` | Crawler and ad-network plumbing |

`tests/test_website.py` guards all of it (23 tests): broken links, dead in-page
anchors, a navbar that lost a destination, a baked-in version or test count that
drifted from `VERSION`, a stale demo, a demo that started loading something over
the network, and a Pages workflow that stopped publishing `docs/`.

```powershell
python -m unittest tests.test_website          # the site guards only
python make_bundle.py                          # rebuild preview_bundle.html + docs/demo.html
```

---

## Hosting: what you already have

**Live URL:** <https://idkunknown657-cell.github.io/ICE-JARVIS/> — free, permanent,
HTTPS, no card, no expiry. For a project like this it is a perfectly respectable
address, and nothing below is required to keep it working.

## Getting a nicer address for free

Free means **a subdomain of somebody else's domain**, and every option is
"open a pull request and they add a DNS record for you":

| Service | Example | Notes |
|---|---|---|
| [is-a.dev](https://is-a.dev) | `icejarvis.is-a.dev` | Most reliable of these. Add `domains/icejarvis.json` to [is-a-dev/register](https://github.com/is-a-dev/register) as a PR; maintainers create the DNS records pointing at `idkunknown657-cell.github.io` |
| [js.org](https://js.org) | `icejarvis.js.org` | **For JavaScript projects.** This is a Python app with a JS interface, so expect a rejection unless you argue the case convincingly |
| `is-cool.dev`, `is-local.org`, … | — | The same PR-based model, run by the same community |

What you need in the PR is a JSON file with the owner, a record for the
`CNAME` target (`idkunknown657-cell.github.io`), and a description of the
project. Then:

1. Add a file named `CNAME` in this folder containing **just** the domain, e.g.
   `icejarvis.is-a.dev` (no protocol, no trailing slash).
2. Repo → *Settings → Pages → Custom domain* → enter it → **Enforce HTTPS**.
3. Run the URL in `docs/README.md`'s "changing the domain" instructions below.

### The honest catch

A free subdomain is **cosmetic only**. It does not unlock advertising: AdSense
rejects `*.github.io`, `*.is-a.dev` and every other domain you do not own,
because you cannot serve `/ads.txt` from its root and cannot prove you control
it. If the goal is to earn, a free subdomain does not get you there.

## Buying a domain (about $8–15 a year)

This is the cheapest thing that unlocks the rest. `.com` is usually $10–12/year
at Cloudflare Registrar (sold at cost, no markup) or Namecheap; `.dev`/`.app`
run ~$12 and force HTTPS, which is good.

1. Buy it, then in the registrar's DNS add, for `@` (and `www` if you want it):
   `CNAME` → `idkunknown657-cell.github.io`.
   (Apex domains need `ALIAS`/`A` records — Cloudflare handles this with
   "CNAME flattening"; Namecheap wants four `A` records to GitHub's IPs, which
   GitHub publishes in its Pages docs.)
2. Add the `CNAME` file described above, commit, push.
3. Repo → *Settings → Pages → Custom domain* → the domain → wait for the
   certificate → tick **Enforce HTTPS**.
4. Update the site so it advertises the new address (next section).
5. Add the domain to the repo's *About → Website* field (already set to the
   Pages URL; change it there too).

### Changing the domain in the files

The base URL appears in exactly the places SEO needs it. The test suite derives
the expected base from `index.html`'s `<link rel="canonical">`, so if the pages
disagree, the tests fail instead of half the site pointing at the old domain.

```bash
grep -rl "idkunknown657-cell.github.io" docs/ | xargs sed -i 's|https://idkunknown657-cell.github.io/ICE-JARVIS|https://your-domain.com|g'
```

Then fix the two spots that are *not* a plain swap:

- `docs/CNAME` — the bare domain.
- `docs/robots.txt` — the `Sitemap:` line.

And run `python -m unittest tests.test_website`.

---

## Earning something — the truthful version

Three routes exist. Only two of them are real for this project today.

### 1. Advertising (AdSense and friends)

Requirements, all of them hard:

- **A domain you own.** `*.github.io` is rejected on application — you cannot
  serve `/ads.txt` from its root, and the site is not yours. This is the blocker.
- **Content a reviewer will accept.** A landing page plus an FAQ plus an install
  guide is thin but not disqualifying; a page with no original content is.
- **A privacy policy** (done — `privacy.html`) and a cookie/consent notice for
  EU visitors once ads that use cookies are switched on.
- Approval, then a review period, then ads that can be disabled at any time.

The money, honestly: developer-tool traffic runs roughly **$0.50–$3.00 CPM**
(revenue per thousand page views) — often at the low end, because developer
audiences use ad blockers heavily. At 1,000 page views a month that is
**$0.50–$3 a month**. You would need tens of thousands of monthly views for this
to be worth more than the domain costs, and you would be trading away the "no
tracking" promise that is this project's whole personality.

**How to switch it on, if you decide to.** It is one line, and the slot is
already in the markup (`#ad-slot` in `index.html`), hidden by CSS until it is
live:

```html
<!-- in the <head> of the pages you want ads on, after the theme bootstrap -->
<script>window.ICE_ADS = { publisher: "ca-pub-0000000000000000" };</script>
```

`site.js`'s `ads()` then loads Google's script once, marks the slot `.ready`, and
labels it *Advertisement*. Until that line exists, **no third-party script is
loaded on any page** — which is why the site can honestly claim it tracks
nobody. Before you enable it, also:

- put the real line in `docs/ads.txt` (at the domain root — see the file's own
  notes),
- rewrite the **Advertising** section of `privacy.html` to name the network and
  its cookies, as that page promises,
- adjust the "No ads, no analytics" note in `index.html#support`.

### 2. Sponsors and donations (no domain needed)

Fits the project's promise, and works today:

1. **GitHub Sponsors**: <https://github.com/sponsors/accounts> — needs your
   identity, a payout method (Stripe) and tax details; GitHub takes 0% for
   individuals. The profile page `https://github.com/sponsors/idkunknown657-cell`
   currently redirects to the profile, which means it is **not enabled yet**.
2. **Ko-fi** or **Buy Me a Coffee** — faster to set up, no review, takes ~5%.
   Create the page, then add the link to `index.html#support`.
3. PayPal.me as a last resort.

When one exists, replace the "Donations are not set up yet" sentence in
`index.html#support` with a button in `.support-cta`. Until then the section
asks only for the things that actually help a small project: a star, a clear bug
report, and word of mouth.

### 3. Selling something

The licence is **CC BY-NC 4.0** — others may not sell it, but as the copyright
holder you may. That means a paid "pro" build, priority support, or a packaged
macOS/Linux bundle are all legally open to you. It is a product decision rather
than a technical one, and it changes the character of the project. If you want
to explore it, the honest first step is deciding what would be paid that is not
paywalling a fix.

---

## What I would actually do, in order

1. **Buy a cheap domain** (~$10/yr) — not for ads, but because the name is the
   product, and `github.io/ICE-JARVIS` will not survive being written on a
   sticky note.
2. **Enable GitHub Sponsors** — five minutes, no domain, no tracking, and it
   converts a little interest into a little money without changing what the
   site is.
3. **Let the site be found** — the site is already indexed-ready (canonical URLs,
   sitemap, structured data). Real traffic for a tool like this comes from a
   demo people can try without installing: the live demo on the landing page,
   plus a short, honest post where people already are (r/Windows, a Show HN, the
   Python and Hindi-language dev communities).
4. **Only then** consider ads, with a clear-eyed look at the CPM numbers above
   and the cost to the project's story.
