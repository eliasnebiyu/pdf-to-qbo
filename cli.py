"""
PDF-to-QBO CLI

Usage examples:
  python cli.py convert statement.pdf
  python cli.py convert statement.pdf --format qfx --output ~/Downloads/
  python cli.py convert statement.pdf --format csv
  python cli.py banks
"""
import sys
from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel

from src.parser import detect_and_parse, list_supported_banks
from src.exporter import save_ofx, save_csv

console = Console()


@click.group()
@click.version_option("1.0.0", prog_name="parsify")
def cli():
    """Convert bank statement PDFs to QuickBooks-compatible OFX/QFX/CSV files."""
    pass


@cli.command()
@click.argument("pdf_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--format", "-f",
    type=click.Choice(["ofx", "qfx", "csv"], case_sensitive=False),
    default="ofx",
    show_default=True,
    help="Output format. OFX/QFX = direct QBO import. CSV = manual import.",
)
@click.option(
    "--output", "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output file or directory. Defaults to same directory as input PDF.",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Show detailed transaction list after conversion.",
)
def convert(pdf_file: Path, format: str, output: Optional[Path], verbose: bool):
    """
    Convert a bank statement PDF to QBO-compatible format.

    PDF_FILE: path to the bank statement PDF
    """
    console.print(f"\n[bold]parsify[/bold] — converting [cyan]{pdf_file.name}[/cyan]")
    console.print("─" * 50)

    # Parse
    with console.status("[bold green]Parsing PDF..."):
        try:
            statement = detect_and_parse(pdf_file)
        except FileNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)
        except Exception as e:
            console.print(f"[red]Unexpected error:[/red] {e}")
            sys.exit(1)

    # Print warnings
    for w in statement.warnings:
        console.print(f"[yellow]⚠ Warning:[/yellow] {w}")

    # Summary panel
    acc = statement.account
    console.print(Panel(
        f"[bold]{acc.bank_name}[/bold]  ·  Account: [cyan]{acc.account_id or 'unknown'}[/cyan]\n"
        f"Period:  [green]{acc.statement_start}[/green] → [green]{acc.statement_end}[/green]\n"
        f"Transactions: [bold]{statement.transaction_count}[/bold]  "
        f"· Debits: [red]${statement.total_debits:,.2f}[/red]  "
        f"· Credits: [green]${statement.total_credits:,.2f}[/green]",
        title="Statement Summary",
        border_style="blue",
    ))

    # Verbose transaction table
    if verbose and statement.transactions:
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        table.add_column("Date",   style="cyan",  width=12)
        table.add_column("Description", width=40)
        table.add_column("Amount", justify="right", width=12)
        table.add_column("Balance", justify="right", width=12)

        for tx in statement.transactions:
            amt_str = f"[red]-${abs(tx.amount):,.2f}[/red]" if tx.amount < 0 \
                      else f"[green]+${tx.amount:,.2f}[/green]"
            bal_str = f"${tx.balance:,.2f}" if tx.balance else "—"
            table.add_row(
                str(tx.date),
                tx.description[:40],
                amt_str,
                bal_str,
            )
        console.print(table)

    # Determine output path
    fmt_lower = format.lower()
    if output is None:
        out_path = pdf_file.with_suffix(f".{fmt_lower}")
    elif output.is_dir():
        out_path = output / pdf_file.with_suffix(f".{fmt_lower}").name
    else:
        out_path = output

    # Export
    with console.status(f"[bold green]Writing {fmt_lower.upper()}..."):
        if fmt_lower in ("ofx", "qfx"):
            saved = save_ofx(statement, out_path, is_qfx=(fmt_lower == "qfx"))
        else:
            saved = save_csv(statement, out_path)

    console.print(f"\n[bold green]✓ Done![/bold green] Saved to: [cyan]{saved}[/cyan]")
    console.print(
        "\n[dim]Next step: In QuickBooks Online, go to[/dim]\n"
        "  [bold]Banking → Upload transactions → Select your file[/bold]\n"
    )


@cli.command()
def banks():
    """List all supported banks."""
    console.print("\n[bold]Supported banks:[/bold]\n")
    for name in list_supported_banks():
        console.print(f"  • {name}")
    console.print(
        "\n[dim]Don't see your bank? The generic parser handles most "
        "standard PDF layouts automatically.[/dim]\n"
    )


if __name__ == "__main__":
    cli()
