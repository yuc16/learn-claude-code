import os
import subprocess

print(os.getcwd())

r = subprocess.run(
    "which python",
    shell=True,
    cwd=os.getcwd(),
    capture_output=True,
    text=True,
    timeout=120,
)
out = (r.stdout + r.stderr).strip()

print(out)
