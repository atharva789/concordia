#!/usr/bin/env python3
"""
PromptBus: multi-user prompt inbox with deduped tasks and executor.
"""
import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_DB = os.environ.get("PROMPTBUS_DB", "data/promptbus.db")
DEFAULT_CONFIG = os.environ.get("PROMPTBUS_CONFIG", "config.json")

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT NOT NULL,
    title TEXT,
    prompt TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NEW',
    task_id INTEGER
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT NOT NULL,
    prompt TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NEW',
    last_run_at TEXT,
    run_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS task_prompts (
    task_id INTEGER NOT NULL,
    prompt_id INTEGER NOT NULL,
    PRIMARY KEY (task_id, prompt_id)
);

CREATE INDEX IF NOT EXISTS idx_prompts_status ON prompts(status);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""

TOKEN_RE = re.compile(r"[a-zA-Z0-9_']+")


@dataclass
class Prompt:
    id: int
    user: str
    title: str
    prompt: str
    created_at: str


@dataclass
class Task:
    id: int
    summary: str
    prompt: str
    created_at: str


def now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def connect_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def load_config(path: str) -> Dict[str, str]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def tokenize(text: str) -> List[str]:
    return [t.lower() for t in TOKEN_RE.findall(text)]


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa = set(a)
    sb = set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def summarize(prompt: Prompt) -> str:
    if prompt.title:
        return prompt.title.strip()
    words = tokenize(prompt.prompt)
    return " ".join(words[:12]) or "untitled"


def build_task_prompt(prompts: List[Prompt]) -> str:
    lines = [
        "You are the execution agent. Deduplicate and satisfy the following combined user prompts:",
        "",
    ]
    for p in prompts:
        title = p.title.strip() if p.title else "untitled"
        lines.append(f"- From {p.user}: {title}")
        lines.append(p.prompt.strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def fetch_new_prompts(conn: sqlite3.Connection) -> List[Prompt]:
    rows = conn.execute(
        "SELECT id, user, title, prompt, created_at FROM prompts WHERE status='NEW' ORDER BY id"
    ).fetchall()
    return [Prompt(**row) for row in rows]


def fetch_open_tasks(conn: sqlite3.Connection) -> List[Task]:
    rows = conn.execute(
        "SELECT id, summary, prompt, created_at FROM tasks WHERE status='NEW' ORDER BY id"
    ).fetchall()
    return [Task(**row) for row in rows]


def attach_prompt_to_task(conn: sqlite3.Connection, prompt_id: int, task_id: int) -> None:
    conn.execute("UPDATE prompts SET status='CLUSTERED', task_id=? WHERE id=?", (task_id, prompt_id))
    conn.execute(
        "INSERT OR IGNORE INTO task_prompts(task_id, prompt_id) VALUES (?, ?)",
        (task_id, prompt_id),
    )


def create_task(conn: sqlite3.Connection, summary: str, prompt_text: str) -> int:
    cur = conn.execute(
        "INSERT INTO tasks(summary, prompt, created_at, status) VALUES (?, ?, ?, 'NEW')",
        (summary, prompt_text, now_iso()),
    )
    return int(cur.lastrowid)


def update_task_prompt(conn: sqlite3.Connection, task_id: int, prompt_text: str) -> None:
    conn.execute("UPDATE tasks SET prompt=? WHERE id=?", (prompt_text, task_id))


def dedupe(conn: sqlite3.Connection, threshold: float) -> int:
    new_prompts = fetch_new_prompts(conn)
    if not new_prompts:
        return 0

    open_tasks = fetch_open_tasks(conn)
    task_tokens = {t.id: tokenize(t.prompt + " " + t.summary) for t in open_tasks}

    created_or_updated = 0
    clusters: List[Tuple[Optional[int], List[Prompt]]] = []

    for p in new_prompts:
        p_tokens = tokenize(p.prompt + " " + (p.title or ""))

        best_task_id = None
        best_score = 0.0
        for task_id, tokens in task_tokens.items():
            score = jaccard(p_tokens, tokens)
            if score > best_score:
                best_score = score
                best_task_id = task_id

        if best_task_id is not None and best_score >= threshold:
            # Attach to existing task
            attach_prompt_to_task(conn, p.id, best_task_id)
            created_or_updated += 1
            continue

        # Compare to new clusters
        best_cluster = None
        best_cluster_score = 0.0
        for idx, (cluster_task_id, prompts) in enumerate(clusters):
            cluster_tokens = tokenize(" ".join(pr.prompt for pr in prompts))
            score = jaccard(p_tokens, cluster_tokens)
            if score > best_cluster_score:
                best_cluster_score = score
                best_cluster = idx

        if best_cluster is not None and best_cluster_score >= threshold:
            clusters[best_cluster][1].append(p)
        else:
            clusters.append((None, [p]))

    for _, prompts in clusters:
        summary = summarize(prompts[0])
        prompt_text = build_task_prompt(prompts)
        task_id = create_task(conn, summary, prompt_text)
        for p in prompts:
            attach_prompt_to_task(conn, p.id, task_id)
        created_or_updated += 1

    # Refresh task prompts for existing tasks that got new prompts
    for task in open_tasks:
        rows = conn.execute(
            "SELECT id, user, title, prompt, created_at FROM prompts WHERE task_id=? ORDER BY id",
            (task.id,),
        ).fetchall()
        prompts = [Prompt(**row) for row in rows]
        if prompts:
            update_task_prompt(conn, task.id, build_task_prompt(prompts))

    return created_or_updated


def claim_next_task(conn: sqlite3.Connection) -> Optional[Task]:
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT id, summary, prompt, created_at FROM tasks WHERE status='NEW' ORDER BY id LIMIT 1"
    ).fetchone()
    if not row:
        conn.execute("COMMIT")
        return None
    conn.execute("UPDATE tasks SET status='IN_PROGRESS' WHERE id=?", (row["id"],))
    conn.execute("COMMIT")
    return Task(**row)


