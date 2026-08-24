import sys
from pathlib import Path

# Make `import contracts.schemas` work no matter where pytest is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
