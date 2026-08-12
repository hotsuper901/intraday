"""Local dev server for the Vercel layout — runs the exact same app Vercel
loads (root main.py). From the repo root:

    DATA_MODE=demo uvicorn main:app --port 8000
    # or:  python dev.py
"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
