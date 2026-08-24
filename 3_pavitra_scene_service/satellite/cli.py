import argparse

def main():
    parser = argparse.ArgumentParser(description="Fetch Satellite Scenes")
    parser.add_argument("--scene-id", required=True, help="Sentinel-1 scene ID")
    args = parser.parse_args()
    print(f"Fetching scene: {args.scene_id}")

if __name__ == "__main__":
    main()
