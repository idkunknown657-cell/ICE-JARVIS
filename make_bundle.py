from pathlib import Path
root = Path('ui_web')
html = (root / 'index.html').read_text(encoding='utf-8')
css = (root / 'css' / 'style.css').read_text(encoding='utf-8')
html = html.replace('<link rel="stylesheet" href="css/style.css">', '<style>\n' + css + '\n</style>')
for name in ['vendor/three.min.js', 'js/markdown.js', 'js/avatar.js', 'js/mock.js', 'js/avatar3d.js', 'js/app.js']:
    js = (root / name).read_text(encoding='utf-8')
    tag = '<script src="{}"></script>'.format(name)
    if tag in html:
        html = html.replace(tag, '<script>\n' + js + '\n</script>')
    else:
        html = html.replace('</body>', '<script>\n' + js + '\n</script>\n</body>')
Path('preview_bundle.html').write_text(html, encoding='utf-8')
print('bundle:', len(html), 'bytes')
