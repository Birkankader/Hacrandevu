#!/usr/bin/env python3
"""HacettepeBot web sunucusu entry point."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
