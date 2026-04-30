import sys
import re
from pathlib import Path
from typing import Set, Tuple
import orjson

def get_user_traded_products(input_path: Path) -> Tuple[Set[str], int]:
    """Finds products traded by SUBMISSION and the position of Trade History section."""
    products = set()
    marker = b"Trade History:"
    found_pos = -1
    
    input_path = input_path.resolve()
    
    try:
        if not input_path.exists():
            return set(), -1
            
        file_size = input_path.stat().st_size
        if file_size == 0:
            return set(), -1

        with open(input_path, 'rb') as f:
            # Search backward in 10MB chunks, up to 300MB back
            chunk_size = 10 * 1024 * 1024
            
            for i in range(1, 31):
                pos = max(0, file_size - i * chunk_size)
                f.seek(pos)
                chunk = f.read(chunk_size + len(marker))
                
                idx = chunk.rfind(marker)
                if idx != -1:
                    found_pos = pos + idx
                    break
                if pos == 0:
                    break
            
            # If not found in backward search, do a full scan (literal search is fast)
            if found_pos == -1:
                f.seek(0)
                while True:
                    curr_pos = f.tell()
                    chunk = f.read(chunk_size + len(marker))
                    if not chunk:
                        break
                    idx = chunk.find(marker)
                    if idx != -1:
                        found_pos = curr_pos + idx
                        break
                    if len(chunk) < chunk_size:
                        break
                    f.seek(curr_pos + chunk_size)

            if found_pos != -1:
                f.seek(found_pos + len(marker))
                trades_section = f.read().decode('utf-8', errors='ignore')
                
                # Robustly find symbols traded by SUBMISSION using regex
                # Look for "buyer": "SUBMISSION" or "seller": "SUBMISSION"
                trade_blocks = re.finditer(r'\{[^{}]*?"(?:buyer|seller)"\s*:\s*"SUBMISSION"[^{}]*?\}', trades_section, re.DOTALL)
                for match in trade_blocks:
                    block = match.group(0)
                    sym_match = re.search(r'"symbol"\s*:\s*"([^"]+)"', block)
                    if sym_match:
                        products.add(sym_match.group(1))
    except Exception:
        pass
                
    return products, found_pos

def prune_lambda_data(data, products):
    """Recursively prunes the visualizer state data."""
    if isinstance(data, list):
        if len(data) > 0 and isinstance(data[0], list) and len(data[0]) > 0 and isinstance(data[0][0], str):
            # Found listings: [[symbol, name, 1], ...] or trades: [[symbol, price, qty, buyer, seller, ts], ...]
            if len(data[0]) in (3, 6):
                return [x for x in data if x[0] in products]
        return [prune_lambda_data(x, products) for x in data]
    
    if isinstance(data, dict):
        return {k: prune_lambda_data(v, products) for k, v in data.items() 
                if k in products or not isinstance(v, (dict, list))}
    
    if isinstance(data, str) and '\n' in data:
        # Prune multiline log lines within lambdaLog
        lines = data.split('\n')
        return "\n".join([l for l in lines if any(p in l for p in products) or not l.strip()])
        
    return data

def prune_log(input_path: Path) -> Path:
    input_path = input_path.resolve()
    output_path = input_path.with_name(f"{input_path.stem}-pruned{input_path.suffix}")
    
    user_traded_products, trades_marker_pos = get_user_traded_products(input_path)
    if not user_traded_products:
        print("No user trades found. Keeping all products.")
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
                            if l_log.startswith("[[") or l_log.startswith("{"):
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
        if section == "trades" and trades_marker_pos != -1:
            fout.write(b"[\n")
            with open(input_path, 'rb') as f:
                f.seek(trades_marker_pos + len(b"Trade History:"))
                trades_section = f.read().decode('utf-8', errors='ignore').strip()
                
                # Clean up potential trailing commas and wrap in brackets if needed
                trades_json = trades_section
                if not trades_json.startswith("["):
                    trades_json = "[" + trades_json
                if not trades_json.endswith("]"):
                    trades_json = trades_json + "]"
                
                # Fix trailing commas before closing brackets
                trades_json = re.sub(r',\s*([\]}])', r'\1', trades_json)
                
                try:
                    all_trades = orjson.loads(trades_json)
                    pruned_trades = [t for t in all_trades if t.get("symbol") in user_traded_products]
                    
                    # Write trades one by one to be more memory efficient than join()
                    for i, t in enumerate(pruned_trades):
                        trade_str = orjson.dumps(t, option=orjson.OPT_INDENT_2).decode('utf-8')
                        # Add indentation to match original log format
                        indented_trade = "  " + trade_str.replace("\n", "\n  ")
                        fout.write(indented_trade.encode('utf-8'))
                        if i < len(pruned_trades) - 1:
                            fout.write(b",\n")
                        else:
                            fout.write(b"\n")
                except Exception:
                    # Fallback: just write the original if pruning fails
                    fout.write(trades_section.encode('utf-8'))
            fout.write(b"]")

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