def mark_task_done(conn: sqlite3.Connection, task_id: int, status: str) -> None:
    conn.execute(
        "UPDATE tasks SET status=?, last_run_at=?, run_count=run_count+1 WHERE id=?",
        (status, now_iso(), task_id),
    )


def run_task(task: Task, executor_command: str, dry_run: bool) -> int:
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".prompt", encoding="utf-8") as f:
        f.write(task.prompt)
        prompt_path = f.name
    cmd = executor_command.format(prompt_file=prompt_path, task_id=task.id)
    if dry_run:
        print(cmd)
        return 0
    try:
        result = subprocess.run(cmd, shell=True, check=False)
        return result.returncode
    finally:
        try:
            os.unlink(prompt_path)
        except OSError:
            pass


def cmd_submit(args: argparse.Namespace) -> None:
    prompt_text = args.prompt
    if prompt_text is None:
        prompt_text = sys.stdin.read()
    if not prompt_text.strip():
        raise SystemExit("Prompt text is empty")
    conn = connect_db(args.db)
    conn.execute(
        "INSERT INTO prompts(user, title, prompt, created_at, status) VALUES (?, ?, ?, ?, 'NEW')",
        (args.user, args.title, prompt_text.strip(), now_iso()),
    )
    conn.commit()
    print("submitted")


def cmd_list(args: argparse.Namespace) -> None:
    conn = connect_db(args.db)
    if args.kind in ("prompts", "all"):
        rows = conn.execute(
            "SELECT id, user, title, status, created_at FROM prompts ORDER BY id DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
        print("prompts")
        for row in rows:
            title = row["title"] or "untitled"
            print(f"  #{row['id']} {row['status']} {row['user']} {title} {row['created_at']}")
    if args.kind in ("tasks", "all"):
        rows = conn.execute(
            "SELECT id, summary, status, created_at, run_count FROM tasks ORDER BY id DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
        print("tasks")
        for row in rows:
            print(
                f"  #{row['id']} {row['status']} runs={row['run_count']} {row['summary']} {row['created_at']}"
            )


def cmd_dedupe(args: argparse.Namespace) -> None:
    conn = connect_db(args.db)
    conn.execute("BEGIN IMMEDIATE")
    changed = dedupe(conn, args.threshold)
    conn.execute("COMMIT")
    print(f"deduped {changed} task(s)")


def cmd_run(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    executor_command = args.executor or config.get("executor_command")
    if not executor_command:
        raise SystemExit("Missing executor command. Set --executor or config.json")
    conn = connect_db(args.db)

    if args.task_id:
        row = conn.execute(
            "SELECT id, summary, prompt, created_at FROM tasks WHERE id=?",
            (args.task_id,),
        ).fetchone()
        if not row:
            raise SystemExit("Task not found")
        task = Task(**row)
    else:
        task = claim_next_task(conn)
        if task is None:
            print("no tasks")
            return

    code = run_task(task, executor_command, args.dry_run)
    status = "DONE" if code == 0 else "FAILED"
    mark_task_done(conn, task.id, status)
    conn.commit()
    print(f"task #{task.id} {status}")


def cmd_agent(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    executor_command = args.executor or config.get("executor_command")
    if not executor_command:
        raise SystemExit("Missing executor command. Set --executor or config.json")
    conn = connect_db(args.db)
    while True:
        conn.execute("BEGIN IMMEDIATE")
        changed = dedupe(conn, args.threshold)
        conn.execute("COMMIT")
        if changed:
            print(f"deduped {changed} task(s)")
        task = claim_next_task(conn)
        if task:
            code = run_task(task, executor_command, args.dry_run)
            status = "DONE" if code == 0 else "FAILED"
            mark_task_done(conn, task.id, status)
            conn.commit()
            print(f"task #{task.id} {status}")
        else:
            time.sleep(args.poll)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="promptbus", description="Multi-user prompt bus")
    p.add_argument("--db", default=DEFAULT_DB, help="Path to SQLite db")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("submit", help="Submit a prompt")
    sp.add_argument("--user", required=True, help="User name")
    sp.add_argument("--title", default="", help="Short title")
    sp.add_argument("--prompt", help="Prompt text (otherwise read stdin)")
    sp.set_defaults(func=cmd_submit)

    sp = sub.add_parser("list", help="List prompts or tasks")
    sp.add_argument("--kind", choices=["prompts", "tasks", "all"], default="all")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("dedupe", help="Deduplicate prompts into tasks")
    sp.add_argument("--threshold", type=float, default=0.35, help="Jaccard threshold")
    sp.set_defaults(func=cmd_dedupe)

    sp = sub.add_parser("run", help="Run a task via executor command")
    sp.add_argument("--config", default=DEFAULT_CONFIG)
    sp.add_argument("--executor", help="Override executor command")
    sp.add_argument("--task-id", type=int, help="Run a specific task id")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("agent", help="Loop: dedupe + run tasks")
    sp.add_argument("--config", default=DEFAULT_CONFIG)
    sp.add_argument("--executor", help="Override executor command")
    sp.add_argument("--threshold", type=float, default=0.35)
    sp.add_argument("--poll", type=float, default=2.0)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_agent)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
