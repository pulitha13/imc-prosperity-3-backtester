import json
import sys
import re
from pathlib import Path
from typing import Set
import orjson

def get_user_traded_products(input_path: Path) -> Set[str]:
    """Finds products traded by SUBMISSION by reading from the end of the file."""
    products = set()
    with open(input_path, 'rb') as f:
        # Seek toward the end of the file to find Trade History efficiently
        f.seek(0, 2)
        file_size = f.tell()
        # Read the last 5MB, which should cover all trades for a typical day
        seek_pos = max(0, file_size - 5 * 1024 * 1024)
        f.seek(seek_pos)
        chunk = f.read().decode('utf-8', errors='ignore')
        
        if "Trade History:" in chunk:
            trades_section = chunk.split("Trade History:")[1].strip()
            # Clean up non-standard JSON (trailing commas and potential single quotes from str() calls)
            trades_json = re.sub(r',\s*([\]}])', r'\1', trades_section)
            try:
                trades_data = orjson.loads(trades_json)
                for trade in trades_data:
                    if trade.get("buyer") == "SUBMISSION" or trade.get("seller") == "SUBMISSION":
                        products.add(trade.get("symbol"))
            except Exception as e:
                # Fallback to regex if JSON parsing fails on the chunk
                matches = re.findall(r'"symbol":\s*"([^"]+)"', trades_section)
                # This is a bit broad, so we check for SUBMISSION nearby
                trade_blocks = re.findall(r'\{[^{}]*?SUBMISSION[^{}]*?\}', trades_section)
                for block in trade_blocks:
                    sym_match = re.search(r'"symbol":\s*"([^"]+)"', block)
                    if sym_match:
                        products.add(sym_match.group(1))
    return products

def prune_lambda_data(data, products):
    """Recursively prunes the visualizer state data."""
    if isinstance(data, list):
        if len(data) > 0 and isinstance(data[0], list) and len(data[0]) == 3 and isinstance(data[0][0], str):
            # Found listings: [[symbol, name, 1], ...]
            return [x for x in data if x[0] in products]
        return [prune_lambda_data(x, products) for x in data]
    
    if isinstance(data, dict):
        return {k: prune_lambda_data(v, products) for k, v in data.items() 
                if k in products or not isinstance(v, (dict, list))}
    
    if isinstance(data, str) and data:
        # Prune log lines within lambdaLog
        lines = data.split('\n')
        return "\n".join([l for l in lines if any(p in l for p in products) or not l.strip()])
        
    return data

def prune_log(input_path: Path) -> Path:
    output_path = input_path.with_name(f"{input_path.stem}-pruned{input_path.suffix}")
    
    user_traded_products = get_user_traded_products(input_path)
    if not user_traded_products:
        print("No user trades found. Keeping all products.")
        # We need a list of all products to avoid pruning everything
        # For simplicity, if no trades, just return input_path or copy
        return input_path

    print(f"User traded products: {user_traded_products}")

    with open(input_path, 'r', encoding='utf-8') as fin, \
         open(output_path, 'wb') as fout:
        
        section = "header"
        buffer = []
        
        for line in fin:
            line_strip = line.strip()
            
            if line_strip == "Sandbox logs:":
                section = "sandbox"
                fout.write(line.encode('utf-8'))
                continue
            elif line_strip == "Activities log:":
                section = "activities"
                fout.write(line.encode('utf-8'))
                continue
            elif line_strip == "Trade History:":
                section = "trades"
                fout.write(line.encode('utf-8'))
                break # We'll handle trades at the end

            if section == "sandbox":
                if line_strip == "{":
                    buffer = [line]
                elif line_strip == "}":
                    buffer.append(line)
                    try:
                        obj = orjson.loads("".join(buffer))
                        
                        # Prune sandboxLog
                        if obj.get("sandboxLog"):
                            sb_lines = obj["sandboxLog"].strip().split('\n')
                            obj["sandboxLog"] = "\n".join([l for l in sb_lines if any(p in l for p in user_traded_products)])
                        
                        # Prune lambdaLog
                        if obj.get("lambdaLog"):
                            l_log = obj["lambdaLog"].strip()
                            if l_log.startswith("[["):
                                try:
                                    l_data = orjson.loads(l_log)
                                    l_data = prune_lambda_data(l_data, user_traded_products)
                                    obj["lambdaLog"] = orjson.dumps(l_data).decode('utf-8')
                                except:
                                    pass # Keep original if parse fails
                            else:
                                lines = l_log.split('\n')
                                obj["lambdaLog"] = "\n".join([l for l in lines if any(p in l for p in user_traded_products)])
                        
                        # Write pruned object (indent=2)
                        fout.write(orjson.dumps(obj, option=orjson.OPT_INDENT_2))
                        fout.write(b"\n")
                    except:
                        fout.write("".join(buffer).encode('utf-8'))
                    buffer = []
                elif buffer:
                    buffer.append(line)
                else:
                    fout.write(line.encode('utf-8'))
                    
            elif section == "activities":
                parts = line_strip.split(';')
                if len(parts) > 2:
                    product = parts[2]
                    # Index 2 is the product column. Header check:
                    if product == "product" or product in user_traded_products:
                        fout.write(line.encode('utf-8'))
                else:
                    fout.write(line.encode('utf-8'))
            else:
                fout.write(line.encode('utf-8'))

        # Handle Trade History
        if section == "trades":
            fout.write(b"[\n")
            # We already identified user_traded_products, but we need to write the actual trade rows
            # Read from the end again to get the full list
            with open(input_path, 'rb') as f:
                f.seek(seek_pos if 'seek_pos' in locals() else 0)
                chunk = f.read().decode('utf-8', errors='ignore')
                if "Trade History:" in chunk:
                    trades_json = re.sub(r',\s*([\]}])', r'\1', chunk.split("Trade History:")[1].strip())
                    try:
                        all_trades = orjson.loads(trades_json)
                        pruned_trades = [t for t in all_trades if t.get("symbol") in user_traded_products]
                        trade_lines = [orjson.dumps(t, option=orjson.OPT_INDENT_2).decode('utf-8').replace('\n', '\n  ') 
                                       for t in pruned_trades]
                        fout.write(",\n".join(trade_lines).encode('utf-8'))
                    except:
                        pass
            fout.write(b"\n]")

    print(f"Pruned log saved to {output_path}")
    return output_path

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python prune_log.py <logfile>")
        sys.exit(1)
    
    log_file = Path(sys.argv[1])
    if not log_file.exists():
        print(f"File {log_file} does not exist.")
        sys.exit(1)
        
    prune_log(log_file)
