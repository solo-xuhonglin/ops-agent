#!/usr/bin/env python3
"""Run the agent full-cycle E2E tests against the remote backend.

The agent task/suggestion flow needs a *controlled* gRPC worker: the real
ops-agent-core worker actually calls an LLM and is not deterministic. So this
runner:

  1. stops the real agent container (registry empties -> dispatch deterministic)
  2. starts a fake worker container (ops-agent-core image + our fake_worker.py,
     attached to the same docker network, so it reaches admin:9090)
  3. waits for the fake worker's RegisterAck (file /tmp/e2e/ready on the host)
  4. runs pytest with AGENT_E2E=1 (tests/test_agent*.py)
  5. ALWAYS restores the real agent + removes the fake worker (try/finally)
  6. prints the fake worker's grants/results logs as evidence

Usage (from a machine that can reach the remote via scripts/_conn.py):
    python scripts/agent_e2e_runner.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from _conn import get_client  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
FAKE_WORKER_SRC = os.path.join(ROOT, "ops-agent-test", "tests", "support", "fake_worker.py")
TEST_DIR = os.path.join(ROOT, "ops-agent-test")
# The test deps (pytest/httpx/dotenv) live in the ops-agent-test venv; allow
# overriding, defaulting to the interpreter that runs this runner.
TEST_PYTHON = os.getenv("OPSAGENT_TEST_PY", sys.executable)
REMOTE_E2E_DIR = "/tmp/e2e"
FAKE_CONTAINER = "e2e-fake-worker"
READY_FILE = f"{REMOTE_E2E_DIR}/ready"
GRANTS_FILE = f"{REMOTE_E2E_DIR}/grants.log"
RESULTS_FILE = f"{REMOTE_E2E_DIR}/results.log"
WORKER_LOG = f"{REMOTE_E2E_DIR}/worker.log"


def sh(c, cmd: str, timeout: int = 60) -> str:
    _, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "ignore").strip()
    err = stderr.read().decode("utf-8", "ignore").strip()
    return out + (("\nERR: " + err) if err else "")


def main() -> int:
    c = get_client()
    try:
        # --- preflight: project sources present on the remote (fake worker needs the stubs) ---
        ls = sh(c, f"ls {REMOTE_E2E_DIR} 2>/dev/null && echo DIR_OK || echo DIR_MISSING")
        print("[preflight] remote e2e dir:", "OK" if "DIR_OK" in ls else "created")
        sh(c, f"mkdir -p {REMOTE_E2E_DIR} && rm -f {READY_FILE} {GRANTS_FILE} {RESULTS_FILE} {WORKER_LOG}")
        net = sh(c, "docker inspect ops-agent-agent --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'")
        print(f"[preflight] agent network: {net.strip()}")
        if not net.strip():
            print("ERROR: cannot resolve agent docker network", file=sys.stderr)
            return 2

        # --- upload fake worker ---
        sftp = c.open_sftp()
        sftp.put(FAKE_WORKER_SRC, f"{REMOTE_E2E_DIR}/fake_worker.py")
        sftp.close()
        print("[setup] uploaded fake_worker.py")

        # --- isolate: stop the real agent (registry empties) ---
        print("[setup] stopping real agent container (ops-agent-agent) ...")
        sh(c, f"docker stop ops-agent-agent", timeout=90)
        time.sleep(6)  # let the gRPC stream tear down + registry unregister

        # --- start fake worker ---
        sh(c, f"docker rm -f {FAKE_CONTAINER} >/dev/null 2>&1 || true")
        run_cmd = (
            f"docker run -d --name {FAKE_CONTAINER} "
            f"--network {net.strip().split()[0]} "
            f"-v /opt/ops-agent/ops-agent-core:/app "
            f"-v {REMOTE_E2E_DIR}:/e2e "
            f"-e ADMIN_GRPC_ADDR=admin:9090 "
            f"ops-agent-core:latest python3 /e2e/fake_worker.py"
        )
        out = sh(c, run_cmd, timeout=120)
        print("[setup] fake worker started:", out[:120])
        if "Error" in out and "CONTAINER_ID" not in out and len(out.split()) != 1:
            # docker run -d prints a container id; anything else is an error
            print(f"ERROR starting fake worker: {out}", file=sys.stderr)
            return 3

        # --- wait for registration (RegisterAck) ---
        ready = False
        for _ in range(30):
            r = sh(c, f"cat {READY_FILE} 2>/dev/null || true")
            if r:
                print(f"[setup] fake worker registered: {r}")
                ready = True
                break
            time.sleep(2)
        if not ready:
            print("ERROR: fake worker never registered (see worker.log):", file=sys.stderr)
            print(sh(c, f"cat {WORKER_LOG} 2>/dev/null || true; docker logs {FAKE_CONTAINER} --tail 20 2>&1"))
            return 4
        time.sleep(2)

        # --- run the agent test suite ---
        print("[run] executing pytest (AGENT_E2E=1) ...")
        env = dict(os.environ)
        env["AGENT_E2E"] = "1"
        pytest_args = [
            TEST_PYTHON,
            "-m", "pytest",
            "tests/test_agent.py", "tests/test_agent_negative.py", "tests/test_agent_worker.py",
            "-v",
        ]
        proc = subprocess.run(pytest_args, cwd=TEST_DIR, env=env)
        code = proc.returncode
        print(f"[run] pytest exit={code}")

        # --- evidence from the fake worker ---
        print("\n[evidence] grants received by fake worker (approve push):")
        print(sh(c, f"cat {GRANTS_FILE} 2>/dev/null || echo '(none)'"))
        print("\n[evidence] dispatched tasks handled by fake worker:")
        print(sh(c, f"cat {RESULTS_FILE} 2>/dev/null || echo '(none)'"))
        print("\n[evidence] worker log (tail):")
        print(sh(c, f"tail -n 20 {WORKER_LOG} 2>/dev/null || echo '(none)'"))
        return code
    finally:
        print("\n[teardown] restoring environment ...")
        try:
            print(sh(c, f"docker rm -f {FAKE_CONTAINER} >/dev/null 2>&1; echo cleaned"))
            print(sh(c, "docker start ops-agent-agent", timeout=90))
            print("[teardown] real agent restored")
        except Exception as e:  # noqa: BLE001
            print(f"[teardown] ERROR restoring agent: {e}", file=sys.stderr)
        c.close()


if __name__ == "__main__":
    sys.exit(main())
