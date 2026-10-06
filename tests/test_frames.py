"""frames.py — fixture-based: real small mp4 (spec §7).

Fixture: tests/fixtures/test_video.mp4 — generate ONCE, commit as a
binary test asset (~50KB):
    ffmpeg -f lavfi -i testsrc=duration=3:size=320x240:rate=10 \
           -pix_fmt yuv420p tests/fixtures/test_video.mp4
"""

import pytest

from frames import FrameExtractionError, FramesExtractor


@pytest.fixture
def extractor(cfg):
    return FramesExtractor(cfg.frames)


def test_extract_produces_frames_in_time_order(extractor, cfg, video_dir):
    # out = extractor.extract(video_dir / "test_video.mp4",
    #                        cfg.scratch.root / "job1" / "frames")
    # assert [p for p in out] all exist, suffix == ".jpg", sorted by name == time order
    # for a 3s @ 1fps clip: expect 3 frames
    ...


def test_max_frames_downsamples_not_fails(extractor, tmp_path):
    # cfg with max_frames=2 -> extract -> exactly 2 frames UNIFORMLY spaced
    # (first and last of the sampled range, not the last 2)
    ...


def test_bad_input_raises_frame_extraction_error(extractor, tmp_path, cfg):
    # a file with a .mp4 name but text contents -> FrameExtractionError
    ...


def test_cleanup_removes_dir(extractor, tmp_path):
    # extract -> cleanup -> dir gone
    ...
