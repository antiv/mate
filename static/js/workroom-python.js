/*
 * Runs the Work Room canvas's Python code with Pyodide, on the sandboxed canvas
 * page (/dashboard/workroom/canvas). The Work Room reads this file and puts it
 * inline in the document it sends that page, which cannot load files from MATE
 * itself. The code comes from <script type="application/json" id="mateCode">,
 * which the browser never runs.
 */
(async function () {
    'use strict';

    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    var out = document.getElementById('o');
    try {
        var code = JSON.parse(document.getElementById('mateCode').textContent);
        var py = await loadPyodide();
        py.runPython('import sys,io;_o=io.StringIO();_e=io.StringIO();sys.stdout=_o;sys.stderr=_e');
        var err = null;
        try { py.runPython(code); } catch (e) { err = String(e); }
        var so = py.runPython('_o.getvalue()');
        var se = py.runPython('_e.getvalue()');
        out.innerHTML = '';
        if (so) out.innerHTML += '<pre>' + esc(so) + '</pre>';
        if (se) out.innerHTML += '<pre class=err>' + esc(se) + '</pre>';
        if (err) out.innerHTML += '<pre class=err>' + esc(err) + '</pre>';
        if (!so && !se && !err) out.innerHTML = '<span class=ok>&#10003; Ran successfully (no output)</span>';
    } catch (e) {
        out.innerHTML = '<pre class=err>&#10008; ' + esc(String(e)) + '</pre>';
    }
})();
