"""Shared limit on heavy background work (analysis, narration, dubbing, library building) so the GPU is not overloaded."""
import os
import threading

HEAVY = threading.Semaphore(int(os.environ.get("CINECUT_WEB_JOBS", "2")))
