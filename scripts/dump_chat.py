"""SSH to remote, dump DB rows for a specific conversation.

Usage: `python scripts/dump_chat.py <conversation_id>`
Falls back to listing recent conversations if no id given.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _conn import REMOTE_DIR, get_client


def run_remote(cmd: str) -> str:
    client = get_client()
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
        out = stdout.read().decode("utf-8", "ignore")
        err = stderr.read().decode("utf-8", "ignore").strip()
        if err:
            print(f"--- STDERR ---\n{err}")
        return out
    finally:
        client.close()


def find_conversation(query: str) -> str:
    """模糊匹配 title 或 conversation_id，返回 conversation_id。"""
    sql = (
        "SELECT conversation_id, title, user_id, updated_at "
        "FROM agent_conversations "
        "WHERE title ILIKE '%%{q}%%' OR conversation_id ILIKE '%%{q}%%' "
        "ORDER BY updated_at DESC LIMIT 5"
    ).format(q=query.replace("'", "''"))
    out = run_remote(
        f"docker exec ops-agent-postgres psql -U opsagent -d opsagent "
        f"-A -F '|' -t -c \"{sql}\""
    )
    return out.strip()


def dump_messages(conv_id: str) -> str:
    sql = (
        "SELECT id, kind, LEFT(content, 800) as content "
        "FROM agent_conversation_messages "
        "WHERE conversation_id='{c}' "
        "AND (kind='ASSISTANT' OR kind='APPROVAL') "
        "ORDER BY id ASC"
    ).format(c=conv_id.replace("'", "''"))
    return run_remote(
        f"docker exec ops-agent-postgres psql -U opsagent -d opsagent "
        f"-A -F '|' -t -c \"{sql}\""
    )


def dump_tasks(conv_id: str) -> str:
    sql = (
        "SELECT task_id, task_type, status, suggestion_id, "
        "       LEFT(query, 80) as query, LEFT(conclusion, 80) as conclusion, "
        "       started_at, finished_at, created_at, updated_at "
        "FROM agent_tasks "
        "WHERE conversation_id='{c}' "
        "ORDER BY started_at ASC NULLS LAST"
    ).format(c=conv_id.replace("'", "''"))
    return run_remote(
        f"docker exec ops-agent-postgres psql -U opsagent -d opsagent "
        f"-A -F '|' -t -c \"{sql}\""
    )


def dump_suggestions(conv_id: str) -> str:
    sql = (
        "SELECT suggestion_id, action_type, target_type, target_id, "
        "       status, retry_of, confirmed_by, confirmed_at, executed_at, "
        "       LEFT(result, 120) as result, created_at "
        "FROM agent_suggestions "
        "WHERE conversation_id='{c}' "
        "ORDER BY id ASC"
    ).format(c=conv_id.replace("'", "''"))
    return run_remote(
        f"docker exec ops-agent-postgres psql -U opsagent -d opsagent "
        f"-A -F '|' -t -c \"{sql}\""
    )


def dump_plans(conv_id: str) -> str:
    sql = (
        "SELECT plan_id, status, summary, LEFT(steps::text, 200) as steps, "
        "created_at, updated_at "
        "FROM agent_plans "
        "WHERE conversation_id='{c}' "
        "ORDER BY id ASC"
    ).format(c=conv_id.replace("'", "''"))
    return run_remote(
        f"docker exec ops-agent-postgres psql -U opsagent -d opsagent "
        f"-A -F '|' -t -c \"{sql}\""
    )


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if not arg:
        out = run_remote(
            "docker exec ops-agent-postgres psql -U opsagent -d opsagent "
            "-A -F '|' -t -c \"SELECT conversation_id, title, LEFT(user_id::text, 5), "
            "updated_at FROM agent_conversations ORDER BY updated_at DESC LIMIT 10\""
        )
        print(f"recent conversations:\n{out}")
        print("\nusage: python scripts/dump_chat.py <conversation_id|substr>")
        return
    print(f">> lookup '{arg}'")
    found = find_conversation(arg)
    if not found:
        print("no conversation matched")
        return
    print("matched:")
    print(found)
    # 1st column = conversation_id
    conv_id = found.splitlines()[0].split("|")[0].strip()
    if not conv_id:
        print("could not parse id")
        return
    print(f"\n=== messages for {conv_id} ===")
    print(dump_messages(conv_id) or "(none)")
    print(f"\n=== tasks for {conv_id} ===")
    print(dump_tasks(conv_id) or "(none)")
    print(f"\n=== suggestions for {conv_id} ===")
    print(dump_suggestions(conv_id) or "(none)")
    print(f"\n=== plans for {conv_id} ===")
    print(dump_plans(conv_id) or "(none)")


if __name__ == "__main__":
    main()
