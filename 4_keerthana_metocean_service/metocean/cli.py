import argparse

def main():
    parser = argparse.ArgumentParser(description="Fetch Metocean Datasets")
    parser.add_argument("--bbox", required=True)
    parser.add_argument("--time-range", required=True)
    args = parser.parse_args()
    print(f"Fetching metocean data for {args.bbox} over {args.time_range}")

if __name__ == "__main__":
    main()
