"""Frame extraction via ffmpeg subprocess (robust across codecs, runs in
the slim Docker image — ffmpeg is a runtime apt dep).

Contract (spec §3):
- extract_frames() reads the video at <scratch_root>/<job_id>/frames/
  at fps_sample_rate, caps at max_frames by UNIFORM DOWN-SAMPLING
  (never fails on a long video), writes frame_format (default jpg).
- Returns absolute frame paths in chronological order, or [] if none.
- The PIPELINE owns cleanup (finally); this module provides the helper.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from config import FramesConfig


class FrameExtractionError(Exception):
    """ffmpeg missing / unreadable file / decode failure (-> no_frames)."""


class FramesExtractor:
    def __init__(self, cfg: FramesConfig):
        self._cfg = cfg

    def check_available(self) -> None:
        """Boot-time check: shutil.which("ffmpeg") else FrameExtractionError."""
        # pseudocode
        ...

    def extract(
        self,
        video_path: Path,
        output_dir: Path,
    ) -> list[Path]:
        """Write frames and return them in time order.

        Algorithm:
        1. output_dir.mkdir(parents=True, exist_ok=True)
        2. probe duration (ffmpeg -i) so we can cap sample count at max_frames
        3. run: ffmpeg -i <video> -vf fps=<fps> -frames:v <n>
               <output_dir>/frame_%05d.<ext>
        4. collect output_dir.glob(frame_*) sorted
        5. if len > max_frames: keep every kth (uniform), delete the rest,
           return the kept list
        6. on nonzero exit / decode error => FrameExtractionError
           (pipeline maps to failed: no_frames)

        Deterministic, idempotent per job_id (dir is namespaced).
        """
        # pseudocode — algorithm above is the implementation; keep it in one
        # place here, pipeline only calls extract() and cleanup().
        ...

    def cleanup(self, output_dir: Path) -> None:
        """rmtree output_dir's PARENT scratch dir? No — just this dir.
        pipeline.cleanup_job() handles the whole job scratch tree."""
        # pseudocode: shutil.rmtree(output_dir, ignore_errors=True)
        ...
