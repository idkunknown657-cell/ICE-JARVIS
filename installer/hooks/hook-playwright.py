"""Bundle Playwright's driver, which is data pretending to be a dependency.

PyInstaller's analysis sees `import playwright` and stops there. Without this hook
the packaged app imports fine and then fails the moment it tries to launch a
browser, because the thing that actually drives one is a node runtime sitting in
`playwright/driver/` — around 130 MB that nothing ever imports. The browsers
themselves are deliberately *not* here: they are the optional 450 MB download in
`core/browser_deps.py`.

`get_package_paths` returns `(package_base, package_dir)` — the *parent* folder
first. Taking the first element looks harmless and silently bundles nothing, which
is exactly what the frozen build's `--selftest` caught.
"""
from pathlib import Path

from PyInstaller.utils.hooks import get_package_paths

datas = []

try:
    _, package_dir = get_package_paths("playwright")
    driver = Path(package_dir) / "driver"
    if (driver / "package" / "cli.js").exists():
        datas.append((str(driver), "playwright/driver"))
except Exception:
    # A missing driver is not fatal to the build: --install-browser-deps and the
    # browser features report it plainly at runtime.
    pass
