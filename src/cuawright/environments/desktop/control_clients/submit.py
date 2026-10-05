import argparse
import json

parser = argparse.ArgumentParser()
parser.parse_args()
print(json.dumps({"cuawright_control": 1, "operation": "submit"}))
