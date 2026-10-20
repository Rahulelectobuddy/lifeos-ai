import json
from pathlib import Path


def main():
    for file in Path(__file__).parent.glob("*.json"):
        cases = json.loads(file.read_text())
        assert cases and len({case["id"] for case in cases}) == len(cases)
        for case in cases:
            assert case["request"] and case["checks"] and case["expected"]
        print(f"{file.name}: {len(cases)} evaluation scenarios")


if __name__ == "__main__":
    main()
