"""Generate an additional ChArUco calibration board at a chosen paper size.

Companion to scripts/generate_charuco_board.py (the canonical A0 / 75 mm board).
This produces a second, more portable board on A4/A3/A2/A1/A0, keeping the same
marker dictionary and square count so it stays compatible with the DRS
calibrator. The square size auto-fits the chosen paper (or pass --square).

Note: for intrinsic camera calibration the absolute square size does not change
the camera matrix; just record the printed square size for pose/extrinsics.
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import cv2
from PIL import Image
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

DICTIONARY_NAME = "DICT_5X5_1000"
SQUARES_X = 10
SQUARES_Y = 7
MARKER_RATIO = 11.0 / 15.0  # 55 / 75, the canonical marker-to-square ratio
TARGET_DPI = 600

# Paper sizes in millimetres as (long_edge, short_edge).
PAPER_SIZES = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}


def fit_square_mm(paper: str, margin_mm: float) -> float:
    long_edge, short_edge = PAPER_SIZES[paper]
    by_width = (long_edge - 2 * margin_mm) / SQUARES_X
    by_height = (short_edge - 2 * margin_mm) / SQUARES_Y
    return float(int(min(by_width, by_height)))  # whole millimetres


def create_board(square_mm: float, marker_mm: float) -> cv2.aruco.CharucoBoard:
    if not hasattr(cv2, "aruco"):
        raise RuntimeError("OpenCV lacks the ArUco module. Install opencv-contrib-python>=4.9.")
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, DICTIONARY_NAME))
    return cv2.aruco.CharucoBoard((SQUARES_X, SQUARES_Y), square_mm, marker_mm, dictionary)


def render_png(board: cv2.aruco.CharucoBoard, square_mm: float, png_path: Path) -> tuple[int, int]:
    pixels_per_square = max(300, round(square_mm / 25.4 * TARGET_DPI))
    width_px = SQUARES_X * pixels_per_square
    height_px = SQUARES_Y * pixels_per_square
    image = board.generateImage((width_px, height_px), marginSize=0, borderBits=1)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(png_path), image, [cv2.IMWRITE_PNG_COMPRESSION, 9]):
        raise OSError(f"OpenCV could not write PNG: {png_path}")
    return width_px, height_px


def create_pdf(png_path: Path, pdf_path: Path, paper: str, square_mm: float) -> tuple[float, float]:
    long_edge, short_edge = PAPER_SIZES[paper]
    page_width_mm, page_height_mm = long_edge, short_edge  # landscape page for the 10x7 board
    board_width_mm = SQUARES_X * square_mm
    board_height_mm = SQUARES_Y * square_mm
    x = (page_width_mm - board_width_mm) / 2.0
    y = (page_height_mm - board_height_mm) / 2.0
    document = canvas.Canvas(str(pdf_path), pagesize=(page_width_mm * mm, page_height_mm * mm), pageCompression=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        board_image = ImageReader(str(png_path))
    document.drawImage(board_image, x * mm, y * mm, width=board_width_mm * mm, height=board_height_mm * mm,
                       preserveAspectRatio=False, mask=None)
    document.showPage()
    document.save()
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise OSError(f"ReportLab could not write PDF: {pdf_path}")
    return page_width_mm, page_height_mm


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an additional ChArUco board at a chosen paper size")
    parser.add_argument("--paper", default="A3", choices=sorted(PAPER_SIZES), help="Paper size (default A3)")
    parser.add_argument("--square", type=float, default=None, help="Square size in mm (default: auto-fit the paper)")
    parser.add_argument("--marker", type=float, default=None, help="Marker size in mm (default: 11/15 of square)")
    parser.add_argument("--margin", type=float, default=10.0, help="Minimum page margin in mm")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    square_mm = args.square if args.square else fit_square_mm(args.paper, args.margin)
    marker_mm = args.marker if args.marker else round(square_mm * MARKER_RATIO, 1)
    if marker_mm >= square_mm:
        raise SystemExit("Marker size must be smaller than square size.")

    stem = f"charuco_board_{args.paper}_{int(square_mm)}mm"
    png_path = project_root / f"{stem}.png"
    pdf_path = project_root / f"{stem}.pdf"

    board = create_board(square_mm, marker_mm)
    width_px, height_px = render_png(board, square_mm, png_path)
    page_w_mm, page_h_mm = create_pdf(png_path, pdf_path, args.paper, square_mm)

    board_w_mm = SQUARES_X * square_mm
    board_h_mm = SQUARES_Y * square_mm
    effective_dpi = (width_px / SQUARES_X) / (square_mm / 25.4)

    print("\nChArUco board generated successfully")
    print("=" * 55)
    print(f"Dictionary           : {DICTIONARY_NAME}")
    print(f"Layout               : {SQUARES_X} x {SQUARES_Y} squares")
    print(f"Square size          : {square_mm:.0f} mm")
    print(f"Marker size          : {marker_mm:.1f} mm")
    print(f"Printed board size    : {board_w_mm:.0f} x {board_h_mm:.0f} mm")
    print(f"Paper                : {args.paper} landscape ({page_w_mm:.0f} x {page_h_mm:.0f} mm)")
    print(f"PNG resolution       : {width_px} x {height_px} px  ({effective_dpi:.0f} DPI)")
    print(f"PNG output           : {png_path.resolve()}")
    print(f"PDF output           : {pdf_path.resolve()}")
    print("\nPRINTING INSTRUCTIONS")
    print(f"- Print on {args.paper}, LANDSCAPE.")
    print("- Scaling: 100% / Actual size (NOT 'fit to page').")
    print("- Matte finish, mount flat on a rigid board (foam-core or ACP).")
    print(f"- Verify with a ruler: each square must measure exactly {square_mm:.0f} mm.")
    print(f"- Tell the calibrator this square size ({square_mm:.0f} mm) for pose/extrinsics.")


if __name__ == "__main__":
    main()
