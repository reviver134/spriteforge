"""Build SpriteForge's original 3x5 bitmap HUD alphabet (no external font dependency)."""

from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

from PIL import Image

GLYPHS = {
    " ": "000000000000000",
    "-": "000000111000000",
    "A": "010101111101101",
    "B": "110101110101110",
    "C": "011100100100011",
    "D": "110101101101110",
    "E": "111100110100111",
    "F": "111100110100100",
    "G": "011100101101011",
    "H": "101101111101101",
    "I": "111010010010111",
    "J": "001001001101010",
    "K": "101101110101101",
    "L": "100100100100111",
    "M": "101111111101101",
    "N": "101111111111101",
    "O": "010101101101010",
    "P": "110101110100100",
    "Q": "010101101111011",
    "R": "110101110101101",
    "S": "011100010001110",
    "T": "111010010010010",
    "U": "101101101101111",
    "V": "101101101101010",
    "W": "101101111111101",
    "X": "101101010101101",
    "Y": "101101010010010",
    "Z": "111001010100111",
    "0": "111101101101111",
    "1": "010110010010111",
    "2": "110001010100111",
    "3": "110001010001110",
    "4": "101101111001001",
    "5": "111100110001110",
    "6": "011100111101111",
    "7": "111001010010010",
    "8": "111101111101111",
    "9": "111101111001110",
}


def build():
    root = Path(__file__).resolve().parents[1] / "src/spriteforge/templates/phaser"
    image = Image.new("RGBA", (len(GLYPHS) * 4, 6))
    font = Element("font")
    SubElement(font, "info", face="SpriteForge Pixel", size="6")
    SubElement(
        font, "common", lineHeight="6", base="5", scaleW=str(image.width), scaleH="6", pages="1"
    )
    pages = SubElement(font, "pages")
    SubElement(pages, "page", id="0", file="font.png")
    chars = SubElement(font, "chars", count=str(len(GLYPHS)))
    for i, (char, pixels) in enumerate(GLYPHS.items()):
        for n, pixel in enumerate(pixels):
            if pixel == "1":
                image.putpixel((i * 4 + n % 3, n // 3), (255, 255, 255, 255))
        SubElement(
            chars,
            "char",
            id=str(ord(char)),
            x=str(i * 4),
            y="0",
            width="3",
            height="5",
            xoffset="0",
            yoffset="0",
            xadvance="4",
            page="0",
            chnl="15",
        )
    image.save(root / "font.png")
    (root / "font.xml").write_bytes(tostring(font))


if __name__ == "__main__":
    build()
