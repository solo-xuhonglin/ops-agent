#!/usr/bin/env python3
"""deploy-remote.py — local build, remote docker build & deploy (no GitHub needed).

Uses paramiko for SSH/SFTP (this sandbox has no sshpass). Reads connection info from
deploy-remote.env (same dir) or environment variables.

Flow:
  1. (optional, --front) build frontend dist locally
  2. SFTP the prebuilt backend jar (+ admin Dockerfile) and/or frontend dist to remote
  3. remote: docker compose build <svc> && docker compose up -d <svc>

Backend-only by default (the dataset 404 + MinIO-purge fixes are backend only);
pass --front to also redeploy the frontend.

Usage:
  python deploy-remote.py            # deploy backend (admin) only
  python deploy-remote.py --front    # deploy backend + frontend
"""
import os
import sys
import time

import paramiko

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    # No host/credential defaults here on purpose: they come from
    # deploy-remote.env (gitignored) or environment variables.
    cfg = {
        "REMOTE_HOST": "",
        "REMOTE_USER": "root",
        "REMOTE_PORT": "22",
        "REMOTE_DIR": "/opt/ops-agent",
        "SSHPASS": "",
    }
    env_file = os.path.join(HERE, "deploy-remote.env")
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    # env vars override the file
    for k in cfg:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def connect(cfg):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=cfg["REMOTE_HOST"],
        port=int(cfg["REMOTE_PORT"]),
        username=cfg["REMOTE_USER"],
        password=cfg["SSHPASS"],
        timeout=30,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def sftp_put(client, local_path, remote_path):
    sftp = client.open_sftp()
    try:
        # ensure remote parent dirs exist
        parent = os.path.dirname(remote_path)
        cur = ""
        for part in parent.strip("/").split("/"):
            cur = cur + "/" + part
            try:
                sftp.stat(cur)
            except IOError:
                try:
                    sftp.mkdir(cur)
                except IOError:
                    pass
        print(f"    put {os.path.basename(local_path)} -> {remote_path}")
        sftp.put(local_path, remote_path, callback=lambda sent, total: None)
    finally:
        sftp.close()


def remote_exec(client, cmd):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=600)
    for line in stdout:
        print("   ", line.rstrip("\n"))
    err = stderr.read().decode("utf-8", "replace")
    if err.strip():
        print("    [stderr]", err.strip()[:2000])
    exit_status = stdout.channel.recv_exit_status()
    return exit_status


def main():
    do_front = "--front" in sys.argv
    cfg = load_config()
    for required in ("REMOTE_HOST", "SSHPASS"):
        if not cfg[required]:
            print(
                f"ERROR: {required} not set. Put it in deploy-remote.env "
                f"(copy from deploy-remote.env.example) or export it as an env var.",
                file=sys.stderr,
            )
            sys.exit(1)

    local_admin = os.path.join(HERE, "ops-agent-admin")
    jar = os.path.join(local_admin, "target", "ops-agent-admin-0.0.1-SNAPSHOT.jar")
    if not os.path.exists(jar):
        print(f"ERROR: backend jar not found: {jar}\n  run: mvn -B clean package -DskipTests", file=sys.stderr)
        sys.exit(1)

    print(f"==> target: {cfg['REMOTE_USER']}@{cfg['REMOTE_HOST']}:{cfg['REMOTE_PORT']} {cfg['REMOTE_DIR']}")

    client = connect(cfg)
    try:
        # ---- backend ----
        print("==> [backend] uploading jar + Dockerfile")
        remote_admin = cfg["REMOTE_DIR"] + "/ops-agent-admin"
        sftp_put(client, jar, remote_admin + "/target/" + os.path.basename(jar))
        sftp_put(client, os.path.join(local_admin, "Dockerfile"), remote_admin + "/Dockerfile")

        if do_front:
            print("==> [frontend] building dist locally")
            front = os.path.join(HERE, "ops-agent-front")
            os.system(f'cd "{front}" && npm install && npm run build')
            dist = os.path.join(front, "dist")
            if not os.path.isdir(dist) or not os.listdir(dist):
                print("ERROR: frontend dist/ missing or empty", file=sys.stderr)
                sys.exit(1)
            print("==> [frontend] uploading dist + Dockerfile + nginx.conf")
            remote_front = cfg["REMOTE_DIR"] + "/ops-agent-front"
            for root, _dirs, files in os.walk(dist):
                for fn in files:
                    lp = os.path.join(root, fn)
                    rp = remote_front + "/dist/" + os.path.relpath(lp, dist).replace("\\", "/")
                    sftp_put(client, lp, rp)
            sftp_put(client, os.path.join(front, "Dockerfile"), remote_front + "/Dockerfile")
            if os.path.exists(os.path.join(front, "nginx.conf")):
                sftp_put(client, os.path.join(front, "nginx.conf"), remote_front + "/nginx.conf")

        # ---- remote docker build & deploy ----
        services = "admin front" if do_front else "admin"
        print(f"==> remote docker compose build & up -d ({services})")
        cmd = (
            f"cd '{cfg['REMOTE_DIR']}' && "
            f"docker compose --env-file .env build {services} && "
            f"docker compose --env-file .env up -d {services} && "
            f"sleep 6 && docker compose ps"
        )
        rc = remote_exec(client, cmd)
        if rc != 0:
            print(f"ERROR: remote deploy command exited {rc}", file=sys.stderr)
            sys.exit(rc)
    finally:
        client.close()

    print("==> deploy complete")
    print(f"    backend API: http://{cfg['REMOTE_HOST']}:8080/api")
    print(f"    frontend:    http://{cfg['REMOTE_HOST']}/")


if __name__ == "__main__":
    main()
