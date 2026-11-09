"""CLI command for running comparative model performance benchmarks."""

from __future__ import annotations

import asyncio

import typer

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.core.benchmarking import BenchmarkEngine
from keypool.integrations.json_output import emit_json

benchmark_app = typer.Typer(
    name="benchmark",
    help="Benchmark model latency, throughput, TTFT, and cost across providers.",
    no_args_is_help=True,
)


@benchmark_app.command("run")
def benchmark_run(
    models: str = typer.Option(
        "fast,smart",
        "--models",
        "-m",
        help="Comma-separated list of models or aliases to benchmark.",
    ),
    prompt: str = typer.Option(
        "Explain the theory of relativity in two concise sentences.",
        "--prompt",
        "-p",
        help="Prompt text to evaluate model performance.",
    ),
    runs: int = typer.Option(2, "--runs", "-r", help="Number of benchmark iterations per target."),
    json_output: bool = typer.Option(
        False, "--json", help="Output benchmark results in JSON format."
    ),
) -> None:
    """Run performance benchmarks comparing latency, TTFT, throughput, and cost."""
    console = get_console()
    ctx = RuntimeContext()
    engine = BenchmarkEngine(ctx)

    target_models = [m.strip() for m in models.split(",") if m.strip()]
    if not target_models:
        console.print("[red]No models specified to benchmark.[/red]")
        raise typer.Exit(1)

    if not json_output:
        console.print(
            f"[bold cyan]Running benchmark across {len(target_models)} target(s) ({runs} run(s) each)...[/bold cyan]"
        )

    report = asyncio.run(engine.run_benchmark(target_models, prompt, runs=runs))

    if json_output:
        emit_json(
            "benchmark_run",
            {
                "prompt": report.prompt,
                "runs": report.runs_per_model,
                "results": [r.model_dump() for r in report.results],
            },
        )
    else:
        console.print(report.render_rich_table())
