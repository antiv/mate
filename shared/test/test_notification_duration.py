"""Exercise the dashboard toast timer without starting the server."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


BASE_TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "base.html"


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestNotificationDuration(unittest.TestCase):
    def test_explicit_duration_and_default_are_used_for_toast_removal(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('function showNotification(');
const end = source.indexOf('// Custom confirm dialog', start);
if (start < 0 || end < 0) throw new Error('notification function not found');
const timers = [];
const notification = {
    style: {},
    querySelector: () => ({addEventListener() {}, set textContent(value) {}}),
    remove() {},
};
const document = {
    createElement: () => notification,
    body: {appendChild(element) { element.parentNode = this; }},
};
vm.runInNewContext(source.slice(start, end) + `
showNotification('trigger result', 'success', 8000);
showNotification('ordinary result', 'success');`, {
    document,
    setTimeout: (_, ms) => timers.push(ms),
});
process.stdout.write(JSON.stringify(timers));
"""
        result = subprocess.run(
            ["node", "-e", script, str(BASE_TEMPLATE)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        self.assertEqual(json.loads(result.stdout), [8000, 5000])


if __name__ == "__main__":
    unittest.main()
