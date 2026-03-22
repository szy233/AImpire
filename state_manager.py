"""
state_manager — 实验状态管理

SQLite 存储：
- 实验记录（参数、状态、结果）
- 操作日志
- 状态摘要（给 Agent 做上下文）
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass
from enum import Enum

from loguru import logger

from configs.config_manager import get_config


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StateManager:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_config().local.db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS experiments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    branch TEXT DEFAULT '',
                    params TEXT DEFAULT '{}',
                    status TEXT DEFAULT 'pending',
                    command TEXT DEFAULT '',
                    started_at REAL,
                    finished_at REAL,
                    result_summary TEXT DEFAULT '',
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS action_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id INTEGER,
                    action TEXT NOT NULL,
                    detail TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
                );

                CREATE INDEX IF NOT EXISTS idx_exp_status ON experiments(status);
                CREATE INDEX IF NOT EXISTS idx_exp_created ON experiments(created_at);
            """)
        logger.info(f"数据库就绪: {self.db_path}")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ===== 实验 CRUD =====

    def create_experiment(self, name: str, params: Dict = None,
                          branch: str = "", command: str = "") -> int:
        """创建新实验，返回 ID"""
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO experiments (name, branch, params, status, command, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name, branch, json.dumps(params or {}, ensure_ascii=False),
                 TaskStatus.PENDING, command, time.time())
            )
            exp_id = cursor.lastrowid
            self._log_action(conn, exp_id, "created", f"实验创建: {name}")
            logger.info(f"创建实验 #{exp_id}: {name}")
            return exp_id

    def update_status(self, exp_id: int, status: TaskStatus,
                      result_summary: str = "") -> None:
        """更新实验状态"""
        now = time.time()
        updates = {"status": status}

        if status == TaskStatus.RUNNING:
            updates["started_at"] = now
        elif status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
            updates["finished_at"] = now
            if result_summary:
                updates["result_summary"] = result_summary

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [exp_id]

        with self._conn() as conn:
            conn.execute(f"UPDATE experiments SET {set_clause} WHERE id = ?", values)
            self._log_action(conn, exp_id, "status_change", f"状态 -> {status}")
        logger.info(f"实验 #{exp_id} 状态 -> {status}")

    def get_experiment(self, exp_id: int) -> Optional[Dict]:
        """获取实验详情"""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,)).fetchone()
            return dict(row) if row else None

    def get_latest(self, n: int = 5) -> List[Dict]:
        """获取最近 n 个实验"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM experiments ORDER BY created_at DESC LIMIT ?", (n,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_running(self) -> List[Dict]:
        """获取正在运行的实验"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM experiments WHERE status = ?", (TaskStatus.RUNNING,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ===== 操作日志 =====

    def _log_action(self, conn: sqlite3.Connection, exp_id: Optional[int],
                    action: str, detail: str = ""):
        conn.execute(
            "INSERT INTO action_log (experiment_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (exp_id, action, detail, time.time())
        )

    def log_action(self, exp_id: Optional[int], action: str, detail: str = ""):
        """记录操作日志"""
        with self._conn() as conn:
            self._log_action(conn, exp_id, action, detail)

    def get_actions(self, exp_id: int, n: int = 20) -> List[Dict]:
        """获取实验的操作日志"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM action_log WHERE experiment_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (exp_id, n)
            ).fetchall()
            return [dict(r) for r in rows]

    # ===== 摘要（给 Agent 做上下文）=====

    def summary_text(self) -> str:
        """生成当前状态摘要"""
        running = self.get_running()
        recent = self.get_latest(5)

        lines = []

        if running:
            lines.append(f"🔄 正在运行 ({len(running)} 个):")
            for exp in running:
                elapsed = time.time() - (exp["started_at"] or time.time())
                lines.append(f"  #{exp['id']} {exp['name']} — 已运行 {elapsed/60:.0f} 分钟")
        else:
            lines.append("当前没有运行中的实验")

        if recent:
            lines.append(f"\n📜 最近实验:")
            for exp in recent:
                icon = {
                    "pending": "⏳", "running": "🔄", "success": "✅",
                    "failed": "❌", "cancelled": "🚫"
                }.get(exp["status"], "?")
                lines.append(f"  {icon} #{exp['id']} {exp['name']} [{exp['status']}]")
                if exp["result_summary"]:
                    lines.append(f"     {exp['result_summary'][:80]}")

        return "\n".join(lines)


# ===== 全局实例 =====
_state: Optional[StateManager] = None


def get_state_manager() -> StateManager:
    global _state
    if _state is None:
        _state = StateManager()
    return _state


# ===== CLI 测试 =====

if __name__ == "__main__":
    from rich import print as rprint
    from configs.config_manager import load_config

    load_config()
    sm = StateManager()

    # 模拟完整流程
    rprint("[yellow]模拟实验流程...[/yellow]\n")

    # 创建实验
    eid = sm.create_experiment(
        name="baseline_resnet50",
        params={"lr": 0.001, "batch_size": 32, "epochs": 10},
        branch="exp/baseline",
        command="python train.py --lr 0.001"
    )
    rprint(f"[green]✓ 创建实验 #{eid}[/green]")

    # 启动
    sm.update_status(eid, TaskStatus.RUNNING)
    rprint(f"[green]✓ 实验已启动[/green]")

    # 完成
    sm.update_status(eid, TaskStatus.SUCCESS, "val_loss: 0.5678, accuracy: 85%")
    rprint(f"[green]✓ 实验已完成[/green]")

    # 再创建一个
    eid2 = sm.create_experiment(
        name="exp_lr0.0001",
        params={"lr": 0.0001, "batch_size": 64},
        command="python train.py --lr 0.0001"
    )
    sm.update_status(eid2, TaskStatus.RUNNING)

    # 查看摘要
    rprint(f"\n{sm.summary_text()}")

    # 查看详情
    rprint(f"\n[dim]实验 #{eid} 详情: {sm.get_experiment(eid)}[/dim]")
    rprint(f"[dim]操作日志: {sm.get_actions(eid)}[/dim]")
