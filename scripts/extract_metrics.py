import argparse
import json
import os

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-log", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    # Basic metric extraction
    log_content = ""
    if os.path.exists(args.agent_log):
        with open(args.agent_log, "r") as f:
            log_content = f.read()

    metrics = {
        "iterations": log_content.count("--- Iteration"),
        "status": "completed" if "Fix successful" in log_content else "failed",
    }

    with open(args.output, "w") as f:
        json.dump(metrics, f, indent=2)
    
    print(f"Metrics saved to {args.output}")

if __name__ == "__main__":
    main()
