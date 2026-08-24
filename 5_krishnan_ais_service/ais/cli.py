import argparse

def main():
    parser = argparse.ArgumentParser(description="AIS Service CLI")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--generate", action="store_true")
    args = parser.parse_args()
    if args.fetch:
         print("Fetching AIS data...")
    if args.generate:
         print("Generating synthetic AIS coordinates...")

if __name__ == "__main__":
    main()
