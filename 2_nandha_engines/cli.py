import argparse
import sys

def main():
    parser = argparse.ArgumentParser(description="oceantrace Nandha Engines Command-Line Tool")
    parser.add_argument("engine", choices=["characterise", "drift", "attribution"], help="Engine to execute")
    args = parser.parse_args()

    print(f"Running engine: {args.engine}")
    # Sub-engine routing logic
    sys.exit(0)

if __name__ == "__main__":
    main()
