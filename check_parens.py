"""Find the unbalanced paren in app.js (skips strings, template literals, comments)."""
src = open('ui_web/js/app.js', encoding='utf-8').read()
i, line, depth = 0, 1, 0
stack = []
deepest = (0, None)
in_str = None
in_com = None
esc = False
SQ = chr(39)
DQ = chr(34)
BT = chr(96)
BS = chr(92)
LP = chr(40)
RP = chr(41)
NL = chr(10)

while i < len(src):
    ch = src[i]
    if ch == NL:
        line += 1
        if in_com == '//':
            in_com = None
    elif in_com:
        if in_com == '/*' and ch == '*' and i + 1 < len(src) and src[i + 1] == '/':
            in_com = None
            i += 1
    elif in_str:
        if esc:
            esc = False
        elif ch == BS:
            esc = True
        elif ch == in_str:
            in_str = None
    else:
        if ch in (SQ, DQ, BT):
            in_str = ch
        elif ch == '/' and i + 1 < len(src) and src[i + 1] == '/':
            in_com = '//'
        elif ch == '/' and i + 1 < len(src) and src[i + 1] == '*':
            in_com = '/*'
        elif ch == LP:
            stack.append((line, i))
            depth += 1
            if depth > deepest[0]:
                deepest = (depth, line)
        elif ch == RP:
            depth -= 1
            if stack:
                stack.pop()
            if depth < 0:
                print('NEGATIVE depth at line', line)
                break
    i += 1

print('final depth:', depth)
print('deepest:', deepest)
print('unclosed opens at (line, col):', stack[:10])
for ln, col in stack[:10]:
    lines = src.split(NL)
    print(ln, ':', lines[ln - 1].strip()[:110])
