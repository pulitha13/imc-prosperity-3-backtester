import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from functools import reduce
from importlib import import_module, metadata, reload
from pathlib import Path
from typing import Annotated, Any, List, Optional

from typer import Argument, Option, Typer

from prosperity4.data import has_day_data
from prosperity4.file_reader import FileReader, FileSystemReader, PackageResourcesReader
from prosperity4.models import BacktestResult, TradeMatchingMode
from prosperity4.ledger_display import print_ledger
from prosperity4.open import open_visualizer
from prosperity4.runner import run_backtest

DIST_NAME = "prosperity3bt"


def parse_algorithm(algorithm: Path) -> Any:
    sys.path.append(str(algorithm.parent))
    return import_module(algorithm.stem)


def parse_data(data_root: Optional[Path]) -> FileReader:
    if data_root is not None:
        return FileSystemReader(data_root)
    else:
        return PackageResourcesReader()


def parse_days(file_reader: FileReader, days: list[str]) -> list[tuple[int, int]]:
    parsed_days = []

    for arg in days:
        if "-" in arg:
            round_num, day_num = map(int, arg.split("-", 1))

            if not has_day_data(file_reader, round_num, day_num):
                print(f"Warning: no data found for round {round_num} day {day_num}")
                continue

            parsed_days.append((round_num, day_num))
        else:
            round_num = int(arg)

            parsed_days_in_round = []
            for day_num in range(-5, 100):
                if has_day_data(file_reader, round_num, day_num):
                    parsed_days_in_round.append((round_num, day_num))

            if len(parsed_days_in_round) == 0:
                print(f"Warning: no data found for round {round_num}")
                continue

            parsed_days.extend(parsed_days_in_round)

    if len(parsed_days) == 0:
        print("Error: did not find data for any requested round/day")
        sys.exit(1)

    return parsed_days

def parse_out(out: Optional[Path], no_out: bool, csv: bool) -> Optional[Path]:

    if out is not None:
        return out

    if no_out:
        return None

    if csv:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Return a directory path instead of a .log file
        return Path.cwd() / "backtests" / timestamp

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return Path.cwd() / "backtests" / f"{timestamp}.log"

def print_day_summary(result: BacktestResult) -> None:
    last_timestamp = result.activity_logs[-1].timestamp

    product_lines = []
    total_profit = 0

    for row in reversed(result.activity_logs):
        if row.timestamp != last_timestamp:
            break

        product = row.columns[2]
        profit = row.columns[-1]

        product_lines.append(f"{product}: {profit:,.0f}")
        total_profit += profit

    print(*reversed(product_lines), sep="\n")
    print(f"Total profit: {total_profit:,.0f}")


def merge_results(
    a: BacktestResult, b: BacktestResult, merge_profit_loss: bool, merge_timestamps: bool
) -> BacktestResult:
    sandbox_logs = a.sandbox_logs[:]
    activity_logs = a.activity_logs[:]
    trades = a.trades[:]

    if merge_timestamps:
        a_last_timestamp = a.activity_logs[-1].timestamp
        timestamp_offset = a_last_timestamp + 100
    else:
        timestamp_offset = 0

    sandbox_logs.extend([row.with_offset(timestamp_offset) for row in b.sandbox_logs])
    trades.extend([row.with_offset(timestamp_offset) for row in b.trades])

    if merge_profit_loss:
        profit_loss_offsets = defaultdict(float)
        for row in reversed(a.activity_logs):
            if row.timestamp != a_last_timestamp:
                break

            profit_loss_offsets[row.columns[2]] = row.columns[-1]

        activity_logs.extend(
            [row.with_offset(timestamp_offset, profit_loss_offsets[row.columns[2]]) for row in b.activity_logs]
        )
    else:
        activity_logs.extend([row.with_offset(timestamp_offset, 0) for row in b.activity_logs])

    return BacktestResult(a.round_num, a.day_num, sandbox_logs, activity_logs, trades)


def write_output_json(output_file: Path, merged_results: BacktestResult) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w+", encoding="utf-8") as file:
        file.write("Sandbox logs:\n")
        for row in merged_results.sandbox_logs:
            file.write(str(row))

        file.write("\n\n\nActivities log:\n")
        file.write(
            "day;timestamp;product;bid_price_1;bid_volume_1;bid_price_2;bid_volume_2;bid_price_3;bid_volume_3;ask_price_1;ask_volume_1;ask_price_2;ask_volume_2;ask_price_3;ask_volume_3;mid_price;profit_and_loss\n"
        )
        file.write("\n".join(map(str, merged_results.activity_logs)))
        file.write("\n\n\n\n\nTrade History:\n")
        file.write("[\n")
        file.write(",\n".join(map(str, merged_results.trades)))
        file.write("]")

