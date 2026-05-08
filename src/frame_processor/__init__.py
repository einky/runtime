"""Frame processor: capture Xvfb -> resize -> greyscale -> dither -> dispatch."""

from frame_processor.dither import floyd_steinberg
from frame_processor.processor import FrameProcessor

__all__ = ["FrameProcessor", "floyd_steinberg"]
