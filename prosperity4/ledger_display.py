"""
Print a summary table of ledger.jsonl.

Entry point: `uv run ledger`
Also called by prosperity4 after --post.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

_G = "\033[32m"
_R = "\033[31m"
_B = "\033[1m"
_D = "\033[2m"
_X = "\033[0m"


def _c(s: str, code: str) -> str:
    return f"{code}{s}{_X}" if sys.stdout.isatty() else s


def _pnl(v: int) -> str:
    return _c(f"{v:>+,}", _G if v >= 0 else _R)


def _ts(s: str) -> str:
    try:
        return datetime.fromisoformat(s).astimezone().strftime("%m/%d %H:%M")
    except Exception:
        return s[:16]


def _ref(git: dict) -> str:
    h = git.get("short_hash", "?")
    return f"{git.get('branch','?')}@{h}{'*' if git.get('dirty') else ''}"


def _days(entry: dict) -> str:
    rounds: dict = {}
    for d in entry.get("days", []):
        rounds.setdefault(d["round"], []).append(d["day"])
    return "  ".join(f"r{r}:{','.join(str(d) for d in sorted(ds))}" for r, ds in sorted(rounds.items())) or "—"


def print_ledger(ledger_path: Path, n: int = 8, new_index: int = -1) -> None:
    if not ledger_path.exists():
        print("No ledger found.")
        return

    entries = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    if not entries:
        print("Ledger is empty.")
        return

    total = len(entries)
    shown = entries[-n:]
    best_i = max(range(total), key=lambda i: entries[i].get("total_pnl", float("-inf")))

    idx_w = len(str(total - 1))
    W = {"ts": 11, "ref": 22, "alg": 14, "days": 16, "pnl": 10}
    sep = "  "

    def row(idx, ts, ref, alg, days, pnl):
        return (
            f"{idx:<{idx_w}}{sep}"
            f"{ts:<{W['ts']}}{sep}"
            f"{ref:<{W['ref']}}{sep}"
            f"{alg:<{W['alg']}}{sep}"
            f"{days:<{W['days']}}{sep}"
            f"{pnl:>{W['pnl']}}"
        )

    header = row("#", "Date", "Branch@Hash", "Algorithm", "Days", "Total PnL")
    rule = "─" * len(header)

    title = "Backtest Ledger"
    if total > n:
        title += _c(f"  (last {n} of {total})", _D)

    print()
    print(_c(title, _B))
    print(rule)
    print(_c(header, _D))
    print(rule)

    offset = total - len(shown)
    for i, e in enumerate(shown):
        abs_i = offset + i
        alg = (e.get("algorithm") or "?")[-W["alg"]:]
        ref = _ref(e.get("git", {}))[:W["ref"]]
        days = _days(e)[:W["days"]]
        pnl_val = e.get("total_pnl", 0)

        tag = ""
        if abs_i == new_index:
            tag = _c("  ← new", _G)
        elif abs_i == best_i and abs_i != new_index:
            tag = _c("  ★ best", _G)

        note = e.get("note", "")
        note_str = _c(f"  [{note}]", _D) if note else ""

        print(row(abs_i, _ts(e.get("timestamp", "")), ref, alg, days, _pnl(pnl_val)) + tag + note_str)

    print(rule)
    best = entries[best_i]
    print(f"Best  {_pnl(best.get('total_pnl', 0))}  {_c(_ref(best.get('git', {})), _D)}  {_c(_ts(best.get('timestamp', '')), _D)}")
    print()


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Show backtest ledger.")
    p.add_argument("--ledger", type=Path, default=None)
    p.add_argument("-n", type=int, default=8)
    p.add_argument("--delete", type=int, default=None, metavar="N",
                   help="Delete entry at index N (0-based, negatives count from end). E.g. --delete -1 removes the last entry.")
    args = p.parse_args()

    if args.ledger:
        ledger_path = args.ledger
    else:
        ledger_path = None
        for cand in [Path.cwd(), *Path.cwd().parents]:
            if (cand / "ledger.jsonl").exists():
                ledger_path = cand / "ledger.jsonl"
                break
        ledger_path = ledger_path or Path.cwd() / "ledger.jsonl"

    if args.delete is not None:
        lines = [l for l in ledger_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        idx = args.delete
        if idx < -len(lines) or idx >= len(lines):
            print(f"Error: index {idx} out of range (ledger has {len(lines)} entries)")
            sys.exit(1)
        del lines[idx]
        ledger_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        print(f"Deleted entry {idx}.")

    print_ledger(ledger_path, n=args.n)


if __name__ == "__main__":
    main()