def write_output_csv(output_base: Path, results: list[BacktestResult]) -> None:
    if not results:
        return

    r_num = results[0].round_num
    # 1. Create the nested directory (e.g., backtests/2026-04-10_18-43-00/r0/)
    output_dir = output_base / f"r{r_num}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 2. Write the Sandbox log text file (combined for all days)
    sandbox_file = output_dir / "sandbox.log"
    with sandbox_file.open("w+", encoding="utf-8") as s_file:
        for result in results:
            s_file.write(f"--- Round {result.round_num} Day {result.day_num} ---\n")
            for row in result.sandbox_logs:
                s_file.write(str(row))

    # 3. Write separate CSVs for each day
    for result in results:
        r_num = result.round_num
        d_num = result.day_num

        prices_file = output_dir / f"prices_round_{r_num}_day_{d_num}.csv"
        with prices_file.open("w+", encoding="utf-8") as file:
            file.write("day;timestamp;product;bid_price_1;bid_volume_1;bid_price_2;bid_volume_2;bid_price_3;bid_volume_3;ask_price_1;ask_volume_1;ask_price_2;ask_volume_2;ask_price_3;ask_volume_3;mid_price;profit_and_loss\n")
            file.write("\n".join(map(str, result.activity_logs)))

        trades_file = output_dir / f"trades_round_{r_num}_day_{d_num}.csv"
        with trades_file.open("w+", encoding="utf-8") as file:
            file.write("timestamp;buyer;seller;symbol;currency;price;quantity\n")
            for trade in result.trades:
                try:
                    t_dict = json.loads(str(trade).replace("'", '"'))
                    row = f"{t_dict.get('timestamp', '')};{t_dict.get('buyer', '')};{t_dict.get('seller', '')};{t_dict.get('symbol', '')};{t_dict.get('currency', '')};{t_dict.get('price', '')};{t_dict.get('quantity', '')}"
                    file.write(row + "\n")
                except Exception:
                    pass

def print_overall_summary(results: list[BacktestResult]) -> None:
    print("Profit summary:")

    total_profit = 0
    for result in results:
        last_timestamp = result.activity_logs[-1].timestamp

        profit = 0
        for row in reversed(result.activity_logs):
            if row.timestamp != last_timestamp:
                break

            profit += row.columns[-1]

        print(f"Round {result.round_num} day {result.day_num}: {profit:,.0f}")
        total_profit += profit

    print(f"Total profit: {total_profit:,.0f}")


def format_path(path: Path) -> str:
    cwd = Path.cwd()
    if path.is_relative_to(cwd):
        return str(path.relative_to(cwd))
    else:
        return str(path)


def find_git_root(path: Path) -> Path:
    """Walk up from path to find the nearest .git directory."""
    for candidate in [path] + list(path.parents):
        if (candidate / ".git").exists():
            return candidate
    return path


def get_git_info(repo_root: Path) -> dict:
    def run(cmd: List[str]) -> str:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=repo_root)
        return r.stdout.strip()

    return {
        "hash": run(["git", "rev-parse", "HEAD"]),
        "short_hash": run(["git", "rev-parse", "--short", "HEAD"]),
        "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "dirty": bool(run(["git", "status", "--porcelain"])),
        "message": run(["git", "log", "-1", "--format=%s"]),
    }


def extract_pnl(results: List[BacktestResult]) -> dict:
    """Return per-day PnL keyed by 'r<round>_d<day>'."""
    per_day = {}
    for result in results:
        last_ts = result.activity_logs[-1].timestamp
        products: dict = {}
        for row in reversed(result.activity_logs):
            if row.timestamp != last_ts:
                break
            products[row.columns[2]] = round(row.columns[-1])
        key = f"r{result.round_num}_d{result.day_num}"
        per_day[key] = {
            "round": result.round_num,
            "day": result.day_num,
            "products": products,
            "total": sum(products.values()),
        }
    return per_day


