"""
log_analyzer — 训练日志解析

解析训练日志，提取关键指标。
内置通用解析器，支持自定义解析规则。

目前支持：
- 通用正则匹配（loss, accuracy, lr, epoch, step）
- JSON Lines 格式
- 自定义解析器（后续按你的训练框架个性化）
"""

import re
import json
from pathlib import Path
from typing import Optional, List, Dict, Callable
from dataclasses import dataclass, field, asdict

from loguru import logger


@dataclass
class TrainingMetrics:
    """单个时间点的训练指标"""
    epoch: Optional[float] = None
    step: Optional[int] = None
    train_loss: Optional[float] = None
    val_loss: Optional[float] = None
    accuracy: Optional[float] = None
    lr: Optional[float] = None
    extra: Dict[str, float] = field(default_factory=dict)


@dataclass
class TrainingSummary:
    """训练总结"""
    total_epochs: int = 0
    total_steps: int = 0
    best_val_loss: Optional[float] = None
    best_accuracy: Optional[float] = None
    final_train_loss: Optional[float] = None
    final_val_loss: Optional[float] = None
    is_finished: bool = False
    metrics_history: List[Dict] = field(default_factory=list)
    raw_tail: str = ""

    def to_text(self) -> str:
        """生成人类可读的摘要"""
        lines = ["📊 训练摘要:"]
        if self.total_epochs:
            lines.append(f"  Epochs: {self.total_epochs}")
        if self.total_steps:
            lines.append(f"  Steps: {self.total_steps}")
        if self.final_train_loss is not None:
            lines.append(f"  最终 train loss: {self.final_train_loss:.4f}")
        if self.final_val_loss is not None:
            lines.append(f"  最终 val loss: {self.final_val_loss:.4f}")
        if self.best_val_loss is not None:
            lines.append(f"  最佳 val loss: {self.best_val_loss:.4f}")
        if self.best_accuracy is not None:
            lines.append(f"  最佳 accuracy: {self.best_accuracy:.2%}")
        lines.append(f"  状态: {'✅ 已完成' if self.is_finished else '🔄 进行中'}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """导出为字典（方便存数据库或传给 Claude 分析）"""
        return asdict(self)


class LogAnalyzer:
    """
    训练日志解析器。

    内置通用规则，可通过 register_parser() 添加自定义解析逻辑。
    """

    # 通用正则模式
    DEFAULT_PATTERNS = {
        "epoch": re.compile(r"[Ee]poch[:\s]*(\d+)"),
        "step": re.compile(r"[Ss]tep[:\s]*(\d+)"),
        "train_loss": re.compile(r"(?:train[_\s]?)?loss[:\s]*([0-9]+\.?[0-9]*)"),
        "val_loss": re.compile(r"val[_\s]?loss[:\s]*([0-9]+\.?[0-9]*)"),
        "accuracy": re.compile(r"acc(?:uracy)?[:\s]*([0-9]+\.?[0-9]*)"),
        "lr": re.compile(r"(?:learning[_\s]?rate|lr)[:\s]*([0-9]+\.?[0-9e\-]*)"),
    }

    FINISH_MARKERS = [
        "training complete", "training finished", "done training",
        "best model saved", "finished", "completed",
    ]

    def __init__(self):
        self.patterns = dict(self.DEFAULT_PATTERNS)
        self.custom_parsers: List[Callable] = []

    # ===== 自定义扩展 =====

    def add_pattern(self, name: str, pattern: str):
        """
        添加自定义正则模式。

        用法：
            analyzer.add_pattern("f1_score", r"f1[:\s]*([0-9]+\.?[0-9]*)")
        """
        self.patterns[name] = re.compile(pattern)
        logger.info(f"添加解析模式: {name}")

    def register_parser(self, parser_fn: Callable):
        """
        注册自定义解析函数。

        函数签名: def my_parser(line: str) -> Optional[TrainingMetrics]
        返回 None 表示这行不匹配。

        用法：
            def parse_my_format(line):
                # 你的自定义解析逻辑
                if "my_metric" in line:
                    m = TrainingMetrics()
                    m.extra["my_metric"] = float(...)
                    return m
                return None
            analyzer.register_parser(parse_my_format)
        """
        self.custom_parsers.append(parser_fn)
        logger.info(f"注册自定义解析器: {parser_fn.__name__}")

    # ===== 解析 =====

    def parse_log_file(self, log_path: str) -> TrainingSummary:
        """解析日志文件"""
        path = Path(log_path)
        if not path.exists():
            logger.warning(f"日志文件不存在: {log_path}")
            return TrainingSummary()
        content = path.read_text(encoding="utf-8", errors="replace")
        return self.parse_log_text(content)

    def parse_log_text(self, text: str) -> TrainingSummary:
        """解析日志文本"""
        lines = text.strip().split("\n")
        summary = TrainingSummary()
        summary.raw_tail = "\n".join(lines[-20:])

        all_metrics = []

        for line in lines:
            # 先尝试自定义解析器
            metrics = None
            for parser in self.custom_parsers:
                metrics = parser(line)
                if metrics:
                    break

            # 再用通用正则
            if not metrics:
                metrics = self._parse_line(line)

            if metrics:
                all_metrics.append(metrics)

            # 检查是否完成
            if any(marker in line.lower() for marker in self.FINISH_MARKERS):
                summary.is_finished = True

        # 尝试 JSON Lines 格式
        if not all_metrics:
            all_metrics = self._try_jsonl(lines)

        # 汇总
        self._summarize(summary, all_metrics)
        return summary

    def parse_remote_log(self, log_path: str) -> TrainingSummary:
        """
        直接从算力服务器读取日志并解析。

        Args:
            log_path: 算力服务器上的日志路径（绝对路径或相对于 workspace）
        """
        from tools.ssh_executor import get_ssh_executor
        from configs.config_manager import get_config

        ssh = get_ssh_executor()
        ws = get_config().gpu_server.workspace

        # 如果是相对路径，拼上 workspace
        if not log_path.startswith("/"):
            log_path = f"{ws}/{log_path}"

        result = ssh.run(f"cat {log_path}", timeout=30)
        if result["exit_code"] != 0:
            logger.warning(f"读取远程日志失败: {result['stderr']}")
            return TrainingSummary()

        return self.parse_log_text(result["stdout"])

    def tail_remote_log(self, log_path: str, lines: int = 50) -> str:
        """读取远程日志最后 N 行（快速查看进度）"""
        from tools.ssh_executor import get_ssh_executor
        from configs.config_manager import get_config

        ssh = get_ssh_executor()
        ws = get_config().gpu_server.workspace

        if not log_path.startswith("/"):
            log_path = f"{ws}/{log_path}"

        result = ssh.run(f"tail -n {lines} {log_path}", timeout=15)
        return result["stdout"]

    # ===== 实验对比 =====

    def compare_experiments(self, summaries: Dict[str, TrainingSummary]) -> str:
        """对比多个实验的结果"""
        lines = ["📊 实验对比:", ""]
        header = f"{'实验':<20} {'val_loss':<12} {'accuracy':<12} {'epochs':<8} {'状态':<6}"
        lines.append(header)
        lines.append("-" * len(header))

        for name, s in summaries.items():
            vl = f"{s.best_val_loss:.4f}" if s.best_val_loss else "N/A"
            acc = f"{s.best_accuracy:.2%}" if s.best_accuracy else "N/A"
            ep = str(s.total_epochs)
            status = "✅" if s.is_finished else "🔄"
            lines.append(f"{name:<20} {vl:<12} {acc:<12} {ep:<8} {status:<6}")

        return "\n".join(lines)

    # ===== 内部方法 =====

    def _parse_line(self, line: str) -> Optional[TrainingMetrics]:
        """用正则解析单行"""
        metrics = TrainingMetrics()
        found_any = False

        for key, pattern in self.patterns.items():
            match = pattern.search(line)
            if match:
                try:
                    value = float(match.group(1))
                    if hasattr(metrics, key):
                        setattr(metrics, key, value)
                    else:
                        metrics.extra[key] = value
                    found_any = True
                except (ValueError, IndexError):
                    pass

        return metrics if found_any else None

    def _try_jsonl(self, lines: List[str]) -> List[TrainingMetrics]:
        """尝试 JSON Lines 格式"""
        metrics_list = []
        for line in lines:
            try:
                data = json.loads(line.strip())
                if isinstance(data, dict):
                    m = TrainingMetrics(
                        epoch=data.get("epoch"),
                        step=data.get("step") or data.get("global_step"),
                        train_loss=data.get("loss") or data.get("train_loss"),
                        val_loss=data.get("val_loss") or data.get("eval_loss"),
                        accuracy=data.get("accuracy") or data.get("eval_accuracy"),
                        lr=data.get("learning_rate") or data.get("lr"),
                    )
                    metrics_list.append(m)
            except (json.JSONDecodeError, KeyError):
                continue
        return metrics_list

    def _summarize(self, summary: TrainingSummary, all_metrics: List[TrainingMetrics]):
        """从指标列表生成汇总"""
        if not all_metrics:
            return

        summary.metrics_history = [asdict(m) for m in all_metrics]
        summary.total_steps = max((m.step or 0) for m in all_metrics)
        summary.total_epochs = int(max((m.epoch or 0) for m in all_metrics))

        val_losses = [m.val_loss for m in all_metrics if m.val_loss is not None]
        train_losses = [m.train_loss for m in all_metrics if m.train_loss is not None]
        accuracies = [m.accuracy for m in all_metrics if m.accuracy is not None]

        if val_losses:
            summary.best_val_loss = min(val_losses)
            summary.final_val_loss = val_losses[-1]
        if train_losses:
            summary.final_train_loss = train_losses[-1]
        if accuracies:
            summary.best_accuracy = max(accuracies)


# ===== 全局实例 =====

def get_log_analyzer() -> LogAnalyzer:
    return LogAnalyzer()


# ===== CLI 测试 =====

if __name__ == "__main__":
    import sys
    from rich import print as rprint

    if "--remote" in sys.argv:
        # 从算力服务器读取日志
        from configs.config_manager import load_config
        load_config()
        analyzer = LogAnalyzer()

        log_path = sys.argv[sys.argv.index("--remote") + 1] if len(sys.argv) > sys.argv.index("--remote") + 1 else ""
        if not log_path:
            rprint("[red]用法: python -m tools.log_analyzer --remote <日志路径>[/red]")
            sys.exit(1)

        rprint(f"[yellow]解析远程日志: {log_path}[/yellow]")
        summary = analyzer.parse_remote_log(log_path)
        rprint(summary.to_text())

    else:
        # 用模拟日志测试
        sample_log = """
        Epoch 1/10, Step 100, loss: 2.3456, val_loss: 2.1234, lr: 0.001
        Epoch 1/10, Step 200, loss: 1.8765, val_loss: 1.7654, lr: 0.001
        Epoch 2/10, Step 300, loss: 1.2345, val_loss: 1.1234, lr: 0.0005
        Epoch 3/10, Step 400, loss: 0.8765, val_loss: 0.9123, accuracy: 0.72, lr: 0.0005
        Epoch 4/10, Step 500, loss: 0.5432, val_loss: 0.6789, accuracy: 0.81, lr: 0.0001
        Epoch 5/10, Step 600, loss: 0.3210, val_loss: 0.5678, accuracy: 0.85, lr: 0.0001
        Training complete. Best model saved.
        """

        analyzer = LogAnalyzer()

        # 演示自定义模式
        analyzer.add_pattern("f1_score", r"f1[:\s]*([0-9]+\.?[0-9]*)")

        summary = analyzer.parse_log_text(sample_log)
        rprint("[green]✓ 模拟日志解析结果:[/green]")
        rprint(summary.to_text())
        rprint(f"\n  历史记录数: {len(summary.metrics_history)}")
        rprint("\n[dim]自定义方式:[/dim]")
        rprint("  analyzer.add_pattern('f1', r'f1[:\\s]*(...)')     # 添加正则")
        rprint("  analyzer.register_parser(my_fn)                  # 注册函数")
        rprint("  analyzer.parse_remote_log('logs/train.log')      # 解析远程日志")
