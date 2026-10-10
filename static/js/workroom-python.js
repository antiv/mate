/*
 * Runs the Work Room canvas's Python code with Pyodide, inside the canvas
 * iframe. The code comes from <script type="application/json" id="mateCode">,
 * which the browser never runs: a file rather than an inline script, so the
 * dashboard's Content-Security-Policy, which the iframe inherits, needs no
 * exception for it.
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
