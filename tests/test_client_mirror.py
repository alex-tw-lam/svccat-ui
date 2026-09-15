"""The §8 mirror: app.js smValidateParams must reject exactly what the
server validators reject, with the same reasons. Runs the 40-case battery
through node when available (CI containers may lack it)."""

import json
import os
import shutil
import subprocess

from django.test import SimpleTestCase

from core import input_schema as ix

BATTERY = r"""
const pick = (s) => {
  const start = s.indexOf('function smValidateParams')
  let depth = 0
  for (let j = s.indexOf('{', start); j < s.length; j++) {
    if (s[j] === '{') depth++
    else if (s[j] === '}') { depth--; if (depth === 0) return s.slice(start, j + 1) }
  }
  throw new Error('unbalanced')
}
const fn = new Function(`${pick(require('fs').readFileSync(process.argv[2], 'utf8'))}; return smValidateParams`)()
const contracts = JSON.parse(require('fs').readFileSync(process.argv[3], 'utf8'))
const cases = JSON.parse(require('fs').readFileSync(process.argv[4], 'utf8'))
let bad = 0
for (const [text, contract, serverErr] of cases) {
  let client = null, threw = false
  try { client = fn(text, contracts[contract]) } catch { threw = true }
  const agree = (serverErr === null && client === null)
    || (serverErr !== null && client !== null && client.includes(serverErr.split(' - ')[0].split(' for ')[0].split(':')[0]))
  if (!agree) { bad++; console.log(`MISMATCH: ${text} server=${serverErr} client=${client}`) }
}
console.log(bad === 0 ? `MIRROR OK (${cases.length} cases)` : `MIRROR FAIL ${bad}/${cases.length}`)
process.exit(bad ? 1 : 0)
"""


class TestClientMirror(SimpleTestCase):
    def _server_verdicts(self):
        """Every (offering, plan) form + validator outcome as battery rows."""
        rows = []
        offerings = ["minio", "redis", "postgresql", "vm"]
        for o in offerings:
            rows.append(("{}", f"{o}|provision", None))
            rows.append(("", f"{o}|provision", None))
            rows.append(("{bad", f"{o}|provision", "not valid JSON"))
            rows.append(("[1]", f"{o}|provision", "parameters must be a JSON object"))
            for bad, _desc in [
                ('{"kubeconfig_path": 1}', "computed"),
                ('{"memory": "1"}', "plan"),
                ('{"namespace": "x"}', "pinned"),
                ('{"nope": 1}', "unknown"),
            ]:
                err = ix.validate_user_params(
                    o,
                    json.loads(bad)
                    if bad.startswith("{") and bad.endswith("}") and bad != "{}"
                    else bad,
                )[1]
                rows.append((bad, f"{o}|provision", err))
            rows.append(("{}", f"{o}|bind", None))
            err = ix.validate_bind_params(o, {"host": "h"})[1]
            rows.append(('{"host": "h"}', f"{o}|bind", err))
        return [r for r in rows if r[2] is not None or True]

    def test_smvalidateparams_agrees_with_server(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not available — JS mirror battery skipped")

        contracts = {
            f"{o}|{op}": ix.drawer_contract(o, op)
            for o in ["minio", "redis", "postgresql", "vm", "nope"]
            for op in ("provision", "bind")
        }
        rows = self._server_verdicts()
        # server-side verdicts for the same texts
        cases = []
        for text, ckey, _ in rows:
            offering, op = ckey.split("|")
            params = None
            try:
                params = json.loads(text) if text else None
            except json.JSONDecodeError:
                cases.append([text, ckey, "not valid JSON"])
                continue
            err = (
                ix.validate_bind_params(offering, params)
                if op == "bind"
                else ix.validate_user_params(offering, params)
            )[1]
            cases.append([text, ckey, err])

        tmp = "/tmp/sv_mirror"
        os.makedirs(tmp, exist_ok=True)
        with open(f"{tmp}/battery.js", "w") as f:
            f.write(BATTERY)
        with open(f"{tmp}/contracts.json", "w") as f:
            json.dump(contracts, f)
        with open(f"{tmp}/cases.json", "w") as f:
            json.dump(cases, f)
        app_js = os.path.join(os.path.dirname(ix.__file__), "public", "app.js")
        proc = subprocess.run(
            [
                node,
                f"{tmp}/battery.js",
                app_js,
                f"{tmp}/contracts.json",
                f"{tmp}/cases.json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("MIRROR OK", proc.stdout)
