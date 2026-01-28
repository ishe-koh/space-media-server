from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


def calc_lane_rects(
    screen_width: int,
    screen_height: int,
    cols: int,
    rows: int,
) -> List[Rect]:
    lane_width = screen_width // cols
    lane_height = screen_height // rows

    rects: List[Rect] = []
    for lane_index in range(cols * rows):
        col_index = lane_index % cols
        row_index = lane_index // cols
        rects.append(
            Rect(
                x=col_index * lane_width,
                y=row_index * lane_height,
                width=lane_width,
                height=lane_height,
            )
        )

    return rects
