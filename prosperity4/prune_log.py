import json
import sys
import re
from pathlib import Path

def prune_log(input_path: Path) -> Path:
    output_path = input_path.with_name(f"{input_path.stem}-pruned{input_path.suffix}")
    
    with open(input_path, 'r') as f:
        lines = f.readlines()

    # Find section indices
    sandbox_start = -1
    activities_start = -1
    trades_start = -1

    for i, line in enumerate(lines):
        if line.strip() == "Sandbox logs:":
            sandbox_start = i
        elif line.strip() == "Activities log:":
            activities_start = i
        elif line.strip() == "Trade History:":
            trades_start = i

    if any(idx == -1 for idx in [sandbox_start, activities_start, trades_start]):
        print("Could not find all sections in the log file.")
        return input_path

    # Extract sections
    sandbox_lines = lines[sandbox_start:activities_start]
    activities_lines = lines[activities_start:trades_start]
    trades_lines = lines[trades_start:]

    # Step 1: Identify user-traded products from Trade History
    trades_json_str = "".join(trades_lines[1:]).strip()
    
    # Handle non-standard JSON with trailing commas
    trades_json_str = re.sub(r',\s*([\]}])', r'\1', trades_json_str)
    
    try:
        trades_data = json.loads(trades_json_str)
    except json.JSONDecodeError as e:
        print(f"Error decoding Trade History JSON: {e}")
        return input_path

    user_traded_products = set()
    for trade in trades_data:
        if trade.get("buyer") == "SUBMISSION" or trade.get("seller") == "SUBMISSION":
            user_traded_products.add(trade.get("symbol"))

    print(f"User traded products: {user_traded_products}")

    # Step 2: Prune Trade History
    pruned_trades_data = [t for t in trades_data if t.get("symbol") in user_traded_products]
    
    # Step 3: Prune Sandbox Logs
    def prune_lambda_data(data, products):
        if isinstance(data, list):
            # Check if it's a listing: [[symbol, name, 1], ...]
            if len(data) > 0 and isinstance(data[0], list) and len(data[0]) == 3 and isinstance(data[0][2], int):
                return [item for item in data if item[0] in products]
            
            # Recursively prune list items
            return [prune_lambda_data(item, products) for item in data]
        
        if isinstance(data, dict):
            # Prune dictionary keys (Order Depths, Position, Market Trades)
            return {k: prune_lambda_data(v, products) for k, v in data.items() if k in products or not isinstance(v, (dict, list))}
        
        if isinstance(data, str):
            # Prune log lines
            log_lines = data.split('\n')
            return "\n".join([line for line in log_lines if any(p in line for p in products) or not line.strip()])
            
        return data

    pruned_sandbox_lines = [sandbox_lines[0]] # "Sandbox logs:"
    sandbox_content = "".join(sandbox_lines[1:])
    
    # Simple JSON object extractor that handles nested braces
    def extract_json_objects(text):
        objs = []
        start = -1
        depth = 0
        for i, char in enumerate(text):
            if char == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0 and start != -1:
                    objs.append(text[start:i+1])
                    start = -1
        return objs

    json_objects = extract_json_objects(sandbox_content)
    
    for obj_str in json_objects:
        try:
            obj = json.loads(obj_str)
            
            # Prune sandboxLog (limit warnings)
            if obj.get("sandboxLog"):
                sb_lines = obj["sandboxLog"].strip().split('\n')
                obj["sandboxLog"] = "\n".join([line for line in sb_lines if any(p in line for p in user_traded_products)])

            # Prune lambdaLog
            if obj.get("lambdaLog"):
                l_log = obj["lambdaLog"].strip()
                if l_log.startswith("[["):
                    try:
                        l_data = json.loads(l_log)
                        l_data = prune_lambda_data(l_data, user_traded_products)
                        obj["lambdaLog"] = json.dumps(l_data)
                    except json.JSONDecodeError:
                        log_lines = l_log.split('\n')
                        obj["lambdaLog"] = "\n".join([line for line in log_lines if any(p in line for p in user_traded_products)])
                else:
                    log_lines = l_log.split('\n')
                    obj["lambdaLog"] = "\n".join([line for line in log_lines if any(p in line for p in user_traded_products)])
            
            pruned_sandbox_lines.append(json.dumps(obj, indent=2) + "\n")
        except json.JSONDecodeError:
            pruned_sandbox_lines.append(obj_str + "\n")

    # Step 4: Prune Activities Log
    pruned_activities_lines = [activities_lines[0], activities_lines[1]]
    for line in activities_lines[2:]:
        if not line.strip():
            continue
        parts = line.split(';')
        if len(parts) > 2:
            product = parts[2]
            if product in user_traded_products:
                pruned_activities_lines.append(line)

    # Step 5: Reassemble and write
    with open(output_path, 'w') as f:
        for line in pruned_sandbox_lines:
            f.write(line)
        
        if not pruned_sandbox_lines[-1].endswith('\n'):
            f.write('\n')
        for line in pruned_activities_lines:
            f.write(line)
        
        f.write("\n\n\n\n\nTrade History:\n")
        f.write("[\n")
        f.write(",\n".join(json.dumps(t, indent=2).replace('\n', '\n  ') for t in pruned_trades_data))
        f.write("\n]")

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
