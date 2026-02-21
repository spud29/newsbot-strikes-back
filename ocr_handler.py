"""
OCR handler for extracting text from images using Tesseract
"""
import os
import re
import pytesseract
from PIL import Image
from utils import logger
import config

# Patterns that indicate TradingView chart OCR garbage
TRADINGVIEW_GARBAGE_PATTERNS = [
    r'TradingView',  # TradingView watermark
    r'(?i)binance|coinbase|kraken|bybit|okx|kucoin',  # Exchange names in chart context
    r'SELL\s*BUY|BUY\s*SELL',  # Order book buttons
    r'Vol[-\s]*[A-Z]{2,5}',  # Volume indicators like "Vol- ETH"
    r'\d{1,2}:\d{2}\s*\d{1,2}:\d{2}',  # Multiple time stamps (chart time axis)
    r'[A-Z]+\s*/\s*U\.?S\.?\s*Dollar',  # Trading pair format "ETH / U.S. Dollar"
    r'\b[OHLC]\s*[\d,]+\.\d+',  # OHLC data (Open, High, Low, Close)
]

# Minimum ratio of "garbage" patterns to total content that indicates OCR garbage
GARBAGE_PATTERN_THRESHOLD = 0.3

def is_tradingview_chart_ocr(text):
    """
    Detect if OCR text appears to be garbage from a TradingView chart image.
    
    These charts produce nonsensical text when OCR'd because they contain:
    - Price numbers scattered everywhere
    - Time axis labels
    - Trading pair names
    - Exchange branding
    - Button text (SELL, BUY)
    - Volume indicators
    
    Args:
        text: OCR extracted text
        
    Returns:
        bool: True if this appears to be TradingView chart garbage
    """
    if not text or len(text) < 50:
        return False
    
    text_lower = text.lower()
    
    # Quick check for TradingView watermark - strong indicator
    if 'tradingview' in text_lower:
        logger.info("TradingView chart OCR detected (watermark found)")
        return True
    
    # Count how many garbage patterns are present
    garbage_matches = 0
    for pattern in TRADINGVIEW_GARBAGE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            garbage_matches += 1
    
    # Check for high density of numbers (charts have lots of price/time numbers)
    # Count digit characters vs alphabetic characters
    digit_count = sum(1 for c in text if c.isdigit())
    alpha_count = sum(1 for c in text if c.isalpha())
    
    # Charts typically have very high number density
    number_ratio = digit_count / max(alpha_count, 1)
    
    # If multiple garbage patterns AND high number density, it's likely a chart
    if garbage_matches >= 3 and number_ratio > 0.5:
        logger.info(
            f"TradingView chart OCR detected: {garbage_matches} patterns matched, "
            f"number ratio {number_ratio:.2f}"
        )
        return True
    
    # Check for fragmented text (OCR from charts produces lots of short fragments)
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if lines:
        avg_line_length = sum(len(l) for l in lines) / len(lines)
        short_lines = sum(1 for l in lines if len(l) < 15)
        short_line_ratio = short_lines / len(lines)
        
        # Charts produce many short, disconnected text fragments
        if short_line_ratio > 0.7 and avg_line_length < 20 and garbage_matches >= 2:
            logger.info(
                f"TradingView chart OCR detected: high fragmentation "
                f"(avg line: {avg_line_length:.1f}, {short_line_ratio:.0%} short lines)"
            )
            return True
    
    return False

class OCRHandler:
    """Handles OCR text extraction from images"""
    
    def __init__(self):
        """Initialize OCR handler and configure Tesseract path"""
        # Set Tesseract executable path for Windows
        # Try standard Windows installation path first
        tesseract_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        
        # Check if custom path is specified in config
        if hasattr(config, 'TESSERACT_PATH') and config.TESSERACT_PATH:
            tesseract_paths.insert(0, config.TESSERACT_PATH)
        
        # Find the first existing Tesseract installation
        tesseract_found = False
        for path in tesseract_paths:
            if os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                tesseract_found = True
                logger.info(f"Tesseract OCR found at: {path}")
                break
        
        if not tesseract_found:
            logger.warning(
                f"Tesseract not found in standard locations. "
                f"OCR will be disabled unless Tesseract is in PATH or TESSERACT_PATH is set in config.py"
            )
        
        self.enabled = tesseract_found and getattr(config, 'OCR_ENABLED', True)
        self.language = getattr(config, 'OCR_LANGUAGE', 'eng')
        
        if self.enabled:
            logger.info(f"OCR Handler initialized (Language: {self.language})")
        else:
            logger.info("OCR Handler disabled")
    
    def extract_text_from_image(self, image_path):
        """
        Extract text from an image file using OCR
        
        Args:
            image_path: Path to the image file
        
        Returns:
            str: Extracted text, or empty string if extraction fails
        """
        if not self.enabled:
            return ""
        
        if not os.path.exists(image_path):
            logger.warning(f"Image file not found: {image_path}")
            return ""
        
        try:
            # Open the image
            image = Image.open(image_path)
            
            # Perform OCR
            text = pytesseract.image_to_string(image, lang=self.language)
            
            # Clean up the extracted text
            text = text.strip()
            
            if text:
                logger.debug(f"OCR extracted {len(text)} characters from {os.path.basename(image_path)}")
            else:
                logger.debug(f"No text found in {os.path.basename(image_path)}")
            
            return text
            
        except Exception as e:
            logger.error(f"Error extracting text from {image_path}: {e}")
            return ""
    
    def extract_text_from_images(self, image_paths):
        """
        Extract text from multiple image files
        
        Args:
            image_paths: List of paths to image files
        
        Returns:
            str: Combined extracted text from all images (empty if detected as chart garbage)
        """
        if not self.enabled:
            return ""
        
        if not image_paths:
            return ""
        
        extracted_texts = []
        
        for image_path in image_paths:
            text = self.extract_text_from_image(image_path)
            if text:
                extracted_texts.append(text)
        
        # Combine all extracted texts
        combined_text = "\n\n".join(extracted_texts)
        
        if combined_text:
            # Check if this is TradingView chart garbage
            if is_tradingview_chart_ocr(combined_text):
                logger.warning(
                    f"Rejecting OCR text as TradingView chart garbage "
                    f"({len(combined_text)} chars from {len(image_paths)} images)"
                )
                return ""
            
            logger.info(f"OCR extracted total of {len(combined_text)} characters from {len(image_paths)} images")
        
        return combined_text


