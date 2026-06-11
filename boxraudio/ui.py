"""
ui.py — Rich-based UI utilities for BoxR.
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn,
    TimeElapsedColumn, TimeRemainingColumn, MofNCompleteColumn
)
from rich.prompt import Confirm, Prompt
from rich.text import Text


console = Console()


def info(message: str):
    console.print(f"  [cyan]ℹ[/cyan]  {message}")


def success(message: str):
    console.print(f"  [green]✓[/green]  {message}")


def warning(message: str):
    console.print(f"  [yellow]![/yellow]  {message}")


def error(message: str):
    console.print(f"  [red]✗[/red]  {message}")


def dim(message: str):
    console.print(f"  [dim]{message}[/dim]")


def step_header(step_num: int, total_steps: int, title: str):
    """Print a colored step header."""
    console.print()
    console.print(
        Panel(
            f"[bold bright_blue]STEP {step_num} of {total_steps}[/bold bright_blue]   "
            f"[bold]{title}[/bold]",
            border_style="bright_blue",
            padding=(0, 1),
        )
    )


def section(title: str):
    """Print a section header (non-step)."""
    console.print()
    console.print(f"[bold bright_cyan]{title}[/bold bright_cyan]")
    console.print(f"[bright_cyan]{'─' * len(title)}[/bright_cyan]")


def kv_table(title: str, items: dict, color: str = "cyan") -> None:
    """Print a key-value table."""
    table = Table(title=title, title_style=f"bold {color}",
                  show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column(style="")
    for k, v in items.items():
        table.add_row(str(k), str(v) if v is not None else "[dim](none)[/dim]")
    console.print(table)


def file_table(title: str, files: list, limit: int = 25) -> None:
    """Print a numbered list of files in a panel."""
    if not files:
        return
    lines = []
    for i, f in enumerate(files[:limit], 1):
        lines.append(f"[dim]{i:04d}[/dim]  {f}")
    if len(files) > limit:
        lines.append(f"[dim]... and {len(files) - limit:,} more[/dim]")
    panel = Panel("\n".join(lines), title=title, border_style="cyan", padding=(0, 1))
    console.print(panel)


def confirm(message: str, default: bool = False) -> bool:
    """Ask the user for YES/NO confirmation."""
    return Confirm.ask(f"[yellow]{message}[/yellow]", default=default, console=console)


def confirm_yes_required(message: str) -> bool:
    """Require typing 'YES' to confirm a destructive action."""
    response = Prompt.ask(
        f"[bold yellow]{message}[/bold yellow]\n  Type [bold]YES[/bold] to confirm",
        default="",
        console=console,
    )
    return response.strip() == "YES"


def prompt(message: str, default: str = "") -> str:
    """Prompt for a string value."""
    return Prompt.ask(message, default=default, console=console)


def make_progress(description: str = "Working") -> Progress:
    """Create a Rich progress bar with sensible columns."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(complete_style="cyan", finished_style="green"),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


def print_completion_banner():
    """Print a final 'pipeline complete' banner."""
    console.print()
    console.print(Panel(
        "[bold bright_green]PIPELINE COMPLETE[/bold bright_green]",
        border_style="bright_green",
        padding=(0, 2),
    ))
    console.print()