def write_ledger_entry(algorithm: Path, results: List[BacktestResult]) -> None:
    repo_root = find_git_root(algorithm.parent)
    ledger_path = repo_root / "ledger.jsonl"

    per_day = extract_pnl(results)
    total_pnl = sum(v["total"] for v in per_day.values())

    try:
        alg_str = str(algorithm.relative_to(repo_root))
    except ValueError:
        alg_str = str(algorithm)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git": get_git_info(repo_root),
        "algorithm": alg_str,
        "days": [{"round": v["round"], "day": v["day"]} for v in per_day.values()],
        "pnl": per_day,
        "total_pnl": total_pnl,
    }

    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    new_index = sum(1 for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()) - 1
    print_ledger(ledger_path, new_index=new_index)


def version_callback(value: bool) -> None:
    if value:
        print(f"prosperity4 {metadata.version(DIST_NAME)}")
        sys.exit(0)


app = Typer(context_settings={"help_option_names": ["--help", "-h"]})


@app.command()
def cli(
    algorithm: Annotated[Path, Argument(help="Path to the Python file containing the algorithm to backtest.", show_default=False, exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    days: Annotated[list[str], Argument(help="The days to backtest on. <round>-<day> for a single day, <round> for all days in a round.", show_default=False)],
    merge_pnl: Annotated[bool, Option("--merge-pnl", help="Merge profit and loss across days.")] = False,
    vis: Annotated[bool, Option("--vis", help="Open backtest results in https://jmerle.github.io/imc-prosperity-3-visualizer/ when done.")] = False,
    out: Annotated[Optional[Path], Option(help="File to save output log to (defaults to backtests/<timestamp>.log).", show_default=False, dir_okay=False, resolve_path=True)] = None,
    no_out: Annotated[bool, Option("--no-out", help="Skip saving output log.")] = False,
    csv: Annotated[bool, Option("--csv", help="Save output log to csv files instead of json (defaults to backtests/<timestamp>/)")] = False,
    data: Annotated[Optional[Path], Option(help="Path to data directory. Must look similar in structure to https://github.com/jmerle/imc-prosperity-3-backtester/tree/master/prosperity4/resources.", show_default=False, exists=True, file_okay=False, dir_okay=True, resolve_path=True)] = None,
    print_output: Annotated[bool, Option("--print", help="Print the trader's output to stdout while it's running.")] = False,
    match_trades: Annotated[TradeMatchingMode, Option(help="How to match orders against market trades. 'all' matches trades with prices equal to or worse than your quotes, 'worse' matches trades with prices worse than your quotes, 'none' does not match trades against orders at all.")] = TradeMatchingMode.all,
    no_progress: Annotated[bool, Option("--no-progress", help="Don't show progress bars.")] = False,
    original_timestamps: Annotated[bool, Option("--original-timestamps", help="Preserve original timestamps in output log rather than making them increase across days.")] = False,
    post: Annotated[bool, Option("--post", help="Record backtest results (git info + per-product PnL) to ledger.jsonl in the algorithm's repo root.")] = False,
    version: Annotated[bool, Option("--version", "-v", help="Show the program's version number and exit.", is_eager=True, callback=version_callback)] = False,
) -> None:  # fmt: skip
    if out is not None and no_out:
        print("Error: --out and --no-out are mutually exclusive")
        sys.exit(1)

    try:
        trader_module = parse_algorithm(algorithm)
    except ModuleNotFoundError as e:
        print(f"{algorithm} is not a valid algorithm file: {e}")
        sys.exit(1)

    if not hasattr(trader_module, "Trader"):
        print(f"{algorithm} does not expose a Trader class")
        sys.exit(1)

    file_reader = parse_data(data)
    parsed_days = parse_days(file_reader, days)
    output_file = parse_out(out, no_out, csv)

    show_progress_bars = not no_progress and not print_output

    results = []
    for round_num, day_num in parsed_days:
        print(f"Backtesting {algorithm} on round {round_num} day {day_num}")

        reload(trader_module)

        result = run_backtest(
            trader_module.Trader(),
            file_reader,
            round_num,
            day_num,
            print_output,
            match_trades,
            True,
            show_progress_bars,
        )

        print_day_summary(result)
        if len(parsed_days) > 1:
            print()

        results.append(result)

    if len(parsed_days) > 1:
        print_overall_summary(results)

    if output_file is not None:
        if csv:
            write_output_csv(output_file, results)
            print(f"\nSuccessfully saved backtest results to {format_path(output_file)}")
        else:
            merged_results = reduce(lambda a, b: merge_results(a, b, merge_pnl, not original_timestamps), results)
            write_output_json(output_file, merged_results)

    if vis and output_file is not None:
        open_visualizer(output_file)

    if post:
        write_ledger_entry(algorithm, results)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
