"""
dashboard/test_render.py — Sep 22 2026

Executes EVERY dashboard renderer against the live /api/data payload in a real
JavaScript engine (JavaScriptCore, via osascript). Run after ANY change to
dashboard/static/app.js:

    venv/bin/python dashboard/test_render.py

WHY THIS EXISTS. Removing a dead renderer by splicing between two markers also
deleted renderFieldReport(), which sat between them. app.js still parsed — the
delimiters all balanced, and a syntax check passed — but renderSystemHealth()
threw ReferenceError at runtime, and because renderAll() calls the renderers in
sequence, SYSTEM HEALTH *and* every card after it (RECENT ACTIVITY) rendered
blank. Counting braces cannot catch a missing function. Running it can.
"""
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'dashboard'))
sys.path.insert(0, os.path.join(BASE, 'options'))

HARNESS = r'''
ObjC.import('Foundation');
function readf(p){ return ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(
    p, $.NSUTF8StringEncoding, null)); }
var src = readf(APP_JS);
var d   = JSON.parse(readf(PAYLOAD));
var EL = { set innerHTML(v){}, get innerHTML(){return '';},
           set textContent(v){}, get textContent(){return '';},
           set className(v){}, set title(v){},
           setAttribute:function(){}, getContext:function(){return {};},
           classList:{add:function(){},remove:function(){},toggle:function(){}},
           querySelectorAll:function(){return [];}, addEventListener:function(){}, style:{} };
var sandbox = "var document={getElementById:function(){return EL;},"
  + "querySelectorAll:function(){return [];},addEventListener:function(){},"
  + "createElement:function(){return EL;}};"
  + "var window={addEventListener:function(){}};"
  + "var Chart=function(){this.data={labels:[],datasets:[{},{},{},{}]};"
  + "this.update=function(){};};"
  + src + "; return {renderAll:renderAll};";
try {
  new Function('EL', sandbox)(EL).renderAll(d);
  'OK';
} catch (e) {
  'FAIL: ' + e.name + ': ' + e.message;
}
'''


def main():
    import app
    with app.app.test_request_context('/api/data'):
        payload = json.loads(app.api_data().get_data(as_text=True))
    print(f"  live payload: {len(payload)} keys")

    tmp = os.path.join(os.getenv('TMPDIR', '/tmp'), '_dash_payload.json')
    with open(tmp, 'w') as fh:
        json.dump(payload, fh, default=str)

    js = os.path.join(BASE, 'dashboard', 'static', 'app.js')
    script = (f'var APP_JS = {json.dumps(js)};\n'
              f'var PAYLOAD = {json.dumps(tmp)};\n' + HARNESS)
    sh = os.path.join(os.getenv('TMPDIR', '/tmp'), '_dash_render_test.js')
    with open(sh, 'w') as fh:
        fh.write(script)

    try:
        out = subprocess.run(['osascript', '-l', 'JavaScript', sh],
                             capture_output=True, text=True, timeout=90)
    except FileNotFoundError:
        print("  SKIP — osascript unavailable (not macOS)")
        return 0
    finally:
        for f in (tmp, sh):
            try: os.unlink(f)
            except OSError: pass

    res = (out.stdout or out.stderr).strip()
    if res == 'OK':
        print("  PASS  every renderer executed against the live payload")
        return 0
    print(f"  FAIL  {res}")
    return 1


if __name__ == '__main__':
    sys.exit(main())
