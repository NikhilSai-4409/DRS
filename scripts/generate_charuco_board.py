"""Generate the production ChArUco calibration target used by Cricket DRS.

The PNG is a board-only, high-resolution master. The PDF places that same
master at its exact physical size in the centre of an A0 page.
"""

from __future__ import annotations

from pathlib import Path
import warnings

import cv2
from PIL import Image
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader


# Keep every production dimension in one place so the printed target and the
# camera-calibration code cannot silently drift apart.
DICTIONARY_NAME = "DICT_5X5_1000"
SQUARES_X = 10
SQUARES_Y = 7
SQUARE_SIZE_MM = 75.0
MARKER_SIZE_MM = 55.0
A0_WIDTH_MM = 841.0
A0_HEIGHT_MM = 1189.0

# 1,440 pixels per square produces a 14,400 x 10,080 px board. It is divisible
# by 15, so the 55/75 = 11/15 marker-to-square ratio also becomes an exact
# integer 1,056 px marker with no raster rounding (about 488 effective DPI).
PIXELS_PER_SQUARE = 1440
PNG_FILENAME = "charuco_board_A0_75mm.png"
PDF_FILENAME = "charuco_board_A0_75mm.pdf"


def create_board() -> cv2.aruco.CharucoBoard:
    """Create the one canonical OpenCV ChArUco board used by the DRS system."""
    if not hasattr(cv2, "aruco"):
        raise RuntimeError(
            "OpenCV was installed without the ArUco module. "
            "Install 'opencv-contrib-python>=4.9'."
        )

    # Load the exact predefined marker family requested for the physical board.
    dictionary_id = getattr(cv2.aruco, DICTIONARY_NAME)
    dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)

    # OpenCV uses metres or millimetres consistently as long as both dimensions
    # use the same unit; millimetres are used here to match the print drawings.
    return cv2.aruco.CharucoBoard(
        (SQUARES_X, SQUARES_Y),
        SQUARE_SIZE_MM,
        MARKER_SIZE_MM,
        dictionary,
    )


def render_board_png(board: cv2.aruco.CharucoBoard, output_path: Path) -> tuple[int, int]:
    """Render and save the lossless, board-only PNG master."""
    width_px = SQUARES_X * PIXELS_PER_SQUARE
    height_px = SQUARES_Y * PIXELS_PER_SQUARE

    # A zero-pixel margin keeps the PNG's outer edges coincident with the exact
    # 750 x 525 mm physical board boundary. One marker border bit is standard.
    image = board.generateImage(
        (width_px, height_px),
        marginSize=0,
        borderBits=1,
    )

    # Use maximum lossless PNG compression. Compression changes file size only,
    # never marker geometry or pixel values.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = cv2.imwrite(
        str(output_path),
        image,
        [cv2.IMWRITE_PNG_COMPRESSION, 9],
    )
    if not written:
        raise OSError(f"OpenCV could not write PNG: {output_path}")

    # Re-open the file to catch truncation, wrong dimensions, or failed encoding
    # before a print-ready PDF is produced from it.
    saved = cv2.imread(str(output_path), cv2.IMREAD_GRAYSCALE)
    if saved is None or saved.shape != (height_px, width_px):
        raise RuntimeError("Saved PNG failed the resolution validation check")
    if max(saved.shape) < 10_000:
        raise RuntimeError("Saved PNG does not meet the 10,000 px requirement")

    return width_px, height_px


def create_a0_pdf(png_path: Path, pdf_path: Path) -> None:
    """Place the board at exactly 750 x 525 mm on a marginless A0 PDF page."""
    page_width = A0_WIDTH_MM * mm
    page_height = A0_HEIGHT_MM * mm
    board_width = (SQUARES_X * SQUARE_SIZE_MM) * mm
    board_height = (SQUARES_Y * SQUARE_SIZE_MM) * mm

    # These offsets centre the landscape board on the portrait A0 sheet. The PDF
    # page itself has no margins; the surrounding white area is part of the page.
    x = (page_width - board_width) / 2.0
    y = (page_height - board_height) / 2.0

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    document = canvas.Canvas(
        str(pdf_path),
        pagesize=(page_width, page_height),
        pageCompression=1,
    )

    # Disable aspect-ratio guessing and draw at explicit physical dimensions.
    # No text, crop marks, headers, footers, or other objects are added.
    # Pillow warns by default for images above its generic web-image threshold.
    # This 143 MP image was generated and dimension-checked locally, so suppress
    # only that expected warning while ReportLab reads this trusted print master.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        board_image = ImageReader(str(png_path))

    document.drawImage(
        board_image,
        x,
        y,
        width=board_width,
        height=board_height,
        preserveAspectRatio=False,
        mask=None,
    )
    document.showPage()
    document.save()

    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise OSError(f"ReportLab could not write PDF: {pdf_path}")


def print_specifications(width_px: int, height_px: int, png_path: Path, pdf_path: Path) -> None:
    """Print the exact target, validation values, and production instructions."""
    board_width_mm = SQUARES_X * SQUARE_SIZE_MM
    board_height_mm = SQUARES_Y * SQUARE_SIZE_MM
    board_area_mm2 = board_width_mm * board_height_mm
    a0_area_mm2 = A0_WIDTH_MM * A0_HEIGHT_MM
    effective_dpi = PIXELS_PER_SQUARE / (SQUARE_SIZE_MM / 25.4)

    print("\nCricket DRS ChArUco board generated successfully")
    print("=" * 55)
    print(f"Dictionary: {DICTIONARY_NAME}")
    print(f"Board dimensions: {SQUARES_X} squares x {SQUARES_Y} squares")
    print(f"Square size: {SQUARE_SIZE_MM:.0f} mm")
    print(f"Marker size: {MARKER_SIZE_MM:.0f} mm")
    print(f"Board physical width: {board_width_mm:.0f} mm")
    print(f"Board physical height: {board_height_mm:.0f} mm")
    print(f"Total printable board area: {board_area_mm2:,.0f} mm^2 ({board_area_mm2 / 1_000_000:.6f} m^2)")
    print(f"A0 page area: {a0_area_mm2:,.0f} mm^2 ({a0_area_mm2 / 1_000_000:.6f} m^2)")
    print(f"PNG resolution: {width_px} x {height_px} px")
    print(f"Effective board resolution: {effective_dpi:.2f} DPI")
    print(f"PNG output: {png_path.resolve()}")
    print(f"PDF output: {pdf_path.resolve()}")

    print("\nPRINTING AND MOUNTING INSTRUCTIONS")
    print("- Print on A0 size (841 x 1189 mm).")
    print("- No scaling.")
    print("- Select 100% size / Actual size in the print dialog.")
    print("- Use a matte finish to prevent reflections.")
    print("- Mount flat on 3-4 mm ACP (Aluminium Composite Panel).")
    print("- Verify several 75 mm squares with a ruler after printing.")


def main() -> None:
    """Generate both canonical calibration-board files beside the project root."""
    project_root = Path(__file__).resolve().parents[1]
    png_path = project_root / PNG_FILENAME
    pdf_path = project_root / PDF_FILENAME

    # Build once, then use the same board definition for every output format.
    board = create_board()
    width_px, height_px = render_board_png(board, png_path)
    create_a0_pdf(png_path, pdf_path)
    print_specifications(width_px, height_px, png_path, pdf_path)


if __name__ == "__main__":
    main()
