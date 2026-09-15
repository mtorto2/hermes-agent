"""Offline TLS regression: real Node/npm through the sandbox's real proxy.

Run with PATH containing Node, npm and OpenSSL 3:
    python3 -m unittest discover -s tests/scripts -p test_dev_sandbox_tls.py -v

Only loopback sockets and temporary certificates/homes are used. No installer,
namespace setup, host trust changes, or public registry access is needed.
"""

import os
from pathlib import Path
import re
import runpy
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


ASSETS = Path(__file__).resolve().parents[2] / "scripts" / "sandbox"
HOST = "registry.sandbox.test"
NODE_REQUEST = r"""
const http = require('node:http');
const tls = require('node:tls');
const req = http.request({host: '127.0.0.1', port: Number(process.argv[1]),
  method: 'CONNECT', path: 'registry.sandbox.test:443'});
req.on('error', fail);
req.on('connect', (res, socket) => {
  const secure = tls.connect({socket, servername: 'registry.sandbox.test'}, () => {
    secure.write('GET /-/ping HTTP/1.1\r\nHost: registry.sandbox.test\r\nConnection: close\r\n\r\n');
  });
  secure.on('data', chunk => process.stdout.write(chunk));
  secure.on('error', fail);
});
function fail(error) { console.error(error.code); process.exitCode = 1; }
req.end();
"""


@unittest.skipUnless(
    all(shutil.which(tool) for tool in ("node", "npm", "openssl")),
    "requires node, npm and OpenSSL 3 on PATH",
)
class SandboxNodeTrustTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-sandbox-tls-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.certs = self.root / "certs"
        self.certs.mkdir()
        # Deliberately unrelated CAs: only ca.pem signs the MITM leaf.
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "OPENSSL_CONF": str(ASSETS / "openssl.cnf"),
            # Match official Node's bundled trust on builds (e.g. Homebrew)
            # that otherwise consult SSL_CERT_FILE and mask this regression.
            "NODE_OPTIONS": "--use-bundled-ca",
        }
        for name in ("ca", "real-ca"):
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-days", "2", "-subj", f"/CN=Test {name}",
                 "-extensions", "sandbox_ca_ext", "-keyout",
                 str(self.certs / f"{name}.key"), "-out",
                 str(self.certs / f"{name}.pem")],
                env=self.env, check=True, capture_output=True, timeout=15,
            )
        fixture = self.root / "http" / HOST / "-" / "ping"
        fixture.parent.mkdir(parents=True)
        fixture.write_text('{"sandbox":"fixture"}')
        with patch.object(sys, "argv", [str(ASSETS / "proxy.py"),
                                       str(self.root / "http"), str(self.certs),
                                       str(self.certs / "real-ca.pem")]):
            self.proxy = runpy.run_path(str(ASSETS / "proxy.py"))
        # Fail closed if npm ever asks for something outside our fixtures.
        # Only the upstream boundary is stubbed; CONNECT, certificate minting,
        # TLS verification and fixture responses all use the real proxy.
        def no_upstream(*args):
            raise AssertionError("unexpected upstream request in offline TLS test")

        proxy_globals = self.proxy["handle_request"].__globals__
        proxy_globals["forward_http"] = no_upstream
        proxy_globals["forward_https"] = no_upstream
        # The real proxy mints its real per-host leaf. Supply its OpenSSL config
        # without changing trust/config anywhere on the host.
        with patch.dict(os.environ, self.env, clear=True):
            self.proxy["cert_for"](HOST)
        self.errors = []
        proxy = self.proxy
        errors = self.errors

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(10)
                try:
                    proxy["handle_request"](self.request)
                except Exception as error:
                    errors.append(repr(error))

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def client_env(self, ca=None):
        # Read the payload's actual bwrap --setenv assignments, translating only
        # its mount prefix to this fixture. Never execute stage2-run.sh on host.
        env = self.env.copy()
        source = (ASSETS / "stage2-run.sh").read_text()
        for key in ("NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE"):
            match = re.search(r"--setenv " + key + r" (\S+)", source)
            if match is None:
                self.fail(f"missing stage2 --setenv {key}")
            env[key] = match.group(1).replace("/work/certs/", str(self.certs) + "/")
        if ca is not None:
            env["NODE_EXTRA_CA_CERTS"] = str(self.certs / ca)
        proxy = f"http://127.0.0.1:{self.port}"
        env.update(HTTPS_PROXY=proxy, HTTP_PROXY=proxy, ALL_PROXY=proxy, NO_PROXY="")
        return env

    def node(self, ca=None):
        return subprocess.run(
            ["node", "-e", NODE_REQUEST, str(self.port)], env=self.client_env(ca),
            capture_output=True, text=True, timeout=15,
        )

    def test_node_trusts_intercepting_ca_from_stage2(self):
        result = self.node()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"sandbox":"fixture"', result.stdout)
        self.assertEqual(self.errors, [])

    def test_unrelated_real_ca_does_not_trust_intercepting_proxy(self):
        result = self.node("real-ca.pem")
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr, "UNABLE_TO_VERIFY_LEAF_SIGNATURE|UNABLE_TO_GET_ISSUER_CERT_LOCALLY|SELF_SIGNED_CERT_IN_CHAIN")

    def test_npm_uses_stage2_trust_for_https_proxy(self):
        result = subprocess.run(
            ["npm", "ping", "--registry", f"https://{HOST}",
             "--fetch-retries=0", "--fetch-timeout=5000", "--strict-ssl=true",
             "--update-notifier=false",
             "--userconfig", str(self.root / "empty-npmrc"),
             "--globalconfig", str(self.root / "empty-global-npmrc"),
             "--cache", str(self.root / "npm-cache")],
            cwd=self.root, env=self.client_env(), capture_output=True,
            text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PONG", result.stderr)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
